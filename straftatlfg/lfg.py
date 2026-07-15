import re
import random
import discord
import asyncio
import logging
import time
from typing import Dict, Literal, Optional, Tuple
from redbot.core import commands, Config, checks
from redbot.core import app_commands
from redbot.core.bot import Red
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS

log = logging.getLogger("red.straftatlfg")


class LFGPostModal(discord.ui.Modal, title="Post an LFG"):
    """The interactive form behind /lfg and /testlfg (new system only).

    Discord hard-caps modals at 5 top-level components, so the modal carries
    the mandatory fields (+ notes). The optional lobby settings arrive as
    slash-command options (already validated client-side by Discord) and are
    threaded through via optional_settings. Selects in modals must be wrapped
    in discord.ui.Label (dpy 2.6+ / Red 3.5.21+); a bare select gets rejected.
    """

    lobby_id_field = discord.ui.Label(
        text="Lobby ID",
        description="Numbers only, e.g. 12345",
        component=discord.ui.TextInput(placeholder="12345", max_length=10, required=True),
    )
    max_players_field = discord.ui.Label(
        text="Max Players",
        component=discord.ui.Select(
            placeholder="How many players can join?",
            options=[
                discord.SelectOption(label="2"),
                discord.SelectOption(label="3"),
                discord.SelectOption(label="4"),
            ],
            required=True,
        ),
    )
    gamemode_field = discord.ui.Label(
        text="Gamemode",
        component=discord.ui.Select(
            placeholder="FFA or Teams?",
            options=[
                discord.SelectOption(label="FFA"),
                discord.SelectOption(label="Teams"),
            ],
            required=True,
        ),
    )
    modded_field = discord.ui.Label(
        text="Modded Lobby",
        component=discord.ui.Select(
            placeholder="Is the lobby running mods?",
            options=[
                discord.SelectOption(label="Yes"),
                discord.SelectOption(label="No"),
            ],
            required=True,
        ),
    )
    notes_field = discord.ui.Label(
        text="Notes",
        description="Optional — casual? Competitive? Anything else!",
        component=discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            max_length=200,
            required=False,
        ),
    )

    def __init__(
        self,
        cog,
        destination_id: int,
        enforce_gate: bool,
        enforce_cooldown: bool,
        silent_ping: bool,
        optional_settings: Optional[Dict[str, Optional[str]]] = None,
    ):
        super().__init__()
        self.cog = cog
        self.destination_id = destination_id
        self.enforce_gate = enforce_gate
        self.enforce_cooldown = enforce_cooldown
        self.silent_ping = silent_ping
        # Display name -> value from the slash command's optional options;
        # None means the invoker skipped that option.
        self.optional_settings: Dict[str, Optional[str]] = optional_settings or {}
        # Set when this submission consumed the cooldown; cleared the moment the
        # post lands so a late failure can never refund a live post.
        self.committed_stamp: Optional[float] = None

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.handle_lfg_submit(interaction, self)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("LFGPostModal submission failed", exc_info=error)
        if self.committed_stamp is not None:
            try:
                await self.cog.refund_cooldown(interaction.user, self.committed_stamp)
            except Exception:
                log.exception("Cooldown refund failed during modal error handling")
        try:
            msg = "Something went wrong — try again."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


