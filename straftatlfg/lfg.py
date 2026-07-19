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

# Notes field cap, shown in the field copy and enforced client-side.
# Tightening any of these bounds invalidates stored last_lfg_post prefills;
# the /lfgupdate modals drop out-of-bounds defaults defensively (helpers
# below) so their forms always open.
NOTES_MAX_LENGTH = 200


def _lobby_prefill(value):
    """A stored lobby id, or None if it no longer satisfies current bounds."""
    if (
        isinstance(value, str)
        and value.isdecimal()
        and LOBBY_ID_MIN_LENGTH <= len(value) <= LOBBY_ID_MAX_LENGTH
    ):
        return value
    return None


def _players_prefill(value):
    if isinstance(value, str) and value.isdecimal() and 2 <= int(value) <= 10:
        return value
    return None


def _first_to_prefill(value):
    if isinstance(value, str) and value.isdecimal() and 1 <= int(value) <= 50:
        return value
    return None


def _notes_prefill(value):
    if isinstance(value, str) and len(value) <= NOTES_MAX_LENGTH:
        return value
    return None


def _update_select_options(values, stored):
    """Options for a per-category /lfgupdate dropdown: the real values plus a
    'Not set' entry (value __clear__) so removing a setting is explicit. The
    current state is always marked default, so an untouched select submits it
    unchanged and the form always shows the post's current configuration."""
    options = [
        discord.SelectOption(label=value, default=(stored == value)) for value in values
    ]
    options.append(
        discord.SelectOption(label="Not set", value="__clear__", default=(stored is None))
    )
    return options


