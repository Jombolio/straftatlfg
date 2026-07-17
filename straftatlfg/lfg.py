import re
import random
import discord
import asyncio
import logging
import time
from typing import Dict, Optional, Tuple
from redbot.core import commands, Config, checks
from redbot.core import app_commands
from redbot.core.bot import Red
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS

log = logging.getLogger("red.straftatlfg")

# Lobby ID bounds: enforced client-side by the modal field and re-checked
# server-side after whitespace stripping (new system only; legacy !lfg is frozen).
LOBBY_ID_MIN_LENGTH = 8
LOBBY_ID_MAX_LENGTH = 20


class LFGPostModal(discord.ui.Modal, title="Post an LFG"):
    """Stage one of the LFG flow: /lfg, /lfgmod, the sticky quick-post
    buttons, and the /testlfg + /testlfgmod testers.

    Discord hard-caps modals at 5 top-level components, so the modal carries
    the mandatory fields (Lobby ID, Max Players, Gamemode), the optional
    Weapon Randomizer select, and Notes. Submitting stashes a draft on an
    ephemeral panel offering Post Now or an Optional Settings modal with the
    remaining five settings (Discord cannot open a modal from a modal
    submission, hence the button bridge). Moddedness is derived from the
    invoking command (is_modded), never asked in the form. Selects in modals
    must be wrapped in discord.ui.Label (dpy 2.6+ / Red 3.5.21+); a bare
    select gets rejected.
    """

    lobby_id_field = discord.ui.Label(
        text="Lobby ID",
        description=f"Numbers only, {LOBBY_ID_MIN_LENGTH} to {LOBBY_ID_MAX_LENGTH} digits",
        component=discord.ui.TextInput(
            placeholder="12345678",
            min_length=LOBBY_ID_MIN_LENGTH,
            max_length=LOBBY_ID_MAX_LENGTH,
            required=True,
        ),
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
    randomizer_field = discord.ui.Label(
        text="Weapon Randomizer",
        description="Optional. Leave empty to skip.",
        component=discord.ui.Select(
            placeholder="Fully Random, Custom, or No?",
            options=[
                discord.SelectOption(label="Fully Random"),
                discord.SelectOption(label="Custom"),
                discord.SelectOption(label="No"),
            ],
            required=False,
        ),
    )
    notes_field = discord.ui.Label(
        text="Notes",
        description="Optional. Casual? Competitive? Anything else!",
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
        is_modded: bool,
        enforce_gate: bool,
        enforce_cooldown: bool,
        silent_ping: bool,
        optional_settings: Optional[Dict[str, Optional[str]]] = None,
    ):
        # Moddedness is derived from the invoking command (/lfgmod vs /lfg),
        # never asked in the form; the title tells the invoker which one opened.
        super().__init__(title="Post a Modded LFG" if is_modded else "Post an LFG")
        self.cog = cog
        self.destination_id = destination_id
        self.is_modded = is_modded
        if is_modded:
            # Class-level Labels are deep-copied per modal instance, so this
            # only rewords THIS modal's Notes field, not the vanilla one.
            self.notes_field.description = "Optional, but please list the mods your lobby is running!"
            self.notes_field.component.placeholder = "Which mods are you using? Anything else to note?"
        self.enforce_gate = enforce_gate
        self.enforce_cooldown = enforce_cooldown
        self.silent_ping = silent_ping
        # Display name -> value placeholders for the optional settings; stage
        # two fills a copy at post time (stable display order). Stage one never
        # commits the cooldown, so there is nothing here to refund on error.
        self.optional_settings: Dict[str, Optional[str]] = optional_settings or {}

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.handle_lfg_stage_one(interaction, self)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("LFGPostModal submission failed", exc_info=error)
        try:
            msg = "Something went wrong. Please try again."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


class LFGOptionalSettingsModal(discord.ui.Modal, title="Optional Lobby Settings"):
    """Stage two of the LFG flow (all posting surfaces): the five optional
    lobby settings. Submitting posts the draft immediately."""

    first_to_field = discord.ui.Label(
        text="First To",
        description="Optional. First to how many wins? 1 to 50.",
        component=discord.ui.TextInput(placeholder="10", max_length=2, required=False),
    )
    lobby_type_field = discord.ui.Label(
        text="Lobby Type",
        component=discord.ui.Select(
            placeholder="Who can join the lobby?",
            options=[
                discord.SelectOption(label="Public"),
                discord.SelectOption(label="Invite Only"),
                discord.SelectOption(label="Private"),
            ],
            required=False,
        ),
    )
    friendly_fire_field = discord.ui.Label(
        text="Friendly Fire",
        component=discord.ui.Select(
            placeholder="Friendly fire setting?",
            options=[
                discord.SelectOption(label="Enabled"),
                discord.SelectOption(label="Disabled"),
            ],
            required=False,
        ),
    )
    mid_match_field = discord.ui.Label(
        text="Mid-Match Joining",
        component=discord.ui.Select(
            placeholder="Allow joining mid-match?",
            options=[
                discord.SelectOption(label="Yes"),
                discord.SelectOption(label="No"),
            ],
            required=False,
        ),
    )
    outlines_field = discord.ui.Label(
        text="Enemy Outlines",
        component=discord.ui.Select(
            placeholder="Show enemy outlines?",
            options=[
                discord.SelectOption(label="Enabled"),
                discord.SelectOption(label="Disabled"),
            ],
            required=False,
        ),
    )

    def __init__(self, cog, panel_view):
        super().__init__()
        self.cog = cog
        self.panel_view = panel_view

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.handle_lfg_stage_two(interaction, self)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("LFGOptionalSettingsModal submission failed", exc_info=error)
        try:
            msg = "Something went wrong. Please try again."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


class LFGDraftPanelView(discord.ui.View):
    """The ephemeral bridge between the two LFG modals (all posting surfaces).

    Discord cannot open a modal in response to a modal submission, so stage
    one's submit lands here: an ephemeral panel whose buttons either post the
    draft immediately or open the optional-settings modal (button clicks are
    component interactions, which CAN open modals). The draft lives on this
    view instance only; a bot restart discards it and the admin re-runs the
    command. Ephemeral messages are only visible and interactable to the
    invoker, so no extra ownership check is needed.
    """

    def __init__(self, cog, draft: Dict[str, object]):
        super().__init__(timeout=300)
        self.cog = cog
        self.draft = draft
        self.message = None  # set after the panel is sent; used by on_timeout
        # Double-post guards, checked-and-set synchronously (no await between)
        # in finalize_chained_post: a double-clicked Post Now, or Post Now plus
        # a still-open stage-two modal, must produce exactly one post.
        self.finalizing = False
        self.posted = False

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    content="This draft expired. Run the command again to post.", view=self
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Post Now", style=discord.ButtonStyle.success)
    async def post_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.finalize_chained_post(interaction, self, from_panel=True)

    @discord.ui.button(label="Optional Settings", style=discord.ButtonStyle.secondary)
    async def optional_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Do not let the user type into a modal that finalize would refuse.
        if self.posted:
            return await interaction.response.send_message(
                "This draft was already posted.", ephemeral=True
            )
        if self.finalizing:
            return await interaction.response.send_message(
                "This draft is already being posted.", ephemeral=True
            )
        await interaction.response.send_modal(LFGOptionalSettingsModal(self.cog, self))

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        log.exception("LFGDraftPanelView interaction failed", exc_info=error)
        try:
            msg = "Something went wrong. Please try again."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


class LFGStickyView(discord.ui.View):
    """Persistent quick-post buttons on the new-system sticky message.

    timeout=None + stable custom_ids, registered once per cog load via
    bot.add_view, so one registration serves every sticky in every channel
    and the buttons keep working across restarts with zero per-message
    bookkeeping. The buttons open the exact same two-step flow as /lfg and
    /lfgmod: same region gate, same shared cooldown, same Post Now and
    Optional Settings panel.
    """

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="LFG",
        style=discord.ButtonStyle.success,
        custom_id="straftatlfg:v1:sticky_lfg",
    )
    async def sticky_lfg(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_sticky_button(interaction, is_modded=False)

    @discord.ui.button(
        label="Modded LFG",
        style=discord.ButtonStyle.primary,
        custom_id="straftatlfg:v1:sticky_lfgmod",
    )
    async def sticky_lfgmod(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_sticky_button(interaction, is_modded=True)


class LFG(commands.Cog):
    """
    LFG command with role pings and cooldowns
    """

    LFG_ROLE_ID = 1358388775570637001
    LFG_CHANNEL_ID = 1284536580941287598  # legacy-lfg channel
    NEW_LFG_CHANNEL_ID = 1526690875470774312  # new-lfg channel
    TEST_ROLE_ID = 1270478869610238084
    TEST_CHANNEL_ID = 1269794533923754089 # log channel
    LFG_BLOCKLIST_ROLE_ID = 1527757073750691921  # LFG Blocklist Role: holders cannot use the new LFG system
    LFG_BLOCKED_MESSAGE = (
        "You are blocked from using the LFG system."
        " Contact the moderators if you believe this is a mistake."
    )
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
        # New-system state (slash commands + sticky). Legacy prefix commands
        # never touch these. The new sticky keys are registered separately so
        # the legacy default_guild dict above stays untouched.
        self.config.register_guild(new_sticky_channels=[], new_sticky_cache={})
        self._cooldown_cache: Dict[Tuple[int, int], float] = {}
        self._ping_toggle_last: Dict[int, float] = {}
        self._new_sticky_locks: Dict[int, asyncio.Lock] = {}
        # In-memory mirror of each channel's current sticky message id, written
        # under the repost lock; lets the listener recognize our own sticky
        # without a Config read per message.
        self._new_sticky_ids: Dict[int, int] = {}
        self._sticky_view: Optional[LFGStickyView] = None

    async def cog_load(self):
        # Persistent-view registration: one static view serves every deployed
        # sticky, across restarts (timeout=None + stable custom_ids).
        self._sticky_view = LFGStickyView(self)
        self.bot.add_view(self._sticky_view)

    async def cog_unload(self):
        # Stop the view so a reloaded cog does not leave two handlers
        # answering the same custom_ids (double-dispatch on reload).
        if self._sticky_view is not None:
            self._sticky_view.stop()

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
        # Freeze exemption (see REFACTOR_PLAN.md Revision 15): the blocklist
        # must cover the legacy surface too, or it is trivially bypassed.
        if self._is_lfg_blocked(ctx.author):
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(self.LFG_BLOCKED_MESSAGE, delete_after=10)
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
        # Freeze exemption (see REFACTOR_PLAN.md Revision 15): blocked members
        # cannot manage the ping role on the legacy surface either.
        if self._is_lfg_blocked(ctx.author):
            return await ctx.send(self.LFG_BLOCKED_MESSAGE, delete_after=10)
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
    # New system (slash commands). Purely additive; the legacy prefix
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

    def _is_lfg_blocked(self, member: discord.Member) -> bool:
        """Holders of the LFG Blocklist Role cannot use the new LFG system.
        Checked at every surface and re-checked at the post moment, so
        assigning the role takes effect even against an open draft."""
        return any(r.id == self.LFG_BLOCKLIST_ROLE_ID for r in member.roles)

    def _region_gate_message(self) -> str:
        if self.REGION_CHANNEL_ID:
            return f"You need a region role to post. Pick one in <#{self.REGION_CHANNEL_ID}> first!"
        return "You need a region role to post. Ask an admin where to pick one!"

    def build_lfg_embed(
        self,
        member: discord.Member,
        lobby_id: str,
        notes: Optional[str],
        region: Optional[Tuple[str, str, int]] = None,
        settings: Optional[Dict[str, str]] = None,
        is_modded: bool = False,
    ) -> discord.Embed:
        """Same look as the legacy embed, plus Region and lobby-settings fields.
        Modded posts are always blue; the booster green applies to vanilla only."""
        title = "Euuuuuugh!" if random.randint(1, 1000) == 1 else "Looking For Group"
        if not is_modded and any(r.id == 1387554310832918528 for r in member.roles):
            color = discord.Color.green()
        else:
            color = discord.Color.blue()
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
        stamp, so two racing submissions cannot both pass."""
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

    # -- The chained two-modal flow (all LFG posting surfaces) ----------------

    async def handle_lfg_stage_one(self, interaction: discord.Interaction, modal: LFGPostModal):
        """Chained stage one: validate the mandatory form, stash a draft on an
        ephemeral panel, and offer Post Now / Optional Settings."""
        member = interaction.user

        if self._is_lfg_blocked(member):
            return await interaction.response.send_message(self.LFG_BLOCKED_MESSAGE, ephemeral=True)

        lobby = str(modal.lobby_id_field.component.value or "").strip()
        if not lobby.isdigit() or not LOBBY_ID_MIN_LENGTH <= len(lobby) <= LOBBY_ID_MAX_LENGTH:
            return await interaction.response.send_message(
                f"The Lobby ID must be {LOBBY_ID_MIN_LENGTH} to {LOBBY_ID_MAX_LENGTH} numbers.",
                ephemeral=True,
            )
        try:
            max_players = modal.max_players_field.component.values[0]
            gamemode = modal.gamemode_field.component.values[0]
        except IndexError:
            return await interaction.response.send_message(
                "A required selection is missing. Please try again.", ephemeral=True
            )
        if modal.enforce_gate and self.get_member_region(member) is None:
            return await interaction.response.send_message(self._region_gate_message(), ephemeral=True)
        if modal.enforce_cooldown:
            # Peek only: the commit happens at the actual post moment in
            # finalize_chained_post, so an abandoned draft costs nothing.
            remaining = await self.check_cooldown(member)
            if remaining > 0:
                return await interaction.response.send_message(
                    f"You can post again in {max(1, round(remaining))}s.", ephemeral=True
                )

        randomizer_values = modal.randomizer_field.component.values
        randomizer = randomizer_values[0] if randomizer_values else None
        optional = dict(modal.optional_settings)
        if randomizer is not None:
            optional["Weapon Randomizer"] = randomizer

        draft = {
            "lobby": lobby,
            "notes": self.sanitize_notes(modal.notes_field.component.value or None),
            "mandatory": {
                "Max Players": max_players,
                "Gamemode": gamemode,
                "Modded Lobby": "Yes" if modal.is_modded else "No",
            },
            "optional": optional,
            "destination_id": modal.destination_id,
            "silent_ping": modal.silent_ping,
            "is_modded": modal.is_modded,
            "enforce_gate": modal.enforce_gate,
            "enforce_cooldown": modal.enforce_cooldown,
        }
        view = LFGDraftPanelView(self, draft)
        await interaction.response.send_message(
            "Core details saved! Post now, or add optional settings first?",
            view=view,
            ephemeral=True,
        )
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            view.message = None

    async def handle_lfg_stage_two(self, interaction: discord.Interaction, modal2: LFGOptionalSettingsModal):
        """Chained stage two: validate First To, merge the optional settings
        into the draft, then post immediately."""
        panel_view = modal2.panel_view

        first_to_raw = str(modal2.first_to_field.component.value or "").strip()
        if first_to_raw and (not first_to_raw.isdigit() or not 1 <= int(first_to_raw) <= 50):
            return await interaction.response.send_message(
                "First To must be a number from 1 to 50. Nothing was saved; use"
                " the draft panel to try again.",
                ephemeral=True,
            )

        # Merge into a COPY: the shared draft is never mutated, so a racing
        # Post Now cannot pick up half-merged settings, and a failed post
        # leaves no stale values behind for the next attempt.
        optional = dict(panel_view.draft["optional"])
        if first_to_raw:
            optional["First To"] = str(int(first_to_raw))  # normalize "05" -> "5"
        for name, field in (
            ("Lobby Type", modal2.lobby_type_field),
            ("Friendly Fire", modal2.friendly_fire_field),
            ("Mid-Match Joining", modal2.mid_match_field),
            ("Enemy Outlines", modal2.outlines_field),
        ):
            values = field.component.values
            if values:
                optional[name] = values[0]

        await self.finalize_chained_post(
            interaction, panel_view, from_panel=False, optional_override=optional
        )

    async def finalize_chained_post(
        self,
        interaction: discord.Interaction,
        panel_view: LFGDraftPanelView,
        *,
        from_panel: bool,
        optional_override: Optional[Dict[str, Optional[str]]] = None,
    ):
        """Post a chained-flow draft. from_panel=True means the Post Now button
        (edit the panel in place); False means the stage-two modal submit
        (fresh interaction; ephemeral followup + best-effort panel cleanup).
        optional_override, when given, replaces the draft's optional settings
        for this attempt only (the shared draft is never mutated).
        The gate re-check and the cooldown check-and-commit happen HERE, at
        the actual post moment (Revision 14): an abandoned draft never
        consumes the cooldown, a failed send refunds it, and a live post
        never gets it refunded. Test commands set the enforce flags False."""
        draft = panel_view.draft
        member = interaction.user

        # Synchronous check-and-set: exactly one post per draft. posted is
        # checked first, so after these guards is_finished() can only mean the
        # panel timed out (a stage-two modal can outlive the 300s expiry).
        if panel_view.posted:
            return await interaction.response.send_message(
                "This draft was already posted.", ephemeral=True
            )
        if panel_view.finalizing:
            return await interaction.response.send_message(
                "This draft is already being posted.", ephemeral=True
            )
        if panel_view.is_finished():
            return await interaction.response.send_message(
                "This draft expired. Run the command again to post.", ephemeral=True
            )
        panel_view.finalizing = True
        stamp: Optional[float] = None
        try:
            # Authoritative blocklist re-check at the post moment: assigning
            # the role mid-draft takes effect before anything is consumed.
            if self._is_lfg_blocked(member):
                msg = self.LFG_BLOCKED_MESSAGE
                if not from_panel:
                    msg += " Your optional settings were not saved."
                return await interaction.response.send_message(msg, ephemeral=True)

            # Authoritative gate re-check at the post moment (roles can change
            # while the panel sits); the region that passed is what renders.
            region = self.get_member_region(member)
            if draft["enforce_gate"] and region is None:
                msg = self._region_gate_message()
                if not from_panel:
                    msg += " Your optional settings were not saved; use the draft panel to try again."
                return await interaction.response.send_message(msg, ephemeral=True)

            # Cooldown: cache-warm, then atomic check-and-stamp (no await
            # between check and stamp), committed at the actual post moment so
            # abandoned drafts never consume it. The ordering invariants of
            # REFACTOR_PLAN.md section 4.4 live here now.
            if draft["enforce_cooldown"]:
                await self.check_cooldown(member)
                stamp = self.commit_cooldown_sync(member)
                if stamp is None:
                    remaining = await self.check_cooldown(member)
                    msg = f"You can post again in {max(1, round(remaining))}s."
                    if not from_panel:
                        msg += " Your optional settings were not saved; use the draft panel to try again."
                    return await interaction.response.send_message(msg, ephemeral=True)

            if from_panel:
                await interaction.response.defer()
            else:
                await interaction.response.defer(ephemeral=True, thinking=True)

            async def report_failure(msg: str):
                if from_panel:
                    # Keep the panel and its buttons alive so the admin can retry.
                    try:
                        await interaction.edit_original_response(content=msg, view=panel_view)
                    except discord.HTTPException:
                        pass
                else:
                    try:
                        await interaction.followup.send(msg, ephemeral=True)
                    except discord.HTTPException:
                        pass

            role = interaction.guild.get_role(self.LFG_ROLE_ID)
            channel = interaction.guild.get_channel(draft["destination_id"])
            if role is None or channel is None:
                if stamp is not None:
                    await self.refund_cooldown(member, stamp)
                    stamp = None
                return await report_failure(
                    "The LFG role or channel is not configured. Please contact an administrator."
                )

            optional = optional_override if optional_override is not None else draft["optional"]
            settings: Dict[str, str] = dict(draft["mandatory"])
            for name, value in optional.items():
                if value is not None:
                    settings[name] = value
            embed = self.build_lfg_embed(
                member,
                draft["lobby"],
                draft["notes"],
                region=region,
                settings=settings,
                is_modded=draft["is_modded"],
            )
            if draft["silent_ping"]:
                mentions = discord.AllowedMentions.none()
            else:
                mentions = discord.AllowedMentions(roles=[role])

            try:
                message = await channel.send(content=role.mention, embed=embed, allowed_mentions=mentions)
            except discord.HTTPException:
                log.exception("Failed to send chained LFG post to channel %s", draft["destination_id"])
                if stamp is not None:
                    await self.refund_cooldown(member, stamp)
                    stamp = None
                return await report_failure(
                    "I couldn't post to the LFG channel. Please contact an administrator."
                )

            panel_view.posted = True
            panel_view.stop()
            # The post is live: persist the stamp; failures here log and never
            # refund (a live post keeps its cooldown).
            if stamp is not None:
                try:
                    await self.config.member(member).last_lfg_ts.set(stamp)
                except Exception:
                    log.exception("Failed to persist LFG cooldown stamp")
            confirmation = f"Posted! [Jump to your LFG]({message.jump_url}) | Lobby ID: `{draft['lobby']}`"
            if from_panel:
                try:
                    await interaction.edit_original_response(content=confirmation, view=None)
                except discord.HTTPException:
                    log.exception("Failed to edit the draft panel with the confirmation")
                    # The post IS live; make sure the admin hears about it.
                    try:
                        await interaction.followup.send(confirmation, ephemeral=True)
                    except discord.HTTPException:
                        pass
            else:
                try:
                    await interaction.followup.send(confirmation, ephemeral=True)
                except discord.HTTPException:
                    log.exception("Failed to send chained LFG confirmation followup")
                if panel_view.message is not None:
                    try:
                        await panel_view.message.edit(content="Posted via Optional Settings.", view=None)
                    except discord.HTTPException:
                        pass
        except Exception:
            # Unexpected failure after a commit and before the post landed:
            # never leave the cooldown consumed with no post. Compare-and-clear
            # makes a second refund a harmless no-op. (CancelledError is not
            # caught deliberately: it only occurs at shutdown, where the
            # cache-only stamp dies with the process anyway.)
            if stamp is not None and not panel_view.posted:
                try:
                    await self.refund_cooldown(member, stamp)
                except Exception:
                    log.exception("Cooldown refund failed during error handling")
            raise
        finally:
            # Never leaves finalizing stuck True on an unexpected exception;
            # after a success the posted guard is what blocks re-posting.
            panel_view.finalizing = False

    # -- New-system sticky with quick-post buttons ----------------------------

    async def handle_sticky_button(self, interaction: discord.Interaction, *, is_modded: bool):
        """Quick-post from the sticky buttons: identical to /lfg and /lfgmod
        (same gate, same shared cooldown, same two-step flow including the
        Optional Settings modal)."""
        await self._launch_lfg_modal(
            interaction,
            destination_id=self.NEW_LFG_CHANNEL_ID,
            is_modded=is_modded,
            enforce_gate=True,
            enforce_cooldown=True,
            silent_ping=False,
            optional_settings=self._blank_optional_settings(),
        )

    def _build_new_sticky_embed(self) -> discord.Embed:
        if self.REGION_CHANNEL_ID:
            region_line = f"You need a region role to post. Pick one in <#{self.REGION_CHANNEL_ID}>!"
        else:
            region_line = "You need a region role to post. Ask an admin where to pick one!"
        description = (
            "Post lobbies with slash commands. Everything stays private to you "
            f"until your post goes live in <#{self.NEW_LFG_CHANNEL_ID}>.\n\n"
            "**/lfg**: post a vanilla lobby. Fill in the form, then Post Now or "
            "add optional settings (First To, Lobby Type, Friendly Fire and "
            "more) first.\n"
            "**/lfgmod**: post a modded lobby. Same flow; please list your mods "
            "in the Notes field!\n"
            "**/lfgpings**: toggle the LFG ping role to get notified about new "
            "lobbies.\n\n"
            f"{region_line}\n\n"
            "**Prefer buttons?** The ones below open the exact same flow as "
            "/lfg and /lfgmod."
        )
        return discord.Embed(
            title="How to use the new LFG system",
            description=description,
            color=discord.Color.blue(),
        )

    async def _repost_new_sticky(self, channel: discord.TextChannel, trigger_id: Optional[int] = None) -> bool:
        """Delete-and-repost the new sticky at the bottom of the channel.
        Guarded by a per-channel lock and stored message ids (never by title
        matching, the legacy sticky's known footgun). Raw Config reads/writes
        keep Config locks away from the REST calls. Returns True if a sticky
        is in place afterwards."""
        lock = self._new_sticky_locks.setdefault(channel.id, asyncio.Lock())
        async with lock:
            # Re-check under the lock: the channel may have been toggled off
            # while we queued, or the triggering message may be the sticky we
            # just posted (its gateway event can beat our send() returning).
            active = await self.config.guild(channel.guild).new_sticky_channels()
            if channel.id not in active:
                return False
            if trigger_id is not None and trigger_id == self._new_sticky_ids.get(channel.id):
                return True
            old_id = await self.config.guild(channel.guild).get_raw(
                "new_sticky_cache", str(channel.id), default=None
            )
            if old_id:
                try:
                    old_msg = await channel.fetch_message(old_id)
                    await old_msg.delete()
                except discord.HTTPException:
                    pass
            try:
                new_msg = await channel.send(
                    embed=self._build_new_sticky_embed(), view=self._sticky_view
                )
            except discord.HTTPException:
                log.exception("Failed to repost the new LFG sticky in channel %s", channel.id)
                return False
            self._new_sticky_ids[channel.id] = new_msg.id
            await self.config.guild(channel.guild).set_raw(
                "new_sticky_cache", str(channel.id), value=new_msg.id
            )
            return True

    @commands.Cog.listener("on_message")
    async def new_sticky_on_message(self, message: discord.Message):
        """Keeps the new sticky at the bottom of its channels. Bot messages DO
        trigger a repost (LFG posts landing in the feed must push the sticky
        down); only two messages are ignored: our own current sticky, and the
        frozen legacy sticky's reposts, so the two systems can never feed each
        other in a loop."""
        if not message.guild or self.bot.user is None:
            return
        if message.flags.ephemeral or message.flags.loading:
            # Verified: the creating app DOES receive MESSAGE_CREATE for its
            # own ephemeral responses (gate deferrals, draft panels,
            # confirmations; Red enables the DIRECT_MESSAGES intent that
            # delivers them). Today they arrive without guild_id (open Discord
            # bug, discord-api-docs#4557) so the guild check above happens to
            # filter them, but that is an accident scheduled to be fixed; this
            # flag guard is the robust filter. Invisible messages must never
            # trigger a sticky repost.
            return
        if message.author.id == self.bot.user.id:
            if message.id == self._new_sticky_ids.get(message.channel.id):
                return
            if message.embeds and message.embeds[0].title in (
                "How to use the LFG system",      # the legacy sticky reposting itself
                "How to use the new LFG system",  # our own sticky under any gateway timing
            ):
                # Title matching is used ONLY as an ignore filter on our own
                # bot's messages (never to find or delete anything), so the
                # legacy title footgun does not apply. Only _repost_new_sticky
                # ever sends the new-sticky embed, which makes this a hard
                # bound of one repost per external trigger.
                return
        active = await self.config.guild(message.guild).new_sticky_channels()
        if message.channel.id not in active:
            return
        await self._repost_new_sticky(message.channel, trigger_id=message.id)

    @commands.command(name="newlfgsticky")
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def newlfgsticky(self, ctx: commands.Context, state: Optional[str] = None):
        """
        Toggle the new LFG system sticky in the current channel.

        With no argument the command toggles: run it once to enable, run it
        again in the same channel to disable. You can also be explicit with
        `!newlfgsticky on` or `!newlfgsticky off`. Note that deleting the
        sticky message by hand does not disable it; the sticky reposts on the
        next message. Use this command to turn it off.

        The sticky explains /lfg, /lfgmod and /lfgpings, and carries LFG and
        Modded LFG quick-post buttons (the same form as the slash commands,
        without the optional settings). Administrator only. Cannot be enabled
        in a channel where the legacy sticky is active.
        """
        if state is not None and state.lower() not in ("on", "off"):
            return await ctx.send(
                "Use `!newlfgsticky` to toggle, or `!newlfgsticky on` /"
                " `!newlfgsticky off` to be explicit.",
                delete_after=10,
            )
        guild_conf = self.config.guild(ctx.guild)
        currently_on = ctx.channel.id in await guild_conf.new_sticky_channels()
        target_on = (not currently_on) if state is None else state.lower() == "on"

        if target_on and currently_on:
            return await ctx.send(
                "The new LFG sticky is already enabled here. Run"
                " `!newlfgsticky off` to disable it.",
                delete_after=10,
            )
        if not target_on and not currently_on:
            return await ctx.send(
                "The new LFG sticky is not enabled in this channel.",
                delete_after=10,
            )

        if target_on:
            # Enable path. Refuse to share a channel with the legacy sticky:
            # the two systems would repost around each other on every message.
            if ctx.channel.id in await guild_conf.active_sticky_channels():
                return await ctx.send(
                    "The legacy sticky is active in this channel. Disable it"
                    " first with `!sticky-toggle` to avoid the two stickies"
                    " fighting.",
                    delete_after=15,
                )
            async with guild_conf.new_sticky_channels() as active:
                if ctx.channel.id not in active:
                    active.append(ctx.channel.id)
            if await self._repost_new_sticky(ctx.channel):
                # A reaction, not a message: a confirmation message would itself
                # trigger one redundant sticky repost.
                try:
                    await ctx.message.add_reaction("✅")
                except discord.DiscordException:
                    pass
            else:
                # Roll back so every later message does not retry a failing
                # send, and tear down any sticky a racing listener repost may
                # have managed to place in the window.
                async with guild_conf.new_sticky_channels() as active:
                    if ctx.channel.id in active:
                        active.remove(ctx.channel.id)
                await self._teardown_new_sticky(ctx.channel)
                await ctx.send(
                    "I couldn't post the sticky here. Check my permissions"
                    " (Send Messages and Embed Links), then try again."
                )
        else:
            # Disable path: deactivate first so queued reposts bail on their
            # re-check, then clean up under the repost lock so an in-flight
            # repost finishes first and its message is the one we delete.
            async with guild_conf.new_sticky_channels() as active:
                if ctx.channel.id in active:
                    active.remove(ctx.channel.id)
            await self._teardown_new_sticky(ctx.channel)
            await ctx.send("New LFG sticky disabled in this channel.", delete_after=10)

    async def _teardown_new_sticky(self, channel: discord.TextChannel):
        """Under the repost lock: clear the stored sticky id and delete the
        sticky message if one exists. The channel must already have been
        removed from new_sticky_channels so queued reposts bail out."""
        guild_conf = self.config.guild(channel.guild)
        lock = self._new_sticky_locks.setdefault(channel.id, asyncio.Lock())
        async with lock:
            old_id = await guild_conf.get_raw(
                "new_sticky_cache", str(channel.id), default=None
            )
            try:
                await guild_conf.clear_raw("new_sticky_cache", str(channel.id))
            except KeyError:
                pass
            self._new_sticky_ids.pop(channel.id, None)
            if old_id:
                try:
                    old_msg = await channel.fetch_message(old_id)
                    await old_msg.delete()
                except discord.HTTPException:
                    pass

    @staticmethod
    def _blank_optional_settings() -> Dict[str, Optional[str]]:
        """Placeholder dict fixing the display order of the optional settings.

        "Weapon Randomizer" is filled from the stage-one modal's optional
        select; the rest are filled from the stage-two modal at post time.
        """
        return {
            "First To": None,
            "Lobby Type": None,
            "Friendly Fire": None,
            "Mid-Match Joining": None,
            "Weapon Randomizer": None,
            "Enemy Outlines": None,
        }

    async def _launch_lfg_modal(
        self,
        interaction: discord.Interaction,
        *,
        destination_id: int,
        is_modded: bool,
        enforce_gate: bool,
        enforce_cooldown: bool,
        silent_ping: bool,
        optional_settings: Dict[str, Optional[str]],
    ):
        """Shared front door for the /lfg command family: blocklist ->
        region gate -> cooldown peek -> the stage-one modal. All feedback is
        ephemeral."""
        if self._is_lfg_blocked(interaction.user):
            return await interaction.response.send_message(self.LFG_BLOCKED_MESSAGE, ephemeral=True)
        if enforce_gate and self.get_member_region(interaction.user) is None:
            return await interaction.response.send_message(self._region_gate_message(), ephemeral=True)
        if enforce_cooldown:
            remaining = await self.check_cooldown(interaction.user)
            if remaining > 0:
                return await interaction.response.send_message(
                    f"You can post again in {max(1, round(remaining))}s.", ephemeral=True
                )
        await interaction.response.send_modal(
            LFGPostModal(
                self,
                destination_id=destination_id,
                is_modded=is_modded,
                enforce_gate=enforce_gate,
                enforce_cooldown=enforce_cooldown,
                silent_ping=silent_ping,
                optional_settings=optional_settings,
            )
        )

    @app_commands.command(name="lfg", description="Post an LFG to #new-lfg")
    @app_commands.guild_only()
    async def slash_lfg(self, interaction: discord.Interaction):
        """Vanilla LFG via the two-step flow: the mandatory modal, then an
        ephemeral panel offering Post Now or an Optional Settings modal. The
        post is tagged Modded Lobby: No; moddedness is derived from which
        command was used, never asked in the form."""
        await self._launch_lfg_modal(
            interaction,
            destination_id=self.NEW_LFG_CHANNEL_ID,
            is_modded=False,
            enforce_gate=True,
            enforce_cooldown=True,
            silent_ping=False,
            optional_settings=self._blank_optional_settings(),
        )

    @app_commands.command(name="lfgmod", description="Post a modded LFG to #new-lfg")
    @app_commands.guild_only()
    async def slash_lfgmod(self, interaction: discord.Interaction):
        """Carbon copy of /lfg for modded lobbies: same two-step flow, same
        gate, same shared cooldown, same destination; the post is tagged
        Modded Lobby: Yes."""
        await self._launch_lfg_modal(
            interaction,
            destination_id=self.NEW_LFG_CHANNEL_ID,
            is_modded=True,
            enforce_gate=True,
            enforce_cooldown=True,
            silent_ping=False,
            optional_settings=self._blank_optional_settings(),
        )

    @app_commands.command(name="testlfg", description="Admin test of the LFG flow. Posts to the log channel")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def slash_testlfg(self, interaction: discord.Interaction):
        """Tester for /lfg using the chained two-modal flow: the mandatory modal,
        then an ephemeral panel offering Post Now or an Optional Settings modal.
        Admin-only, posts to the log channel, no region gate, no cooldown,
        silent role mention. No slash options; stage two carries them instead."""
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "You need the Administrator permission to use this command.", ephemeral=True
            )
        await self._launch_lfg_modal(
            interaction,
            destination_id=self.TEST_CHANNEL_ID,
            is_modded=False,
            enforce_gate=False,
            enforce_cooldown=False,
            silent_ping=True,
            optional_settings=self._blank_optional_settings(),
        )

    @app_commands.command(name="testlfgmod", description="Admin test of the modded LFG flow. Posts to the log channel")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def slash_testlfgmod(self, interaction: discord.Interaction):
        """Tester for /lfgmod using the chained two-modal flow: the mandatory
        modal, then an ephemeral panel offering Post Now or an Optional Settings
        modal. Admin-only, posts to the log channel, no region gate, no cooldown,
        silent role mention. No slash options; stage two carries them instead."""
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "You need the Administrator permission to use this command.", ephemeral=True
            )
        await self._launch_lfg_modal(
            interaction,
            destination_id=self.TEST_CHANNEL_ID,
            is_modded=True,
            enforce_gate=False,
            enforce_cooldown=False,
            silent_ping=True,
            optional_settings=self._blank_optional_settings(),
        )

    @app_commands.command(name="lfgpings", description="Toggle whether you get pinged for LFG posts")
    @app_commands.guild_only()
    async def slash_lfgpings(self, interaction: discord.Interaction):
        if self._is_lfg_blocked(interaction.user):
            return await interaction.response.send_message(self.LFG_BLOCKED_MESSAGE, ephemeral=True)
        now = time.time()
        if now - self._ping_toggle_last.get(interaction.user.id, 0.0) < 5:
            return await interaction.response.send_message(
                "Slow down! Try again in a moment.", ephemeral=True
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
    # unexpected app-command exceptions and replies ephemerally; a cog handler
    # here would duplicate both. Modal errors are handled by LFGPostModal.on_error.

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        """Purge the stored cooldown timestamps (the only end-user data this cog keeps)."""
        all_members = await self.config.all_members()
        for guild_id, members in all_members.items():
            if user_id in members:
                await self.config.member_from_ids(guild_id, user_id).clear()
        self._cooldown_cache = {k: v for k, v in self._cooldown_cache.items() if k[1] != user_id}
        self._ping_toggle_last.pop(user_id, None)
