import re
import random
import discord
import asyncio
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS

class LFG(commands.Cog):
    """
    LFG command with role pings and cooldowns
    """

    LFG_ROLE_ID = 1358388775570637001
    LFG_CHANNEL_ID = 1284536580941287598  # legacy-lfg channel
    NEW_LFG_CHANNEL_ID = 1526690875470774312  # new-lfg channel
    TEST_ROLE_ID = 1270478869610238084
    TEST_CHANNEL_ID = 1269794533923754089 # log channel

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