class LFGPostModal(discord.ui.Modal, title="Post an LFG"):
    """Stage one of the guided two-modal bridge: the sticky quick-post
    buttons and the /testlfg + /testlfgmod testers. The slash commands use
    the streamlined LFGFusedModal instead.

    Discord hard-caps modals at 5 top-level components, so the modal carries
    the mandatory fields (Lobby ID, Max Players, Gamemode), the optional
    Weapon Randomizer select, and Notes. Submitting stashes a draft on an
    ephemeral panel offering Post Now or an Optional Settings modal with the
    remaining five settings (Discord cannot open a modal from a modal
    submission, hence the button bridge). Moddedness is derived from the
    invoking command (is_modded), never asked in the form. Vanilla offers
    Max Players as a 2/3/4 select; modded lobbies support up to 10 players,
    so their variant uses a numerical text input validated server-side
    (2 to 10). Fields are built in __init__ (the documented alternative to
    class-level Labels) so the two variants can differ.
    """

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
        self.enforce_gate = enforce_gate
        self.enforce_cooldown = enforce_cooldown
        self.silent_ping = silent_ping
        # Display name -> value placeholders for the optional settings; stage
        # two fills a copy at post time (stable display order). Stage one never
        # commits the cooldown, so there is nothing here to refund on error.
        self.optional_settings: Dict[str, Optional[str]] = optional_settings or {}

        self.lobby_id_field = discord.ui.Label(
            text="Lobby ID",
            description=f"Numbers only, {LOBBY_ID_MIN_LENGTH} to {LOBBY_ID_MAX_LENGTH} digits",
            component=discord.ui.TextInput(
                placeholder="12345678",
                min_length=LOBBY_ID_MIN_LENGTH,
                max_length=LOBBY_ID_MAX_LENGTH,
                required=True,
            ),
        )
        if is_modded:
            self.max_players_field = discord.ui.Label(
                text="Max Players",
                description="A number from 2 to 10",
                component=discord.ui.TextInput(placeholder="10", max_length=2, required=True),
            )
        else:
            self.max_players_field = discord.ui.Label(
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
        gamemode_options = [
            discord.SelectOption(label="FFA"),
            discord.SelectOption(label="Teams"),
        ]
        if is_modded:
            # Alternative gamemodes are a popular category of mods.
            gamemode_options.append(discord.SelectOption(label="Modded Gamemode"))
            gamemode_placeholder = "FFA, Teams, or Modded Gamemode?"
        else:
            gamemode_placeholder = "FFA or Teams?"
        self.gamemode_field = discord.ui.Label(
            text="Gamemode",
            component=discord.ui.Select(
                placeholder=gamemode_placeholder,
                options=gamemode_options,
                required=True,
            ),
        )
        self.randomizer_field = discord.ui.Label(
            text="Weapon Randomizer",
            description="Optional. Leave empty to skip.",
            component=discord.ui.Select(
                placeholder="Full Randomize, Swapped, Custom Randomize, or No?",
                options=[
                    discord.SelectOption(label="Full Randomize"),
                    discord.SelectOption(label="Swapped"),
                    discord.SelectOption(label="Custom Randomize"),
                    discord.SelectOption(label="No"),
                ],
                required=False,
            ),
        )
        if is_modded:
            notes_description = f"Optional, but please list the mods your lobby is running! Max {NOTES_MAX_LENGTH} characters."
            notes_placeholder = "Which mods are you using? Anything else to note?"
        else:
            notes_description = f"Optional. Casual? Competitive? Anything else! Max {NOTES_MAX_LENGTH} characters."
            notes_placeholder = None
        self.notes_field = discord.ui.Label(
            text="Notes",
            description=notes_description,
            component=discord.ui.TextInput(
                style=discord.TextStyle.paragraph,
                max_length=NOTES_MAX_LENGTH,
                required=False,
                placeholder=notes_placeholder,
            ),
        )
        for field in (
            self.lobby_id_field,
            self.max_players_field,
            self.gamemode_field,
            self.randomizer_field,
            self.notes_field,
        ):
            self.add_item(field)

    def read_max_players(self) -> Tuple[Optional[str], Optional[str]]:
        """(value, error_message) for either variant: the vanilla 2/3/4 select
        or the modded numerical text input validated 2 to 10."""
        component = self.max_players_field.component
        if isinstance(component, discord.ui.TextInput):
            raw = str(component.value or "").strip()
            if not raw.isdecimal() or not 2 <= int(raw) <= 10:
                return None, "Max Players must be a number from 2 to 10."
            return str(int(raw)), None
        if not component.values:
            return None, "A required selection is missing. Please try again."
        return component.values[0], None

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


class LFGFusedModal(discord.ui.Modal, title="Post an LFG"):
    """The streamlined single-modal flow behind /lfg and /lfgmod.

    Everything compressed into Discord's 5-component modal cap. Vanilla
    (Revision 16 layout): Lobby ID, the Max Players x Gamemode cross-product
    as a 6-option radio group, the packed optional-settings multi-select,
    First To, Notes. Modded (Revision 18 layout): Max Players is a numerical
    text input validated server-side (2 to 10) with Gamemode as its own
    radio group, which costs a slot, so the modded variant has no First To
    field (it remains available on the detailed button tier). Fields are
    built in __init__ (the documented alternative to class-level Labels) so
    the two variants can differ. Discord cannot enforce one-per-category
    inside the packed select, so exclusivity is validated server-side on
    submit and a conflict is refused ephemerally before anything is consumed.
    The sticky buttons keep the guided two-modal bridge (LFGPostModal) as the
    detailed alternative.
    """

    PACKED_OPTIONS = (
        ("Weapon Randomizer", "Full Randomize"),
        ("Weapon Randomizer", "Swapped"),
        ("Weapon Randomizer", "Custom Randomize"),
        ("Weapon Randomizer", "No"),
        ("Lobby Type", "Public"),
        ("Lobby Type", "Invite Only"),
        ("Lobby Type", "Private"),
        ("Friendly Fire", "Enabled"),
        ("Friendly Fire", "Disabled"),
        ("Mid-Match Joining", "Yes"),
        ("Mid-Match Joining", "No"),
        ("Enemy Outlines", "Enabled"),
        ("Enemy Outlines", "Disabled"),
    )

    def __init__(
        self,
        cog,
        destination_id: int,
        is_modded: bool,
        enforce_gate: bool,
        enforce_cooldown: bool,
        silent_ping: bool,
    ):
        super().__init__(title="Post a Modded LFG" if is_modded else "Post an LFG")
        self.cog = cog
        self.destination_id = destination_id
        self.is_modded = is_modded
        self.enforce_gate = enforce_gate
        self.enforce_cooldown = enforce_cooldown
        self.silent_ping = silent_ping
        # Set when this submission consumed the cooldown; cleared the moment
        # the post lands so a late failure can never refund a live post.
        self.committed_stamp: Optional[float] = None

        self.lobby_id_field = discord.ui.Label(
            text="Lobby ID",
            description=f"Numbers only, {LOBBY_ID_MIN_LENGTH} to {LOBBY_ID_MAX_LENGTH} digits",
            component=discord.ui.TextInput(
                placeholder="12345678",
                min_length=LOBBY_ID_MIN_LENGTH,
                max_length=LOBBY_ID_MAX_LENGTH,
                required=True,
            ),
        )
        if is_modded:
            # Modded lobbies support up to 10 players. A cross-product would
            # need 18 options (over the radio group's 10-option cap), so Max
            # Players becomes a numerical input and Gamemode its own radio
            # group. The extra slot comes from dropping First To, which stays
            # available on the detailed button tier and via /lfgupdate.
            self.core_field = None
            self.max_players_field = discord.ui.Label(
                text="Max Players",
                description="A number from 2 to 10",
                component=discord.ui.TextInput(placeholder="10", max_length=2, required=True),
            )
            self.gamemode_field = discord.ui.Label(
                text="Gamemode",
                component=discord.ui.RadioGroup(
                    options=[
                        # Modded Gamemode: alternative modes are a popular mod category.
                        discord.RadioGroupOption(label=mode, value=mode)
                        for mode in ("FFA", "Teams", "Modded Gamemode")
                    ],
                    required=True,
                ),
            )
        else:
            self.max_players_field = None
            self.gamemode_field = None
            self.core_field = discord.ui.Label(
                text="Lobby Setup",
                description="Max players and gamemode",
                component=discord.ui.RadioGroup(
                    options=[
                        discord.RadioGroupOption(label=f"{n} players, {mode}", value=f"{n}|{mode}")
                        for n in (2, 3, 4)
                        for mode in ("FFA", "Teams")
                    ],
                    required=True,
                ),
            )
        self.settings_field = discord.ui.Label(
            text="Optional Settings",
            description="Pick at most one option per category",
            component=discord.ui.Select(
                placeholder="Randomizer, lobby type, friendly fire and more",
                min_values=0,
                max_values=len(self.PACKED_OPTIONS),
                required=False,
                options=[
                    discord.SelectOption(label=f"{group}: {value}", value=f"{group}|{value}")
                    for group, value in self.PACKED_OPTIONS
                ],
            ),
        )
        if is_modded:
            self.first_to_field = None
        else:
            self.first_to_field = discord.ui.Label(
                text="First To",
                description="Optional. First to how many wins? 1 to 50.",
                component=discord.ui.TextInput(placeholder="10", max_length=2, required=False),
            )
        if is_modded:
            notes_description = f"Optional, but please list the mods your lobby is running! Max {NOTES_MAX_LENGTH} characters."
            notes_placeholder = "Which mods are you using? Anything else to note?"
        else:
            notes_description = f"Optional. Casual? Competitive? Anything else! Max {NOTES_MAX_LENGTH} characters."
            notes_placeholder = None
        self.notes_field = discord.ui.Label(
            text="Notes",
            description=notes_description,
            component=discord.ui.TextInput(
                style=discord.TextStyle.paragraph,
                max_length=NOTES_MAX_LENGTH,
                required=False,
                placeholder=notes_placeholder,
            ),
        )
        if is_modded:
            fields = (
                self.lobby_id_field,
                self.max_players_field,
                self.gamemode_field,
                self.settings_field,
                self.notes_field,
            )
        else:
            fields = (
                self.lobby_id_field,
                self.core_field,
                self.settings_field,
                self.first_to_field,
                self.notes_field,
            )
        for field in fields:
            self.add_item(field)

    def read_core(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """(max_players, gamemode, error_message) for either variant.
        Duplicated between LFGFusedModal and LFGUpdateEssentialsModal
        (same field attributes); keep the two copies in sync."""
        if self.is_modded:
            raw = str(self.max_players_field.component.value or "").strip()
            if not raw.isdecimal() or not 2 <= int(raw) <= 10:
                return None, None, "Max Players must be a number from 2 to 10."
            gamemode = self.gamemode_field.component.value
            if not gamemode:
                return None, None, "A required selection is missing. Please try again."
            return str(int(raw)), gamemode, None
        value = self.core_field.component.value
        if not value:
            return None, None, "A required selection is missing. Please try again."
        max_players, _, gamemode = value.partition("|")
        return max_players, gamemode, None

    def first_to_raw(self) -> str:
        """The First To entry, or an empty string on the modded variant,
        which has no First To field."""
        if self.first_to_field is None:
            return ""
        return str(self.first_to_field.component.value or "").strip()

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.handle_fused_submit(interaction, self)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("LFGFusedModal submission failed", exc_info=error)
        if self.committed_stamp is not None:
            try:
                await self.cog.refund_cooldown(interaction.user, self.committed_stamp)
            except Exception:
                log.exception("Cooldown refund failed during modal error handling")
        try:
            msg = "Something went wrong. Please try again."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


class LFGOptionalSettingsModal(discord.ui.Modal, title="Optional Lobby Settings"):
    """Stage two of the guided two-modal bridge (sticky buttons and test
    commands): the five optional lobby settings. Submitting posts the draft
    immediately. The slash commands carry these in their fused modal instead."""

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
    """The ephemeral bridge between the two guided modals (sticky buttons and
    test commands; the slash commands use the fused single modal instead).

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


class LFGUpdateEssentialsModal(discord.ui.Modal, title="Edit Essentials"):
    """Partial /lfgupdate form mirroring the stage-one posting layout: Lobby
    ID, players and gamemode, Weapon Randomizer, and Notes, prefilled from
    the stored post. The remaining optional settings have their own modal."""

    def __init__(self, cog, panel):
        super().__init__(title="Edit Essentials")
        self.cog = cog
        self.panel = panel
        record = panel.record or {}
        settings = record.get("settings") or {}
        is_modded = bool(record.get("is_modded"))
        self.is_modded = is_modded

        self.lobby_id_field = discord.ui.Label(
            text="Lobby ID",
            description=f"Numbers only, {LOBBY_ID_MIN_LENGTH} to {LOBBY_ID_MAX_LENGTH} digits",
            component=discord.ui.TextInput(
                placeholder="12345678",
                min_length=LOBBY_ID_MIN_LENGTH,
                max_length=LOBBY_ID_MAX_LENGTH,
                required=True,
                default=_lobby_prefill(record.get("lobby")),
            ),
        )
        if is_modded:
            self.core_field = None
            self.max_players_field = discord.ui.Label(
                text="Max Players",
                description="A number from 2 to 10",
                component=discord.ui.TextInput(
                    placeholder="10",
                    max_length=2,
                    required=True,
                    default=_players_prefill(settings.get("Max Players")),
                ),
            )
            self.gamemode_field = discord.ui.Label(
                text="Gamemode",
                component=discord.ui.RadioGroup(
                    options=[
                        discord.RadioGroupOption(
                            label=mode,
                            value=mode,
                            default=(settings.get("Gamemode") == mode),
                        )
                        for mode in ("FFA", "Teams", "Modded Gamemode")
                    ],
                    required=True,
                ),
            )
        else:
            self.max_players_field = None
            self.gamemode_field = None
            stored_combo = None
            if settings.get("Max Players") and settings.get("Gamemode"):
                stored_combo = f'{settings["Max Players"]}|{settings["Gamemode"]}'
            self.core_field = discord.ui.Label(
                text="Lobby Setup",
                description="Max players and gamemode",
                component=discord.ui.RadioGroup(
                    options=[
                        discord.RadioGroupOption(
                            label=f"{n} players, {mode}",
                            value=f"{n}|{mode}",
                            default=(stored_combo == f"{n}|{mode}"),
                        )
                        for n in (2, 3, 4)
                        for mode in ("FFA", "Teams")
                    ],
                    required=True,
                ),
            )
        if is_modded:
            notes_description = f"Optional, but please list the mods your lobby is running! Max {NOTES_MAX_LENGTH} characters."
        else:
            notes_description = f"Optional. Casual? Competitive? Anything else! Max {NOTES_MAX_LENGTH} characters."
        self.randomizer_field = discord.ui.Label(
            text="Weapon Randomizer",
            component=discord.ui.Select(
                placeholder="Full Randomize, Swapped, Custom Randomize, or No?",
                required=False,
                options=_update_select_options(
                    ("Full Randomize", "Swapped", "Custom Randomize", "No"),
                    settings.get("Weapon Randomizer"),
                ),
            ),
        )
        self.notes_field = discord.ui.Label(
            text="Notes",
            description=notes_description,
            component=discord.ui.TextInput(
                style=discord.TextStyle.paragraph,
                max_length=NOTES_MAX_LENGTH,
                required=False,
                default=_notes_prefill(record.get("notes")),
            ),
        )
        fields = [self.lobby_id_field]
        if is_modded:
            fields.extend([self.max_players_field, self.gamemode_field])
        else:
            fields.append(self.core_field)
        fields.extend([self.randomizer_field, self.notes_field])
        for field in fields:
            self.add_item(field)

    def read_core(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """(max_players, gamemode, error_message) for either variant.
        Duplicated between LFGFusedModal and LFGUpdateEssentialsModal
        (same field attributes); keep the two copies in sync."""
        if self.is_modded:
            raw = str(self.max_players_field.component.value or "").strip()
            if not raw.isdecimal() or not 2 <= int(raw) <= 10:
                return None, None, "Max Players must be a number from 2 to 10."
            gamemode = self.gamemode_field.component.value
            if not gamemode:
                return None, None, "A required selection is missing. Please try again."
            return str(int(raw)), gamemode, None
        value = self.core_field.component.value
        if not value:
            return None, None, "A required selection is missing. Please try again."
        max_players, _, gamemode = value.partition("|")
        return max_players, gamemode, None

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.handle_update_essentials(interaction, self)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("LFGUpdateEssentialsModal submission failed", exc_info=error)
        try:
            msg = "Something went wrong. Please try again."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


class LFGUpdateOptionalModal(discord.ui.Modal, title="Edit Optional Settings"):
    """Partial /lfgupdate form mirroring the stage-two posting layout: First
    To plus one labeled dropdown per category, each prefilled with the post's
    current state and carrying a 'Not set' entry for explicit removal. Weapon
    Randomizer lives on the Edit Essentials form, matching where it sits when
    posting. Available on both variants, so modded posts can set First To
    here even though their posting form has no room for the field."""

    def __init__(self, cog, panel):
        super().__init__(title="Edit Optional Settings")
        self.cog = cog
        self.panel = panel
        settings = (panel.record or {}).get("settings") or {}

        self.first_to_field = discord.ui.Label(
            text="First To",
            description="Optional. First to how many wins? 1 to 50.",
            component=discord.ui.TextInput(
                placeholder="10",
                max_length=2,
                required=False,
                default=_first_to_prefill(settings.get("First To")),
            ),
        )
        self.lobby_type_field = discord.ui.Label(
            text="Lobby Type",
            component=discord.ui.Select(
                placeholder="Who can join the lobby?",
                required=False,
                options=_update_select_options(
                    ("Public", "Invite Only", "Private"), settings.get("Lobby Type")
                ),
            ),
        )
        self.friendly_fire_field = discord.ui.Label(
            text="Friendly Fire",
            component=discord.ui.Select(
                placeholder="Friendly fire setting?",
                required=False,
                options=_update_select_options(
                    ("Enabled", "Disabled"), settings.get("Friendly Fire")
                ),
            ),
        )
        self.mid_match_field = discord.ui.Label(
            text="Mid-Match Joining",
            component=discord.ui.Select(
                placeholder="Allow joining mid-match?",
                required=False,
                options=_update_select_options(
                    ("Yes", "No"), settings.get("Mid-Match Joining")
                ),
            ),
        )
        self.outlines_field = discord.ui.Label(
            text="Enemy Outlines",
            component=discord.ui.Select(
                placeholder="Show enemy outlines?",
                required=False,
                options=_update_select_options(
                    ("Enabled", "Disabled"), settings.get("Enemy Outlines")
                ),
            ),
        )
        for field in (
            self.first_to_field,
            self.lobby_type_field,
            self.friendly_fire_field,
            self.mid_match_field,
            self.outlines_field,
        ):
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.handle_update_optional(interaction, self)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("LFGUpdateOptionalModal submission failed", exc_info=error)
        try:
            msg = "Something went wrong. Please try again."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


class LFGUpdatePanelView(discord.ui.View):
    """The ephemeral /lfgupdate management panel: edit the essentials, edit
    the optional settings, or delete the post. The record snapshot feeds the
    modal prefills; every action re-reads Config and refuses a stale panel
    whose target post changed. busy serializes in-flight actions."""

    def __init__(self, cog, record: Dict[str, object], guild_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.record = record
        self.guild_id = guild_id
        self.message_id = record.get("message_id")
        self.busy = False
        self.closed = False
        self.message = None
        # The currently attached delete-confirm view, if any: orphaned
        # confirms (double-clicked Delete) must never fire their timeout.
        self.active_confirm = None

    def panel_content(self) -> str:
        lobby = (self.record or {}).get("lobby", "?")
        url = (
            f"https://discord.com/channels/{self.guild_id}/"
            f"{(self.record or {}).get('channel_id')}/{self.message_id}"
        )
        return (
            f"Managing your latest LFG post (Lobby ID `{lobby}`)."
            f" [Jump to the post]({url})\n"
            "Edit the essentials, adjust the optional settings, or delete it."
        )

    async def on_timeout(self):
        if self.closed:
            return
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    content="This panel expired. Run /lfgupdate again.", view=self
                )
            except discord.HTTPException:
                pass

    async def _fresh_record(self, interaction: discord.Interaction):
        """Re-read the record; None (after an ephemeral refusal) if stale."""
        record = await self.cog.config.member(interaction.user).last_lfg_post()
        if not record or record.get("message_id") != self.message_id:
            await interaction.response.send_message(
                "Your last post changed since this panel opened. Run /lfgupdate again.",
                ephemeral=True,
            )
            return None
        return record

    @discord.ui.button(label="Edit Essentials", style=discord.ButtonStyle.success)
    async def edit_essentials(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await self._fresh_record(interaction)
        if record is None:
            return
        self.record = record
        await interaction.response.send_modal(LFGUpdateEssentialsModal(self.cog, self))

    @discord.ui.button(label="Optional Settings", style=discord.ButtonStyle.secondary)
    async def edit_optional(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await self._fresh_record(interaction)
        if record is None:
            return
        self.record = record
        await interaction.response.send_modal(LFGUpdateOptionalModal(self.cog, self))

    @discord.ui.button(label="Delete Post", style=discord.ButtonStyle.danger)
    async def delete_post(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.busy:
            return await interaction.response.send_message(
                "Another update for this post is in flight. Please try again.", ephemeral=True
            )
        record = await self._fresh_record(interaction)
        if record is None:
            return
        self.record = record
        if self.active_confirm is not None:
            self.active_confirm.stop()
        confirm = LFGUpdateDeleteConfirmView(self.cog, self)
        self.active_confirm = confirm
        await interaction.response.edit_message(
            content="Delete your LFG post? This cannot be undone.",
            view=confirm,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        log.exception("LFGUpdatePanelView interaction failed", exc_info=error)
        try:
            msg = "Something went wrong. Please try again."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


class LFGUpdateDeleteConfirmView(discord.ui.View):
    """Confirmation step for deleting the tracked LFG post."""

    def __init__(self, cog, panel: LFGUpdatePanelView):
        super().__init__(timeout=60)
        self.cog = cog
        self.panel = panel

    async def on_timeout(self):
        # Only the currently attached confirm may act; orphaned instances
        # (replaced by a re-clicked Delete) must not kill a live panel.
        if self.panel.closed or self.panel.active_confirm is not self:
            return
        self.panel.active_confirm = None
        if self.panel.message is not None:
            try:
                await self.panel.message.edit(
                    content="Deletion timed out. Run /lfgupdate again.", view=None
                )
            except discord.HTTPException:
                pass
        self.panel.stop()

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_update_delete(interaction, self.panel, self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        if self.panel.active_confirm is self:
            self.panel.active_confirm = None
        await interaction.response.edit_message(
            content=self.panel.panel_content(), view=self.panel
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        log.exception("LFGUpdateDeleteConfirmView interaction failed", exc_info=error)
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
    bookkeeping. The LFG and Modded LFG buttons open the detailed tier: the
    guided two-modal bridge (essentials modal, then Post Now or an Optional
    Settings page), with the same region gate, shared cooldown, and
    destination as the streamlined slash commands. The Update Last LFG button
    opens the same ephemeral management panel as /lfgupdate.
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

    @discord.ui.button(
        label="Update Last LFG",
        style=discord.ButtonStyle.secondary,
        custom_id="straftatlfg:v1:sticky_update",
    )
    async def sticky_update(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.open_update_panel(interaction)


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
    LFG_COOLDOWN_SECONDS = 30  # post cooldown window (legacy decorators match; reduced from 60, Revision 23)
    LFG_UPDATE_COOLDOWN_SECONDS = 10  # /lfgupdate cooldown; updates never re-ping, so it is short
    REGION_CHANNEL_ID = 1358380625744105522  # regions channel: hosts the region reaction message

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
        self.config.register_member(last_lfg_ts=0.0, last_lfg_post={})
        # New-system state (slash commands + sticky). Legacy prefix commands
        # never touch these. The new sticky keys are registered separately so
        # the legacy default_guild dict above stays untouched.
        self.config.register_guild(new_sticky_channels=[], new_sticky_cache={})
        self._cooldown_cache: Dict[Tuple[int, int], float] = {}
        self._ping_toggle_last: Dict[int, float] = {}
        # /lfgupdate cooldown stamps; in-memory only (10s window, restart
        # amnesty is irrelevant).
        self._update_last: Dict[Tuple[int, int], float] = {}
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
    @commands.cooldown(1, 30, commands.BucketType.user)  # 30s to match the new system (freeze exemption)
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
    @commands.cooldown(1, 30, commands.BucketType.user)  # 30s to match the new system (freeze exemption)
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
    def sanitize_notes(notes: Optional[str], allow_links: bool = False) -> Optional[str]:
        """Vanilla posts: unwrap masked links and strip raw URLs, same as the
        legacy _process_lfg sanitization. Modded posts pass allow_links=True
        because mod lists are usually links: raw URLs are kept, and masked
        links are rewritten to expose their destination (text (url)) so a
        role-pinging post cannot disguise where a link goes."""
        if notes:
            if allow_links:
                notes = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r"\1 (\2)", notes)
            else:
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
        updated: bool = False,
    ) -> discord.Embed:
        """Same look as the legacy embed, plus Region and lobby-settings
        fields. Color-coded by moddedness: vanilla lobbies are green, modded
        lobbies are blue (stock palette). updated=True marks an /lfgupdate
        edit in the footer."""
        title = "Euuuuuugh!" if random.randint(1, 1000) == 1 else "Looking For Group"
        color = discord.Color.blue() if is_modded else discord.Color.green()
        embed = discord.Embed(title=title, color=color, description=notes)
        embed.add_field(name="Lobby ID", value=f"`{lobby_id}`", inline=True)
        embed.add_field(name="Host", value=member.mention, inline=True)
        if region is not None:
            embed.add_field(name="Region", value=f"{region[0]} {region[1]}", inline=True)
        for name, value in (settings or {}).items():
            embed.add_field(name=name, value=value, inline=True)
        footer = "Join the lobby using the ID above!"
        if updated:
            footer = "Join the lobby using the ID above! (updated)"
        embed.set_footer(text=footer, icon_url=member.display_avatar.url)
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

    # -- The streamlined single-modal flow (/lfg, /lfgmod) and the ------------
    # -- /lfgupdate management panel ------------------------------------------

    def _read_fused_form(self, modal: LFGFusedModal):
        """Parse and validate a fused-modal submission for either variant.
        Returns (form, error_message); nothing is consumed on a validation
        failure. form carries lobby, max_players, gamemode, first_to (None
        when empty or when the modded variant lacks the field), picked
        (category -> value from the packed select, conflict-checked), notes
        (sanitized), and has_first_to_field."""
        lobby = str(modal.lobby_id_field.component.value or "").strip()
        if not lobby.isdecimal() or not LOBBY_ID_MIN_LENGTH <= len(lobby) <= LOBBY_ID_MAX_LENGTH:
            return None, (
                f"The Lobby ID must be {LOBBY_ID_MIN_LENGTH} to {LOBBY_ID_MAX_LENGTH} numbers."
            )
        max_players, gamemode, core_error = modal.read_core()
        if core_error:
            return None, core_error
        first_to_raw = modal.first_to_raw()
        if first_to_raw and (not first_to_raw.isdecimal() or not 1 <= int(first_to_raw) <= 50):
            return None, "First To must be a number from 1 to 50."
        # The packed multi-select: Discord cannot enforce one-per-category, so
        # exclusivity is validated here.
        picked: Dict[str, str] = {}
        conflicts = []
        for raw in modal.settings_field.component.values:
            group, _, value = raw.partition("|")
            if group in picked and group not in conflicts:
                conflicts.append(group)
            picked[group] = value
        if conflicts:
            return None, (
                "Pick at most one option per category. You picked more than one"
                f" for: {', '.join(conflicts)}. Please run the command again."
            )
        return {
            "lobby": lobby,
            "max_players": max_players,
            "gamemode": gamemode,
            "first_to": str(int(first_to_raw)) if first_to_raw else None,
            "picked": picked,
            "notes": self.sanitize_notes(
                modal.notes_field.component.value or None, allow_links=modal.is_modded
            ),
            "has_first_to_field": modal.first_to_field is not None,
        }, None

    async def handle_fused_submit(self, interaction: discord.Interaction, modal: LFGFusedModal):
        """The streamlined pipeline: everything in one modal, posted on submit.
        The ordering invariants of REFACTOR_PLAN.md section 4.4 apply directly:
        every validation precedes the gate, the gate precedes the cooldown
        commit, and the commit immediately precedes the send with refund on
        any committed-but-unsent failure."""
        member = interaction.user

        if self._is_lfg_blocked(member):
            return await interaction.response.send_message(self.LFG_BLOCKED_MESSAGE, ephemeral=True)

        # Validation: nothing is consumed on any failure here.
        form, form_error = self._read_fused_form(modal)
        if form_error:
            return await interaction.response.send_message(form_error, ephemeral=True)
        lobby = form["lobby"]
        notes = form["notes"]

        region = self.get_member_region(member)
        if modal.enforce_gate and region is None:
            return await interaction.response.send_message(self._region_gate_message(), ephemeral=True)

        optional = self._blank_optional_settings()
        optional["First To"] = form["first_to"]
        for group, value in form["picked"].items():
            optional[group] = value
        settings: Dict[str, str] = {
            "Max Players": form["max_players"],
            "Gamemode": form["gamemode"],
            "Modded Lobby": "Yes" if modal.is_modded else "No",
        }
        for name, value in optional.items():
            if value is not None:
                settings[name] = value

        # Cooldown: cache-warm from Config, then atomic check-and-stamp.
        if modal.enforce_cooldown:
            await self.check_cooldown(member)
            stamp = self.commit_cooldown_sync(member)
            if stamp is None:
                remaining = await self.check_cooldown(member)
                return await interaction.response.send_message(
                    f"You can post again in {max(1, round(remaining))}s.", ephemeral=True
                )
            modal.committed_stamp = stamp

        await interaction.response.defer(ephemeral=True, thinking=True)

        role = interaction.guild.get_role(self.LFG_ROLE_ID)
        channel = interaction.guild.get_channel(modal.destination_id)
        if role is None or channel is None:
            if modal.committed_stamp is not None:
                await self.refund_cooldown(member, modal.committed_stamp)
                modal.committed_stamp = None
            return await interaction.followup.send(
                "The LFG role or channel is not configured. Please contact an administrator.",
                ephemeral=True,
            )

        embed = self.build_lfg_embed(
            member, lobby, notes, region=region, settings=settings, is_modded=modal.is_modded
        )
        if modal.silent_ping:
            mentions = discord.AllowedMentions.none()
        else:
            mentions = discord.AllowedMentions(roles=[role])
        try:
            message = await channel.send(content=role.mention, embed=embed, allowed_mentions=mentions)
        except discord.HTTPException:
            log.exception("Failed to send fused LFG post to channel %s", modal.destination_id)
            if modal.committed_stamp is not None:
                await self.refund_cooldown(member, modal.committed_stamp)
                modal.committed_stamp = None
            return await interaction.followup.send(
                "I couldn't post to the LFG channel. Please contact an administrator.",
                ephemeral=True,
            )

        # The post is live: nothing beyond this point may refund the cooldown.
        committed = modal.committed_stamp
        modal.committed_stamp = None
        if committed is not None:
            try:
                await self.config.member(member).last_lfg_ts.set(committed)
            except Exception:
                log.exception("Failed to persist LFG cooldown stamp")
        await self._record_last_post(member, message, modal.is_modded, lobby, notes, settings)

        try:
            await interaction.followup.send(
                f"Posted! [Jump to your LFG]({message.jump_url}) | Lobby ID: `{lobby}`",
                ephemeral=True,
            )
        except discord.HTTPException:
            log.exception("Failed to send LFG confirmation followup")

    async def _record_last_post(
        self,
        member: discord.Member,
        message: discord.Message,
        is_modded: bool,
        lobby: str,
        notes: Optional[str],
        settings: Dict[str, str],
    ):
        """Remember the member's latest post so /lfgupdate can edit it in
        place. Best-effort: a failure here never affects the post itself."""
        try:
            await self.config.member(member).last_lfg_post.set(
                {
                    "message_id": message.id,
                    "channel_id": message.channel.id,
                    "is_modded": is_modded,
                    "lobby": lobby,
                    "notes": notes,
                    "settings": settings,
                }
            )
        except Exception:
            log.exception("Failed to record the LFG post for /lfgupdate")

    async def handle_update_essentials(self, interaction: discord.Interaction, modal: LFGUpdateEssentialsModal):
        """Partial update from the Edit Essentials modal: Lobby ID, players
        and gamemode, Weapon Randomizer, and Notes. The stage-two optional
        settings are preserved as stored."""
        lobby = str(modal.lobby_id_field.component.value or "").strip()
        if not lobby.isdecimal() or not LOBBY_ID_MIN_LENGTH <= len(lobby) <= LOBBY_ID_MAX_LENGTH:
            return await interaction.response.send_message(
                f"The Lobby ID must be {LOBBY_ID_MIN_LENGTH} to {LOBBY_ID_MAX_LENGTH} numbers.",
                ephemeral=True,
            )
        max_players, gamemode, core_error = modal.read_core()
        if core_error:
            return await interaction.response.send_message(core_error, ephemeral=True)
        notes = self.sanitize_notes(
            modal.notes_field.component.value or None, allow_links=modal.is_modded
        )
        essentials: Dict[str, Optional[str]] = {
            "lobby": lobby,
            "notes": notes,
            "max_players": max_players,
            "gamemode": gamemode,
        }
        randomizer_values = modal.randomizer_field.component.values
        if randomizer_values:
            essentials["randomizer"] = (
                None if randomizer_values[0] == "__clear__" else randomizer_values[0]
            )
        # An empty submit (no selection, no default marked) omits the key
        # entirely: the freshly re-read stored value stays authoritative.
        await self._finish_lfg_update(interaction, modal.panel, essentials=essentials)

    async def handle_update_optional(self, interaction: discord.Interaction, modal: LFGUpdateOptionalModal):
        """Partial update from the Edit Optional Settings modal. One dropdown
        per category makes conflicts impossible client-side; each dropdown
        always shows the current state (Not set when absent), so an untouched
        form changes nothing, picking a value sets it, and picking Not set
        (or clearing First To) removes that setting."""
        first_to_raw = str(modal.first_to_field.component.value or "").strip()
        if first_to_raw and (not first_to_raw.isdecimal() or not 1 <= int(first_to_raw) <= 50):
            return await interaction.response.send_message(
                "First To must be a number from 1 to 50.", ephemeral=True
            )
        optional: Dict[str, Optional[str]] = {
            "First To": str(int(first_to_raw)) if first_to_raw else None
        }
        for group, field in (
            ("Lobby Type", modal.lobby_type_field),
            ("Friendly Fire", modal.friendly_fire_field),
            ("Mid-Match Joining", modal.mid_match_field),
            ("Enemy Outlines", modal.outlines_field),
        ):
            values = field.component.values
            if not values:
                # Defensive: a default is always marked, so an empty submit
                # should not occur; skip the category (stored value kept).
                continue
            optional[group] = None if values[0] == "__clear__" else values[0]
        await self._finish_lfg_update(interaction, modal.panel, optional=optional)

    async def _finish_lfg_update(
        self,
        interaction: discord.Interaction,
        panel: LFGUpdatePanelView,
        *,
        essentials: Optional[Dict[str, Optional[str]]] = None,
        optional: Optional[Dict[str, Optional[str]]] = None,
    ):
        """Shared tail of every /lfgupdate edit: blocklist, busy and stale
        guards, the update cooldown (compare-and-clear refund on failure),
        fetch and edit the post, persist the record, refresh the panel. The
        deltas are merged onto the freshly re-read record, never the panel
        snapshot, so a concurrent edit from another session is not reverted."""
        member = interaction.user
        if self._is_lfg_blocked(member):
            return await interaction.response.send_message(self.LFG_BLOCKED_MESSAGE, ephemeral=True)
        # Synchronous check-and-set: one in-flight action per panel.
        if panel.busy:
            return await interaction.response.send_message(
                "Another update for this post is in flight. Please try again.", ephemeral=True
            )
        panel.busy = True
        try:
            current = await self.config.member(member).last_lfg_post()
            if not current or current.get("message_id") != panel.message_id:
                return await interaction.response.send_message(
                    "Your last post changed since this panel opened. Run /lfgupdate again.",
                    ephemeral=True,
                )
            # Rebase the deltas onto the fresh read. Each delta touches only
            # the fields its form controls (None removes a setting); untouched
            # categories keep their stored values.
            record: Dict[str, object] = dict(current)
            settings = dict(record.get("settings") or {})
            if essentials is not None:
                settings["Max Players"] = essentials["max_players"]
                settings["Gamemode"] = essentials["gamemode"]
                record["lobby"] = essentials["lobby"]
                record["notes"] = essentials["notes"]
                if "randomizer" in essentials:
                    if essentials["randomizer"] is None:
                        settings.pop("Weapon Randomizer", None)
                    else:
                        settings["Weapon Randomizer"] = essentials["randomizer"]
            if optional is not None:
                for name, value in optional.items():
                    if value is None:
                        settings.pop(name, None)
                    else:
                        settings[name] = value
            # Normalize to the canonical display order.
            ordered: Dict[str, str] = {}
            for name in ("Max Players", "Gamemode", "Modded Lobby"):
                if settings.get(name) is not None:
                    ordered[name] = settings[name]
            for name in self._blank_optional_settings():
                if settings.get(name) is not None:
                    ordered[name] = settings[name]
            record["settings"] = ordered
            # Update cooldown: synchronous check-and-stamp after the awaited
            # reads, refunded (compare-and-clear) if the edit never lands.
            key = (interaction.guild.id, member.id)
            now = time.time()
            remaining = self.LFG_UPDATE_COOLDOWN_SECONDS - (now - self._update_last.get(key, 0.0))
            if remaining > 0:
                return await interaction.response.send_message(
                    f"You can update again in {max(1, round(remaining))}s.", ephemeral=True
                )
            self._update_last[key] = now

            await interaction.response.defer(ephemeral=True, thinking=True)

            channel = interaction.guild.get_channel(record.get("channel_id"))
            message = None
            if channel is not None:
                try:
                    message = await channel.fetch_message(panel.message_id)
                except discord.HTTPException:
                    message = None
            if message is None:
                if self._update_last.get(key) == now:
                    del self._update_last[key]
                panel.closed = True
                panel.stop()
                try:
                    await self.config.member(member).last_lfg_post.set({})
                except Exception:
                    log.exception("Failed to clear a stale LFG record")
                if panel.message is not None:
                    try:
                        await panel.message.edit(
                            content="Your last LFG post no longer exists. Post a new one!",
                            view=None,
                        )
                    except discord.HTTPException:
                        pass
                return await interaction.followup.send(
                    "Your last LFG post no longer exists. Post a new one with /lfg or /lfgmod!",
                    ephemeral=True,
                )

            embed = self.build_lfg_embed(
                member,
                str(record.get("lobby") or ""),
                record.get("notes"),
                region=self.get_member_region(member),
                settings=record.get("settings") or {},
                is_modded=bool(record.get("is_modded")),
                updated=True,
            )
            try:
                await message.edit(embed=embed)
            except discord.HTTPException:
                log.exception("Failed to edit LFG post %s", panel.message_id)
                if self._update_last.get(key) == now:
                    del self._update_last[key]
                return await interaction.followup.send(
                    "I couldn't update your LFG post. Please try again.", ephemeral=True
                )

            panel.record = record
            try:
                await self.config.member(member).last_lfg_post.set(record)
            except Exception:
                log.exception("Failed to persist the updated LFG record")
            if (
                panel.message is not None
                and not panel.is_finished()
                and panel.active_confirm is None
            ):
                # Never re-attach a stopped view (its buttons would render
                # live but answer with "interaction failed"), and never yank
                # a delete confirmation out from under the user.
                try:
                    await panel.message.edit(content=panel.panel_content(), view=panel)
                except discord.HTTPException:
                    pass
            try:
                await interaction.followup.send(
                    f"Updated! [Jump to your LFG]({message.jump_url})", ephemeral=True
                )
            except discord.HTTPException:
                log.exception("Failed to send the LFG update confirmation")
        finally:
            panel.busy = False

    async def handle_update_delete(
        self,
        interaction: discord.Interaction,
        panel: LFGUpdatePanelView,
        confirm_view: LFGUpdateDeleteConfirmView,
    ):
        """Delete the tracked post after confirmation and clear the record.
        No cooldown applies: the record clears, so nothing is left to spam.
        No blocklist check either, deliberately: deleting removes content,
        which a blocked member should still be able to do."""
        member = interaction.user
        if panel.busy:
            return await interaction.response.send_message(
                "Another update for this post is in flight. Please try again.", ephemeral=True
            )
        panel.busy = True
        try:
            current = await self.config.member(member).last_lfg_post()
            if not current or current.get("message_id") != panel.message_id:
                return await interaction.response.send_message(
                    "Your last post changed since this panel opened. Run /lfgupdate again.",
                    ephemeral=True,
                )
            await interaction.response.defer()
            channel = interaction.guild.get_channel((panel.record or {}).get("channel_id"))
            if channel is not None:
                try:
                    message = await channel.fetch_message(panel.message_id)
                    await message.delete()
                except discord.HTTPException:
                    # Already gone or undeletable; clear our state regardless.
                    pass
            try:
                await self.config.member(member).last_lfg_post.set({})
            except Exception:
                log.exception("Failed to clear the LFG record after deletion")
            panel.closed = True
            panel.active_confirm = None
            confirm_view.stop()
            panel.stop()
            try:
                await interaction.edit_original_response(
                    content="Your LFG post was deleted.", view=None
                )
            except discord.HTTPException:
                pass
        finally:
            panel.busy = False

    # -- The guided two-modal bridge (sticky buttons + test commands) ---------

    async def handle_lfg_stage_one(self, interaction: discord.Interaction, modal: LFGPostModal):
        """Chained stage one: validate the mandatory form, stash a draft on an
        ephemeral panel, and offer Post Now / Optional Settings."""
        member = interaction.user

        if self._is_lfg_blocked(member):
            return await interaction.response.send_message(self.LFG_BLOCKED_MESSAGE, ephemeral=True)

        lobby = str(modal.lobby_id_field.component.value or "").strip()
        if not lobby.isdecimal() or not LOBBY_ID_MIN_LENGTH <= len(lobby) <= LOBBY_ID_MAX_LENGTH:
            return await interaction.response.send_message(
                f"The Lobby ID must be {LOBBY_ID_MIN_LENGTH} to {LOBBY_ID_MAX_LENGTH} numbers.",
                ephemeral=True,
            )
        max_players, players_error = modal.read_max_players()
        if players_error:
            return await interaction.response.send_message(players_error, ephemeral=True)
        try:
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
            "notes": self.sanitize_notes(
                modal.notes_field.component.value or None, allow_links=modal.is_modded
            ),
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
        if first_to_raw and (not first_to_raw.isdecimal() or not 1 <= int(first_to_raw) <= 50):
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
            # REFACTOR_PLAN.md section 4.4 live here and in handle_fused_submit.
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
            if not draft["silent_ping"]:
                # Test posts (silent) are not update targets.
                await self._record_last_post(
                    member, message, draft["is_modded"], draft["lobby"], draft["notes"], settings
                )
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
        """The detailed path from the sticky buttons: the guided two-modal
        bridge (essentials modal, then Post Now or an Optional Settings
        modal). Same gate, same shared cooldown, and same destination as the
        streamlined slash commands."""
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
            "There are two ways to post a lobby. Everything stays private to "
            f"you until your post goes live in <#{self.NEW_LFG_CHANNEL_ID}>.\n\n"
            "**Streamlined (slash commands):** one quick form on a single "
            "page. Fastest once you know your way around.\n"
            "**/lfg**: post a vanilla lobby.\n"
            "**/lfgmod**: post a modded lobby. Please list your mods in the "
            "Notes field! To set First To on a modded lobby, use the Modded "
            "LFG button below.\n"
            "**/lfgupdate**: manage your last posted LFG. Edit its details or "
            "delete it. No new ping is sent. The Update Last LFG button below "
            "does the same.\n\n"
            "**Detailed (buttons below):** a guided setup. Fill in the "
            "essentials first, then choose Post Now or add optional settings "
            "(First To, Lobby Type, Friendly Fire and more) on a second page. "
            "Great if it is your first time posting.\n\n"
            "**/lfgpings**: toggle the LFG ping role to get notified about new "
            "lobbies.\n\n"
            f"{region_line}"
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

        The sticky explains the streamlined slash commands (/lfg, /lfgmod,
        /lfgpings, /lfgupdate) and carries three buttons: LFG and Modded LFG
        open the detailed guided flow (an essentials modal, then Post Now or
        an Optional Settings page), and Update Last LFG opens the /lfgupdate
        management panel. Administrator only. Cannot be enabled in a channel
        where the legacy sticky is active.
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
        optional_settings: Optional[Dict[str, Optional[str]]] = None,
        fused: bool = False,
    ):
        """Shared front door for the /lfg command family: blocklist ->
        region gate -> cooldown peek -> the modal. fused=True opens the
        streamlined single modal (slash commands); fused=False opens stage one
        of the guided two-modal bridge (sticky buttons, test commands). All
        feedback is ephemeral."""
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
        if fused:
            modal = LFGFusedModal(
                self,
                destination_id=destination_id,
                is_modded=is_modded,
                enforce_gate=enforce_gate,
                enforce_cooldown=enforce_cooldown,
                silent_ping=silent_ping,
            )
        else:
            modal = LFGPostModal(
                self,
                destination_id=destination_id,
                is_modded=is_modded,
                enforce_gate=enforce_gate,
                enforce_cooldown=enforce_cooldown,
                silent_ping=silent_ping,
                optional_settings=optional_settings or self._blank_optional_settings(),
            )
        await interaction.response.send_modal(modal)

    @app_commands.command(name="lfg", description="Post an LFG to #new-lfg")
    @app_commands.guild_only()
    async def slash_lfg(self, interaction: discord.Interaction):
        """Vanilla LFG, streamlined: one modal with every setting, posted on
        submit. The guided step-by-step alternative lives on the sticky
        buttons. The post is tagged Modded Lobby: No; moddedness is derived
        from which command was used, never asked in the form."""
        await self._launch_lfg_modal(
            interaction,
            destination_id=self.NEW_LFG_CHANNEL_ID,
            is_modded=False,
            enforce_gate=True,
            enforce_cooldown=True,
            silent_ping=False,
            fused=True,
        )

    @app_commands.command(name="lfgmod", description="Post a modded LFG to #new-lfg")
    @app_commands.guild_only()
    async def slash_lfgmod(self, interaction: discord.Interaction):
        """Same streamlined pipeline as /lfg for modded lobbies, with Max
        Players as a numerical input (2 to 10), Gamemode as its own radio
        group, and no First To field (available via the Modded LFG button);
        the post is tagged Modded Lobby: Yes."""
        await self._launch_lfg_modal(
            interaction,
            destination_id=self.NEW_LFG_CHANNEL_ID,
            is_modded=True,
            enforce_gate=True,
            enforce_cooldown=True,
            silent_ping=False,
            fused=True,
        )

    @app_commands.command(name="testlfg", description="Admin test of the LFG flow. Posts to the log channel")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def slash_testlfg(self, interaction: discord.Interaction):
        """Tester for the guided two-modal bridge (the sticky buttons' flow,
        vanilla variant): the mandatory modal, then an ephemeral panel offering
        Post Now or an Optional Settings modal. Admin-only, posts to the log
        channel, no region gate, no cooldown, silent role mention."""
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
        """Tester for the guided two-modal bridge (the sticky buttons' flow,
        modded variant): the mandatory modal, then an ephemeral panel offering
        Post Now or an Optional Settings modal. Admin-only, posts to the log
        channel, no region gate, no cooldown, silent role mention."""
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

    async def open_update_panel(self, interaction: discord.Interaction):
        """Open the ephemeral /lfgupdate management panel. Shared by the
        slash command and the sticky's Update Last LFG button (both slash
        and component interactions accept the same ephemeral response)."""
        if self._is_lfg_blocked(interaction.user):
            return await interaction.response.send_message(self.LFG_BLOCKED_MESSAGE, ephemeral=True)
        record = await self.config.member(interaction.user).last_lfg_post()
        if not record or not record.get("message_id"):
            return await interaction.response.send_message(
                "You have no LFG post to update. Post one with /lfg or /lfgmod first!",
                ephemeral=True,
            )
        panel = LFGUpdatePanelView(self, record, interaction.guild.id)
        await interaction.response.send_message(
            panel.panel_content(), view=panel, ephemeral=True
        )
        try:
            panel.message = await interaction.original_response()
        except discord.HTTPException:
            panel.message = None

    @app_commands.command(name="lfgupdate", description="Manage your last posted LFG")
    @app_commands.guild_only()
    async def slash_lfgupdate(self, interaction: discord.Interaction):
        """Opens an ephemeral management panel for your last posted LFG:
        edit the essentials (Lobby ID, players, gamemode, notes), adjust the
        optional settings (including First To, even on modded posts), or
        delete the post. Edits never re-ping; each edit is subject to the
        short update cooldown."""
        await self.open_update_panel(interaction)

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
    # here would duplicate both. Modal errors are handled by every modal's
    # own on_error handler.

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        """Purge the stored cooldown timestamp and last-post record (the
        end-user data this cog keeps)."""
        all_members = await self.config.all_members()
        for guild_id, members in all_members.items():
            if user_id in members:
                await self.config.member_from_ids(guild_id, user_id).clear()
        self._cooldown_cache = {k: v for k, v in self._cooldown_cache.items() if k[1] != user_id}
        self._update_last = {k: v for k, v in self._update_last.items() if k[1] != user_id}
        self._ping_toggle_last.pop(user_id, None)