class LFG(commands.Cog):
    """
    LFG command with role pings and cooldowns
    """

    LFG_ROLE_ID = 1358388775570637001
    LFG_CHANNEL_ID = 1284536580941287598  # legacy-lfg channel
    NEW_LFG_CHANNEL_ID = 1526690875470774312  # new-lfg channel
    TEST_ROLE_ID = 1270478869610238084
    TEST_CHANNEL_ID = 1269794533923754089 # log channel
    LFG_COOLDOWN_SECONDS = 60  # /lfg cooldown window (new system; legacy keeps its decorator)
    REGION_CHANNEL_ID = None  # channel hosting the region reaction message; None -> gate message has no link

    # Region reaction roles, in display order: (emoji, label, role_id)
    REGION_ROLES = [
        ("1️⃣", "CIS / СНГ", 1518155782116605976),
        ("2️⃣", "EU", 1518157363960614992),
        ("3️⃣", "NA", 1518155772230369301),
        ("4️⃣", "LATAM", 1518155781772546108),
        ("5️⃣", "ASIA", 1518155783559319552),
        ("6️⃣", "OCE", 1518157364971180122),
        ("7️⃣", "AFRICA", 1518155772964376647),
        ("8️⃣", "ME", 1518157364694618215),
    ]

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2736452831, force_registration=True)
        default_guild = {
            "active_sticky_channels": [],
            "sticky_cache": {},  # channel_id (str): message_id (int)
            "faqs": {},
            "region_message_ids": []  # message IDs of posted region reaction-role embeds
        }
        self.config.register_guild(**default_guild)
        self.config.register_member(last_lfg_ts=0.0)
        # New-system state (slash commands). Legacy prefix commands never touch these.
        self._cooldown_cache: Dict[Tuple[int, int], float] = {}
        self._ping_toggle_last: Dict[int, float] = {}

    def _region_role_map(self):
        """Return a dict mapping region emoji -> role_id."""
        return {emoji: role_id for emoji, _label, role_id in self.REGION_ROLES}

    async def _handle_sticky(self, channel: discord.TextChannel):
        guild_config = self.config.guild(channel.guild)
        
        async with guild_config.sticky_cache() as cache:
            # Delete old message if it exists
            old_msg_id = cache.get(str(channel.id))
            if old_msg_id:
                try:
                    old_msg = await channel.fetch_message(old_msg_id)
                    await old_msg.delete()
                except Exception:
                    pass

            embed = discord.Embed(
                title="How to use the LFG system",
                description=(
                    "**Role Toggle**: You can use `!lfg-role` in <#1310689512615051345> to add/remove the role at any time!\n\n"
                    "To post an LFG message, use the following command: `!lfg <lobby_id> <notes>`\n"
                    "> **Lobby ID**: Must be numerical, no alphabetical characters.\n"
                    "> **Notes**: (Optional) Describe what you are looking for like casual or competitive matches!\n"
                ),
                color=discord.Color.blue()
            )
            
            try:
                new_msg = await channel.send(embed=embed)
                cache[str(channel.id)] = new_msg.id
            except Exception:
                async with guild_config.active_sticky_channels() as active:
                    if channel.id in active:
                        active.remove(channel.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        
        guild_config = self.config.guild(message.guild)
        active_channels = await guild_config.active_sticky_channels()
        
        if message.channel.id not in active_channels:
            return

        # Don't trigger on our own sticky message
        if message.author == self.bot.user:
            if message.embeds and message.embeds[0].title == "How to use the LFG system":
                return
            
        await self._handle_sticky(message.channel)

    async def _process_lfg(self, ctx: commands.Context, channel_id: int, lobby_id: str, notes: str = None):
        if ctx.channel.id != 1310689512615051345:  # straftchat
            ctx.command.reset_cooldown(ctx)
            return await ctx.send("This command can only be used in <#1310689512615051345>.", delete_after=10)

        if not lobby_id.isdigit():
            ctx.command.reset_cooldown(ctx)
            return await ctx.send("The Lobby ID must contain only numerical characters.", delete_after=10)

        # Sanitize notes: remove masked links and raw URLs
        if notes:
            notes = re.sub(r"\[([^\]]+)\]\(https?://[^\s\)]+\)", r"\1", notes)
            notes = re.sub(r"https?://[^\s]+", "", notes)

        role = ctx.guild.get_role(self.LFG_ROLE_ID)
        if not role:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send("LFG Role not found. Please contact an administrator.")

        title = "Euuuuuugh!" if random.randint(1, 1000) == 1 else "Looking For Group"
        color = discord.Color.green() if any(r.id == 1387554310832918528 for r in ctx.author.roles) else discord.Color.blue()
        
        embed = discord.Embed(
            title=title,
            color=color,
            description=notes
        )
        embed.add_field(name="Lobby ID", value=f"`{lobby_id}`", inline=True)
        embed.add_field(name="Host", value=ctx.author.mention, inline=True)
        embed.set_footer(text="Join the lobby using the ID above!", icon_url=ctx.author.display_avatar.url)

        content = f"{role.mention}"

        lfg_channel = ctx.guild.get_channel(channel_id)  # !lfg -> legacy-lfg, !testlfg -> log channel
        if not lfg_channel:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send("LFG channel not found. Please contact an administrator.")

        await lfg_channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=[role])
        )

        try:
            await ctx.message.add_reaction("✅")
        except discord.DiscordException:
            pass

    @commands.command()
    @commands.guild_only()
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def lfg(self, ctx: commands.Context, lobby_id: str, *, notes: str = None):
        """
        Post an LFG message.
        
        Syntax: [p]lfg <lobby_id> <notes>
        Lobby ID must be numerical.
        """
        await self._process_lfg(ctx, self.LFG_CHANNEL_ID, lobby_id, notes)

    @commands.command()
    @commands.guild_only()
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def testlfg(self, ctx: commands.Context, lobby_id: str, *, notes: str = None):
        """
        Test LFG command. 
        Only usable by specific test role.
        """
        if not any(role.id == self.TEST_ROLE_ID for role in ctx.author.roles):
            ctx.command.reset_cooldown(ctx)
            return await ctx.send("You do not have permission to use this test command.", delete_after=10)
        
        await self._process_lfg(ctx, self.TEST_CHANNEL_ID, lobby_id, notes)

    @commands.command(name="lfg-role")
    @commands.guild_only()
    async def lfg_role(self, ctx: commands.Context):
        """
        Toggle the LFG role.
        """
        role = ctx.guild.get_role(self.LFG_ROLE_ID)
        if not role:
            return await ctx.send("LFG Role not found. Please contact an administrator.")

        if role in ctx.author.roles:
            try:
                await ctx.author.remove_roles(role, reason="LFG role toggle")
                await ctx.send("Removed the LFG role.", delete_after=10)
                await ctx.message.add_reaction("❌")
            except discord.Forbidden:
                await ctx.send("I do not have permission to remove that role.")
        else:
            try:
                await ctx.author.add_roles(role, reason="LFG role toggle")
                await ctx.send("Added the LFG role.", delete_after=10)
                await ctx.message.add_reaction("✅")
            except discord.Forbidden:
                await ctx.send("I do not have permission to add that role.")

    @commands.command(name="sticky-toggle", aliases=["toggle-sticky"])
    @commands.guild_only()
    async def sticky_toggle(self, ctx: commands.Context):
        """
        Toggle the sticky info message in the current channel.
        """
        # Maintain existing permission logic but add admin as fallback
        has_test_role = any(role.id == self.TEST_ROLE_ID for role in ctx.author.roles)
        is_admin = ctx.author.guild_permissions.administrator
        
        if not (has_test_role or is_admin):
            return await ctx.send("You do not have permission to use this command.", delete_after=10)

        channel_id = ctx.channel.id
        guild_config = self.config.guild(ctx.guild)
        
        async with guild_config.active_sticky_channels() as active:
            if channel_id in active:
                # Disable sticky
                active.remove(channel_id)
                
                # Cleanup last message
                async with guild_config.sticky_cache() as cache:
                    old_msg_id = cache.pop(str(channel_id), None)
                    if old_msg_id:
                        try:
                            old_msg = await ctx.channel.fetch_message(old_msg_id)
                            await old_msg.delete()
                        except Exception:
                            pass
                
                await ctx.send("Sticky message disabled in this channel.", delete_after=10)
            else:
                # Enable sticky
                active.append(channel_id)
                await self._handle_sticky(ctx.channel)
                await ctx.send(f"Sticky message enabled in {ctx.channel.mention}.", delete_after=10)

    @commands.command()
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def faq(self, ctx: commands.Context, *, name: str):
        """
        Get an FAQ response.
        """
        if ctx.channel.id in [1409306775445831751, 1286747739270545500]:
            ctx.command.reset_cooldown(ctx)
            return
        guild = ctx.guild
        if not guild:
            channel = self.bot.get_channel(self.LFG_CHANNEL_ID)
            if channel:
                guild = channel.guild
            if not guild:
                ctx.command.reset_cooldown(ctx)
                return await ctx.send("Could not determine the server. Please run this command in the server.")

        faqs = await self.config.guild(guild).faqs()
        name = name.lower()
        if name in faqs:
            await ctx.send(faqs[name], delete_after=86400)
        else:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(f"FAQ `{name}` not found.", delete_after=10)

    @commands.command(name="faqnew", aliases=["addfaq"])
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def faqnew(self, ctx: commands.Context, name: str, *, content: str):
        """
        Create a new FAQ response.
        
        Example:
        [p]faqnew test
        ```
        # What is this command?
        This is a test.
        ```
        """
        if ctx.channel.id in [1409306775445831751, 1286747739270545500]:
            return
        match = re.search(r"```[a-zA-Z]*\n?(.*?)```", content, re.DOTALL)
        if match:
            faq_content = match.group(1).strip()
        else:
            faq_content = content.strip()
            
        async with self.config.guild(ctx.guild).faqs() as faqs:
            faqs[name.lower()] = faq_content
            
        await ctx.send(f"FAQ `{name}` added successfully.", delete_after=10)

    @commands.command(name="faqdel", aliases=["delfaq"])
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def faqdel(self, ctx: commands.Context, *, name: str):
        """
        Delete an FAQ response.
        """
        if ctx.channel.id in [1409306775445831751, 1286747739270545500]:
            return
        async with self.config.guild(ctx.guild).faqs() as faqs:
            name = name.lower()
            if name in faqs:
                del faqs[name]
                await ctx.send(f"FAQ `{name}` deleted.", delete_after=10)
            else:
                await ctx.send(f"FAQ `{name}` not found.", delete_after=10)
                
    @commands.command(name="faqlist", aliases=["faqs"])
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def faqlist(self, ctx: commands.Context):
        """
        List all available FAQs and their contents.
        """
        if ctx.channel.id in [1409306775445831751, 1286747739270545500]:
            ctx.command.reset_cooldown(ctx)
            return
        guild = ctx.guild
        if not guild:
            channel = self.bot.get_channel(self.LFG_CHANNEL_ID)
            if channel:
                guild = channel.guild
            if not guild:
                return await ctx.send("Could not determine the server. Please run this command in the server.")
                
        faqs = await self.config.guild(guild).faqs()
        if not faqs:
            return await ctx.send("No FAQs have been set up yet.", delete_after=10)
            
        embeds = []
        current_embed = discord.Embed(
            title="Available FAQs",
            description="Here are the currently available FAQs and their responses:",
            color=discord.Color.blue()
        )
        current_field_count = 0
        current_char_count = len(current_embed.title) + len(current_embed.description)
        
        for name, content in faqs.items():
            # Discord embed field values are limited to 1024 characters
            display_content = content if len(content) <= 1000 else content[:1000] + "..."
            field_name = f"`{name}`"
            field_len = len(field_name) + len(display_content)
            
            # Check limits (max 10 fields per page or ~5500 chars to be safe)
            if current_field_count >= 10 or current_char_count + field_len > 5500:
                embeds.append(current_embed)
                current_embed = discord.Embed(
                    title="Available FAQs (Cont.)",
                    color=discord.Color.blue()
                )
                current_field_count = 0
                current_char_count = len(current_embed.title)
                
            current_embed.add_field(name=field_name, value=display_content, inline=False)
            current_field_count += 1
            current_char_count += field_len
            
        if current_field_count > 0:
            embeds.append(current_embed)
            
        if len(embeds) > 1:
            for i, emb in enumerate(embeds):
                emb.set_footer(text=f"Page {i+1} of {len(embeds)}")
            
        try:
            msg = await ctx.author.send(embed=embeds[0])
            if ctx.guild:
                try:
                    await ctx.message.add_reaction("✅")
                except discord.DiscordException:
                    pass
            
            if len(embeds) > 1:
                await menu(ctx, embeds, DEFAULT_CONTROLS, message=msg)
                
        except discord.Forbidden:
            await ctx.send(
                f"{ctx.author.mention}, I couldn't send you a DM with the FAQ list. "
                "Please check your privacy settings and ensure DMs from server members are enabled.",
                delete_after=15
            )

    @commands.command(name="faqhelp")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def faqhelp(self, ctx: commands.Context):
        """
        Shows a comprehensive list of FAQ commands available.
        """
        if ctx.channel.id in [1409306775445831751, 1286747739270545500]:
            ctx.command.reset_cooldown(ctx)
            return
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="FAQ System Help",
            description="Here are all the available FAQ commands:",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="User Commands",
            value=(
                f"`{prefix}faq <name>`\nRetrieves and outputs an FAQ response.\n\n"
                f"`{prefix}faqlist`\nLists all currently available FAQs."
            ),
            inline=False
        )
        
        embed.add_field(
            name="Admin Commands",
            value=(
                f"`{prefix}faqnew <name> <content>`\nCreates a new FAQ response. You can use markdown codeblocks to ensure headings and other formatting are preserved!\n\n"
                f"`{prefix}faqdel <name>`\nDeletes an existing FAQ response."
            ),
            inline=False
        )
        
        try:
            await ctx.author.send(embed=embed)
            try:
                await ctx.message.add_reaction("✅")
            except discord.DiscordException:
                pass
        except discord.Forbidden:
            await ctx.send(
                f"{ctx.author.mention}, I couldn't send you a DM with the help menu. "
                "Please check your privacy settings and ensure DMs from server members are enabled.",
                delete_after=15
            )

    @commands.command(name="lfg-region")
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def lfg_region(self, ctx: commands.Context):
        """
        Post the interactive region reaction-role message.

        Users react to assign themselves a region role for matchmaking.
        Only one region can be selected at a time.
        """
        lines = [
            "React below to assign yourself a **region role** so others can find players near them.\n",
            "> You can only have **one** region at a time. Picking a new one replaces the old.",
            "> Remove your reaction to clear your region.\n",
        ]
        lines.extend(f"{emoji} <@&{role_id}>" for emoji, _label, role_id in self.REGION_ROLES)

        embed = discord.Embed(
            title="Select Your Region",
            description="\n".join(lines),
            color=discord.Color.blue()
        )

        message = await ctx.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none()
        )

        for emoji, _label, _role_id in self.REGION_ROLES:
            try:
                await message.add_reaction(emoji)
            except discord.DiscordException:
                pass

        async with self.config.guild(ctx.guild).region_message_ids() as message_ids:
            message_ids.append(message.id)

        try:
            await ctx.message.add_reaction("✅")
        except discord.DiscordException:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return

        role_map = self._region_role_map()
        emoji = str(payload.emoji)
        if emoji not in role_map:
            return

        message_ids = await self.config.guild_from_id(payload.guild_id).region_message_ids()
        if payload.message_id not in message_ids:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        member = payload.member or guild.get_member(payload.user_id)
        if member is None or member.bot:
            return

        # Add the selected region role.
        role = guild.get_role(role_map[emoji])
        if role is not None and role not in member.roles:
            try:
                await member.add_roles(role, reason="LFG region reaction role")
            except discord.Forbidden:
                pass

        # Enforce single selection: strip every other region role...
        other_roles = [
            guild.get_role(role_id)
            for other_emoji, _label, role_id in self.REGION_ROLES
            if other_emoji != emoji
        ]
        other_roles = [r for r in other_roles if r is not None and r in member.roles]
        if other_roles:
            try:
                await member.remove_roles(*other_roles, reason="LFG region reaction role (single selection)")
            except discord.Forbidden:
                pass

        # ...and clear this member's other region reactions from the message.
        channel = guild.get_channel(payload.channel_id)
        if channel is not None:
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.DiscordException:
                message = None
            if message is not None:
                for other_emoji, _label, _role_id in self.REGION_ROLES:
                    if other_emoji != emoji:
                        try:
                            await message.remove_reaction(other_emoji, member)
                        except discord.DiscordException:
                            pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return

        role_map = self._region_role_map()
        emoji = str(payload.emoji)
        if emoji not in role_map:
            return

        message_ids = await self.config.guild_from_id(payload.guild_id).region_message_ids()
        if payload.message_id not in message_ids:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return

        role = guild.get_role(role_map[emoji])
        if role is not None and role in member.roles:
            try:
                await member.remove_roles(role, reason="LFG region reaction role removed")
            except discord.Forbidden:
                pass

    @lfg.error
    @testlfg.error
    @lfg_role.error
    @sticky_toggle.error
    @faq.error
    @faqlist.error
    @faqhelp.error
    async def lfg_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"You are on cooldown. Try again in {error.retry_after:.0f} seconds.", delete_after=10)
        elif isinstance(error, commands.MissingRequiredArgument):
            ctx.command.reset_cooldown(ctx)
            await ctx.send(f"Incorrect syntax. Use `{ctx.prefix}lfg <lobby_id> <notes>`", delete_after=10)
        else:
            ctx.command.reset_cooldown(ctx)
            # Log other errors
            raise error

    # ------------------------------------------------------------------
    # New system (slash commands). Purely additive — the legacy prefix
    # commands above are frozen and share no code with this block.
    # ------------------------------------------------------------------

    @staticmethod
    def sanitize_notes(notes: Optional[str]) -> Optional[str]:
        """Same sanitization as legacy _process_lfg: unwrap masked links, strip raw URLs."""
        if notes:
            notes = re.sub(r"\[([^\]]+)\]\(https?://[^\s\)]+\)", r"\1", notes)
            notes = re.sub(r"https?://[^\s]+", "", notes)
        return notes

    def get_member_region(self, member: discord.Member) -> Optional[Tuple[str, str, int]]:
        """First REGION_ROLES entry (emoji, label, role_id) the member holds, else None."""
        member_role_ids = {r.id for r in member.roles}
        return next((rg for rg in self.REGION_ROLES if rg[2] in member_role_ids), None)

    def _region_gate_message(self) -> str:
        if self.REGION_CHANNEL_ID:
            return f"You need a region role to post — pick one in <#{self.REGION_CHANNEL_ID}> first."
        return "You need a region role to post — ask an admin where to pick one."

    def build_lfg_embed(
        self,
        member: discord.Member,
        lobby_id: str,
        notes: Optional[str],
        region: Optional[Tuple[str, str, int]] = None,
        settings: Optional[Dict[str, str]] = None,
    ) -> discord.Embed:
        """Same look as the legacy embed, plus Region and lobby-settings fields."""
        title = "Euuuuuugh!" if random.randint(1, 1000) == 1 else "Looking For Group"
        color = discord.Color.green() if any(r.id == 1387554310832918528 for r in member.roles) else discord.Color.blue()
        embed = discord.Embed(title=title, color=color, description=notes)
        embed.add_field(name="Lobby ID", value=f"`{lobby_id}`", inline=True)
        embed.add_field(name="Host", value=member.mention, inline=True)
        if region is not None:
            embed.add_field(name="Region", value=f"{region[0]} {region[1]}", inline=True)
        for name, value in (settings or {}).items():
            embed.add_field(name=name, value=value, inline=True)
        embed.set_footer(text="Join the lobby using the ID above!", icon_url=member.display_avatar.url)
        return embed

    async def check_cooldown(self, member: discord.Member) -> float:
        """Remaining cooldown seconds. Never writes a stamp; warms the cache from Config."""
        key = (member.guild.id, member.id)
        if key not in self._cooldown_cache:
            persisted = await self.config.member(member).last_lfg_ts()
            # setdefault, never assignment: a concurrent commit that stamped the
            # cache while we awaited Config must not be clobbered by a stale read.
            self._cooldown_cache.setdefault(key, persisted)
        remaining = self.LFG_COOLDOWN_SECONDS - (time.time() - self._cooldown_cache[key])
        return max(0.0, remaining)

    def commit_cooldown_sync(self, member: discord.Member) -> Optional[float]:
        """Atomic check-and-stamp of the in-memory cache. Valid only after check_cooldown
        has warmed the cache for this member since cog load. No await between check and
        stamp — two racing submissions cannot both pass."""
        key = (member.guild.id, member.id)
        now = time.time()
        if now - self._cooldown_cache.get(key, 0.0) < self.LFG_COOLDOWN_SECONDS:
            return None
        self._cooldown_cache[key] = now
        return now

    async def refund_cooldown(self, member: discord.Member, stamp: float) -> None:
        """Compare-and-clear: only refunds the exact committed stamp, so a stale
        failure can never erase a newer legitimate stamp. Cache-only: a refunded
        stamp was never persisted (the Config write happens only after a
        successful send) and any previously persisted stamp is already expired,
        so no Config write is needed here."""
        key = (member.guild.id, member.id)
        if self._cooldown_cache.get(key) == stamp:
            del self._cooldown_cache[key]

    async def handle_lfg_submit(self, interaction: discord.Interaction, modal: LFGPostModal):
        """The ordering here is load-bearing — see REFACTOR_PLAN.md §4.4."""
        member = interaction.user

        # 1. Validate lobby id (server-side; Discord has no numeric input type)
        #    and read the mandatory selects (Discord enforces required=True, so
        #    exactly one value each; guard defensively anyway).
        lobby = str(modal.lobby_id_field.component.value or "").strip()
        if not lobby.isdigit():
            return await interaction.response.send_message(
                "The Lobby ID must contain only numbers.", ephemeral=True
            )
        try:
            max_players = modal.max_players_field.component.values[0]
            gamemode = modal.gamemode_field.component.values[0]
            modded = modal.modded_field.component.values[0]
        except IndexError:
            return await interaction.response.send_message(
                "A required selection is missing — please try again.", ephemeral=True
            )

        # 2. Authoritative region re-check (the modal may have sat open across a
        #    role removal). A failed gate never consumes the cooldown.
        region = self.get_member_region(member)
        if modal.enforce_gate and region is None:
            return await interaction.response.send_message(self._region_gate_message(), ephemeral=True)

        # 3. Sanitize notes; assemble the settings fields (mandatory first, then
        #    whichever optional slash options the invoker actually set).
        notes = self.sanitize_notes(modal.notes_field.component.value or None)
        settings: Dict[str, str] = {
            "Max Players": max_players,
            "Gamemode": gamemode,
            "Modded Lobby": modded,
        }
        for name, value in modal.optional_settings.items():
            if value is not None:
                settings[name] = value

        # 4-5. Cooldown: warm the cache from Config, then atomic check-and-stamp.
        if modal.enforce_cooldown:
            await self.check_cooldown(member)
            stamp = self.commit_cooldown_sync(member)
            if stamp is None:
                remaining = await self.check_cooldown(member)
                return await interaction.response.send_message(
                    f"You can post again in {max(1, round(remaining))}s.", ephemeral=True
                )
            modal.committed_stamp = stamp

        # 6. Beat the 3s interaction token deadline before any network sends.
        await interaction.response.defer(ephemeral=True, thinking=True)

        # 7. Resolve destination + role, build, send. Failures before the post
        #    lands refund the cooldown; failures after it never do.
        role = interaction.guild.get_role(self.LFG_ROLE_ID)
        channel = interaction.guild.get_channel(modal.destination_id)
        if role is None or channel is None:
            if modal.committed_stamp is not None:
                await self.refund_cooldown(member, modal.committed_stamp)
                modal.committed_stamp = None
            return await interaction.followup.send(
                "The LFG role or channel is not configured — please contact an administrator.",
                ephemeral=True,
            )

        embed = self.build_lfg_embed(member, lobby, notes, region=region, settings=settings)
        if modal.silent_ping:
            # /testlfg: render the mention without notifying anyone.
            mentions = discord.AllowedMentions.none()
        else:
            mentions = discord.AllowedMentions(roles=[role])

        try:
            message = await channel.send(content=role.mention, embed=embed, allowed_mentions=mentions)
        except discord.HTTPException:
            log.exception("Failed to send LFG post to channel %s", modal.destination_id)
            if modal.committed_stamp is not None:
                await self.refund_cooldown(member, modal.committed_stamp)
                modal.committed_stamp = None
            return await interaction.followup.send(
                "I couldn't post to the LFG channel — please contact an administrator.",
                ephemeral=True,
            )

        # The post is live: nothing beyond this point may refund the cooldown.
        committed = modal.committed_stamp
        modal.committed_stamp = None

        # 8. Persist the stamp; a failure here logs and skips the refund (worst
        #    case the in-memory cooldown holds for the session).
        if committed is not None:
            try:
                await self.config.member(member).last_lfg_ts.set(committed)
            except Exception:
                log.exception("Failed to persist LFG cooldown stamp")

        # 9. Ephemeral confirmation with jump link + copyable lobby id.
        try:
            await interaction.followup.send(
                f"Posted! [Jump to your LFG]({message.jump_url}) — Lobby ID: `{lobby}`",
                ephemeral=True,
            )
        except discord.HTTPException:
            log.exception("Failed to send LFG confirmation followup")

    @staticmethod
    def _collect_optional_settings(
        first_to: Optional[int],
        lobby_type: Optional[str],
        friendly_fire: Optional[str],
        mid_match_joining: Optional[str],
        weapon_randomizer: Optional[str],
        enemy_outlines: Optional[str],
    ) -> Dict[str, Optional[str]]:
        """Map the slash command's optional options to embed display names."""
        return {
            "First To": str(first_to) if first_to is not None else None,
            "Lobby Type": lobby_type,
            "Friendly Fire": friendly_fire,
            "Mid-Match Joining": mid_match_joining,
            "Weapon Randomizer": weapon_randomizer,
            "Enemy Outlines": enemy_outlines,
        }

    @app_commands.command(name="lfg", description="Post an LFG to #new-lfg")
    @app_commands.guild_only()
    @app_commands.describe(
        first_to="First to how many wins? (1-50)",
        lobby_type="Who can join the lobby",
        friendly_fire="Friendly fire setting",
        mid_match_joining="Allow joining mid-match",
        weapon_randomizer="Weapon randomizer mode",
        enemy_outlines="Enemy outlines setting",
    )
    async def slash_lfg(
        self,
        interaction: discord.Interaction,
        first_to: Optional[app_commands.Range[int, 1, 50]] = None,
        lobby_type: Optional[Literal["Public", "Invite Only", "Private"]] = None,
        friendly_fire: Optional[Literal["Enabled", "Disabled"]] = None,
        mid_match_joining: Optional[Literal["Yes", "No"]] = None,
        weapon_randomizer: Optional[Literal["Fully Random", "Custom", "No"]] = None,
        enemy_outlines: Optional[Literal["Enabled", "Disabled"]] = None,
    ):
        """Region gate -> cooldown peek -> modal. All feedback is ephemeral.
        The optional lobby settings live here as slash options (Discord enforces
        the choices and the 1-50 range client-side); the modal carries the
        mandatory fields, since modals cap at 5 components."""
        if self.get_member_region(interaction.user) is None:
            return await interaction.response.send_message(self._region_gate_message(), ephemeral=True)
        remaining = await self.check_cooldown(interaction.user)
        if remaining > 0:
            return await interaction.response.send_message(
                f"You can post again in {max(1, round(remaining))}s.", ephemeral=True
            )
        await interaction.response.send_modal(
            LFGPostModal(
                self,
                destination_id=self.NEW_LFG_CHANNEL_ID,
                enforce_gate=True,
                enforce_cooldown=True,
                silent_ping=False,
                optional_settings=self._collect_optional_settings(
                    first_to, lobby_type, friendly_fire,
                    mid_match_joining, weapon_randomizer, enemy_outlines,
                ),
            )
        )

    @app_commands.command(name="testlfg", description="Admin test of the LFG flow — posts to the log channel")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        first_to="First to how many wins? (1-50)",
        lobby_type="Who can join the lobby",
        friendly_fire="Friendly fire setting",
        mid_match_joining="Allow joining mid-match",
        weapon_randomizer="Weapon randomizer mode",
        enemy_outlines="Enemy outlines setting",
    )
    async def slash_testlfg(
        self,
        interaction: discord.Interaction,
        first_to: Optional[app_commands.Range[int, 1, 50]] = None,
        lobby_type: Optional[Literal["Public", "Invite Only", "Private"]] = None,
        friendly_fire: Optional[Literal["Enabled", "Disabled"]] = None,
        mid_match_joining: Optional[Literal["Yes", "No"]] = None,
        weapon_randomizer: Optional[Literal["Fully Random", "Custom", "No"]] = None,
        enemy_outlines: Optional[Literal["Enabled", "Disabled"]] = None,
    ):
        """Same modal and options as /lfg, but: admin-only, posts to the log
        channel, no region gate, no cooldown, and the role mention renders
        without notifying anyone."""
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "You need the Administrator permission to use this command.", ephemeral=True
            )
        await interaction.response.send_modal(
            LFGPostModal(
                self,
                destination_id=self.TEST_CHANNEL_ID,
                enforce_gate=False,
                enforce_cooldown=False,
                silent_ping=True,
                optional_settings=self._collect_optional_settings(
                    first_to, lobby_type, friendly_fire,
                    mid_match_joining, weapon_randomizer, enemy_outlines,
                ),
            )
        )

    @app_commands.command(name="lfgpings", description="Toggle whether you get pinged for LFG posts")
    @app_commands.guild_only()
    async def slash_lfgpings(self, interaction: discord.Interaction):
        now = time.time()
        if now - self._ping_toggle_last.get(interaction.user.id, 0.0) < 5:
            return await interaction.response.send_message(
                "Slow down — try again in a moment.", ephemeral=True
            )
        self._ping_toggle_last[interaction.user.id] = now
        await interaction.response.defer(ephemeral=True)
        role = interaction.guild.get_role(self.LFG_ROLE_ID)
        if role is None:
            return await interaction.followup.send(
                "LFG Role not found. Please contact an administrator.", ephemeral=True
            )
        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason="LFG ping toggle (/lfgpings)")
                await interaction.followup.send("You will no longer be pinged for LFG posts.", ephemeral=True)
            else:
                await interaction.user.add_roles(role, reason="LFG ping toggle (/lfgpings)")
                await interaction.followup.send("You will now be pinged for LFG posts.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to manage that role.", ephemeral=True
            )

    # No cog_app_command_error handler: Red's RedTree.on_error already logs
    # unexpected app-command exceptions and replies ephemerally — a cog handler
    # here would duplicate both. Modal errors are handled by LFGPostModal.on_error.

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        """Purge the stored cooldown timestamps (the only end-user data this cog keeps)."""
        all_members = await self.config.all_members()
        for guild_id, members in all_members.items():
            if user_id in members:
                await self.config.member_from_ids(guild_id, user_id).clear()
        self._cooldown_cache = {k: v for k, v in self._cooldown_cache.items() if k[1] != user_id}
        self._ping_toggle_last.pop(user_id, None)
