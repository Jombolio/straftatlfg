# STRAFTAT LFG — Interactive Panel Refactor: Implementation Plan

**Repo:** `C:/Users/jamie/Documents/Project/straftatlfg` (Red-DiscordBot cog package `straftatlfg`)
**Target runtime:** Red 3.5 stable — 3.5.24 at time of writing (verified to bundle discord.py 2.7.1). Every API feature used in core scope (app commands, `send_modal`, `Modal`/`TextInput`, `guild_only`, ephemeral defer/followup) is available from discord.py 2.0 / Red 3.5.0 (2023-05-04, when `[p]slash` shipped per the 3.5.0 changelog). `min_bot_version` is pinned at **3.5.21** (dpy 2.6.2) as of Revision 6: the modal's Label-wrapped string selects (`discord.ui.Label`, select `required=`) genuinely require dpy 2.6 — this floor also covers everything the earlier conservative 3.5.10 pin anticipated (`DynamicItem`, §9.5; `allowed_contexts`, §4.2).

> **Revision 2 — 2026-07-14.** Stakeholder direction: the raw-reaction region system is **permanent, not legacy**, and must be integrated into the new design rather than deprecated. Consequences threaded through this document: the panel's `RegionSelect` is dropped (the Hub carries exactly two buttons); a new **region gate** requires a region role to post an LFG, deferring role-less members to the reaction message's channel; new guild config `region_channel_id` + `region_required`; the LFG embed gains a **Region** field; the reaction listeners are hardened (atomic role swap, Config-driven regions) instead of deleted; PR 4 is re-cut from "region cutover + purge" to "region-system integration & hardening"; `purge-legacy` shrinks to sticky-era keys only — `region_message_ids` is live data and is never purged; PR 5 no longer touches the listeners or `!lfg-region`. A select-based region picker moves to Future Extensions (§9). *(Superseded in part by Revision 3 below: the PR 3–5 ladder and `purge-legacy` structure, and the `region_channel_id`/`region_required` guild-config keys described here, were subsequently dropped — the plan now ships PR 1 + optional PR 2 only, with the entire config apparatus moved to §9. The "region system is permanent" direction itself stands.)*

> **Revision 3 — 2026-07-14.** Stakeholder direction supersedes the panel-first design: the new system is **slash-first** — `/lfg` opens an interactive modal, **every piece of invoker-visible feedback is ephemeral** ("only the author can see"), and the resulting post is **hardcoded to #new-lfg** (`1526690875470774312`, the `NEW_LFG_CHANNEL_ID` constant already in `lfg.py:16`) for now. The **legacy `!lfg` system is FROZEN, byte-for-byte, and runs in parallel**: `!lfg`, `!testlfg`, `!lfg-role`, the sticky instructional message, their decorator cooldowns, channel gates, validation, and their posting to #legacy-lfg all remain exactly as currently written — this plan schedules **zero modifications** to those code paths. No panel, no sticky changes, and **no config migration** in core scope (the entire `lfgset`/guild-config apparatus from Revision 2 moves to Future Extensions); the only Config change is one member-scope cooldown key. Channel identities are now threaded throughout: `1310689512615051345` = **#straftchat** (the server's main chat — the reason chat clutter matters), `1284536580941287598` = **#legacy-lfg** (the legacy feed), `1526690875470774312` = **#new-lfg** (the new feed), and `1269794533923754089` (`TEST_CHANNEL_ID`) is a **log channel** per the maintainer's own comment — not a clean staging channel. Legacy retirement/unification is a future decision (§9), not a scheduled phase. *(Superseded in part by Revision 4 below: one exemption from the freeze was subsequently applied — `_process_lfg` now sends to its passed `channel_id` parameter, so `!testlfg` posts to the log channel (`TEST_CHANNEL_ID`) rather than #legacy-lfg. `!lfg` is byte-identical; the freeze stands everywhere else.)*

> **Revision 4 — 2026-07-14.** Stakeholder exemption from the legacy freeze: **`!testlfg` must only ever post to the log channel** (`TEST_CHANNEL_ID` = `1269794533923754089`). The one-line fix is **already applied** to `lfg.py`: `_process_lfg` now sends to its already-passed `channel_id` parameter (`lfg.py:130`) instead of the hardcoded #legacy-lfg literal — `!lfg` (which passes `LFG_CHANNEL_ID`) is byte-identical, and `!testlfg` (which passes `TEST_CHANNEL_ID`) routes to the log channel. This is the sole applied modification to legacy code; the freeze stands everywhere else.

> **Revision 5 — 2026-07-14.** PR 1 is implemented (see §5), with one stakeholder addition: **`/testlfg`** — an **Administrator-only** slash command (registration-level `@app_commands.default_permissions(administrator=True)` plus a runtime `guild_permissions.administrator` check) that opens the **same `LFGPostModal` as `/lfg`** but posts to the **log channel** (`TEST_CHANNEL_ID`). It deliberately skips the region gate and the cooldown (admins test freely; the shared `/lfg` cooldown store is never touched), and it sends with `AllowedMentions.none()` so the role mention renders without notifying anyone. The legacy prefix `!testlfg` remains separate and frozen — Legacy/Prefix and New/Slash are different systems.

> **Revision 6 — 2026-07-14.** The modal gains lobby settings. Stakeholder spec: mandatory **Maximum players (2-4)**, **Gamemode (FFA/Teams)**, **Modded Lobby (Yes/No)**; optional **First to (1-50)**, **Lobby Type (Public/Invite Only/Private)**, **Friendly Fire (Enabled/Disabled)**, **Allow Mid-Match Joining (Yes/No)**, **Weapon Randomizer (Fully Random/Custom/No)**, **Enemy Outlines (Enabled/Disabled)**. A fact-check confirmed Discord modals remain hard-capped at **5 top-level components** (dpy 2.7.1 enforces it in `Modal.add_item`), so the stakeholder-approved layout is **slash options + one modal**: the six optional settings are optional `/lfg` (and `/testlfg`) command options — Discord natively enforces the choice lists and the 1-50 range via `app_commands.Range` — and the single 5-field modal carries the mandatory set: Lobby ID (text), Maximum players / Gamemode / Modded lobby (required string selects, Label-wrapped per the dpy 2.6+ API, native red-asterisk enforcement), and Notes (optional paragraph). The embed renders the three mandatory settings always and each optional setting only when the invoker set it. **`min_bot_version` bumps 3.5.10 → 3.5.21** (Red 3.5.21 = dpy 2.6.2, the floor for `discord.ui.Label` + selects-in-modals with `required=`).

> **Revision 7 — 2026-07-14.** The slash family splits by moddedness: **`/lfgmod`** is a carbon copy of `/lfg` (same modal, same region gate, same **shared** cooldown store — one post per 60s across both, same #new-lfg destination) and **`/testlfgmod`** joins `/testlfg` as its admin-only tester (log channel, no gate/cooldown, silent mention). **The Modded Lobby select is removed from the modal on all commands** — moddedness is derived from which command was invoked (the modal title switches to "Post a Modded LFG"), and because feed readers can't see the invoking command, the embed keeps a **derived** `Modded Lobby: Yes/No` field. The freed 5th modal slot becomes an **optional Weapon Randomizer select** (Fully Random/Custom/No, `required=False`; untouched = not rendered), and `weapon_randomizer` is removed from the slash options (now five: `first_to`, `lobby_type`, `friendly_fire`, `mid_match_joining`, `enemy_outlines`). All four commands route through one `_launch_lfg_modal` front door.

> **Revision 8 — 2026-07-14.** Command renames per stakeholder direction: `/modlfg` → **`/lfgmod`** and `/testmodlfg` → **`/testlfgmod`** (names updated throughout this document, including in the Revision 7 note). Side benefit: the whole member-facing family now shares the `lfg` prefix, so typing `/lfg` in Discord's picker surfaces `lfg`, `lfgmod`, and `lfgpings` together. Also in this revision: the Lobby ID field is bounded to **8-13 characters** (client-enforced `min_length`/`max_length` plus a server-side re-check after whitespace stripping), the modded commands' Notes field encourages listing the mods being run, and all em-dashes were removed from bot-facing strings. A distinct cyan embed color for modded posts was briefly added and then dropped by stakeholder direction: the cog sticks to discord.py's built-in palette (no hex codes), so all posts share the standard rule (booster-green, otherwise blue) and moddedness is conveyed by the `Modded Lobby` field.

> **Revision 9 — 2026-07-14.** The test commands trial a **chained two-modal flow**; `/lfg` and `/lfgmod` stay exactly as-is (slash options + single modal) per stakeholder direction. Discord cannot open a modal from a modal submission (verified), so the chain uses a button bridge: `/testlfg` or `/testlfgmod` → the mandatory modal (unchanged) → an **ephemeral draft panel** with `Post Now` and `Optional Settings` buttons → `Post Now` posts immediately, or `Optional Settings` opens a **second modal** carrying the five optional settings (First To as a server-validated 1-50 text input, plus the four optional selects), which posts on submit. The test commands **lose their slash options** (stage two replaces them). The draft lives on the panel's view instance (5-minute timeout, lost on restart; the admin re-runs the command), with synchronous double-post guards (`finalizing`/`posted`) so a double-clicked Post Now or a Post Now racing a still-open stage-two modal produces exactly one post. The chained path currently assumes no gate and no cooldown (true for the test commands); promoting it to `/lfg`/`/lfgmod` would require moving the §4.4 cooldown commit into the finalize step.

**Base design (Revision 3):** two parallel systems. The frozen legacy prefix system keeps serving members exactly as today. A purely **additive** slash system — `/lfg` → region gate → cooldown peek → modal → race-correct Config-backed cooldown → embed post to #new-lfg → ephemeral confirmation — ships alongside it, plus `/lfgpings` as the ephemeral ping-role toggle. The Revision 2 mechanics that survive do so **for the new system only**: the race-correct check-and-commit cooldown with refund-on-failure (§4.4), the region gate with authoritative re-check (§4.4), the Region embed field, the 5s ping-toggle throttle, and the `info.json` corrections. The reaction-role region message remains the permanent region-selection surface (Revision 2 direction stands) and is untouched in core scope; one optional, tiny hardening PR (atomic role swap) is the only change even proposed near it.

---

## 1. Overview & Goals

### Goals

1. **A slash-first posting flow**: members type `/lfg`, get an interactive modal (Lobby ID + notes), and the post lands in **#new-lfg**. **Everything the invoker sees is ephemeral** — the gate deferral, validation errors, cooldown refusals, and the success confirmation with jump link are all visible only to the author. #straftchat (the main chat, where `!lfg` must be typed today) stays uncluttered by the new flow: no command message, no `delete_after` error litter, no ✅-reaction bookkeeping.
2. **The legacy system is frozen and parallel.** `!lfg` (posting to #legacy-lfg from #straftchat), `!testlfg`, `!lfg-role`, the sticky instructional embed, the decorator cooldowns, and the reaction-region listeners keep working **exactly as currently written** — not one line of those code paths changes in core scope (two narrow exceptions: the already-applied Revision 4 `!testlfg` routing fix, and the optional PR 2 hardening of `on_raw_reaction_add`, §4.5). Members who prefer the typed flow, or whose mobile client fails to open a modal, lose nothing.
3. **Region-gated, region-tagged posting — on the new surface only**: `/lfg` requires holding one of the `REGION_ROLES` (`lfg.py:21-30`); role-less members get an ephemeral deferral pointing at the region channel. The new embed carries a **Region** field derived from the host's region role at post time. The gate is always-on for `/lfg` (a config toggle arrives with the config-ification future phase, §9). Legacy `!lfg` has **no** region gate — legacy is frozen.
4. **Abuse controls on the new surface, restart-safe**: a 60s per-member cooldown backed by member-scope Red Config (survives restarts; consumed only on successful posts; refunded only when a committed submission fails **before** the post lands in #new-lfg — a live post never gets its cooldown erased, §4.4), numeric lobby-ID validation server-side in the modal's `on_submit` (Discord has no client-side numeric validation — verified fact), and the same URL/masked-link sanitization as legacy, implemented in a **new helper** (`_process_lfg` is not touched). `/lfgpings` gets a 5s in-memory throttle.
5. **Hardcode-for-now**: the new system reads the existing constants directly — `LFG_ROLE_ID`, `NEW_LFG_CHANNEL_ID`, `REGION_ROLES` — plus exactly **two** new class constants: `LFG_COOLDOWN_SECONDS = 60` (the cooldown window) and `REGION_CHANNEL_ID` (the only new *ID* constant, §6). This matches both the stakeholder instruction ("Hardcode `/lfg` to use `1526690875470774312 # new-lfg` for now") and the codebase's established style. Config-ification is a future extension, not core scope.
6. Executable by **one developer in one small additive PR** (plus one optional tiny hardening PR); every mechanism is a boring, documented discord.py / Red pattern with verified citations.

### Non-Goals (explicitly out of scope)

- **No modification to any legacy code path** beyond the Revision 4 exemption. No shared pipeline rewiring, no config-ification of legacy hardcoded ids, no tip lines appended to `!lfg`, no decorator removal, no region gate on legacy, no sticky retirement or copy changes. The formerly ignored-`channel_id` bug in `_process_lfg` (`lfg.py:97` accepts it; `:130` used to hardcode #legacy-lfg, silently misrouting `!testlfg`) is **fixed by stakeholder direction** — `:130` now sends to the passed `channel_id`, the sole applied change to a frozen path.
- **No panel, no persistent views, no sticky engine changes.** The Revision 2 Hub panel is shelved (§9). No message-attached components exist in core scope, so no `custom_id` scheme, no `bot.add_view`, no restart-persistence machinery for views is needed at all (§4.1).
- **No config migration.** No `lfgset` group, no guild-config id keys, no `region_channel_id`/`region_required`/`regions` config keys, no `region_message_channels`, no schema versioning. The only Config change is `config.register_member(last_lfg_ts=0.0)` (§6).
- **No cooldown unification.** Legacy keeps its in-memory `@commands.cooldown` decorators; the new system keeps its Config store. They are **independent by design** during the transition (§4.4) — a member can post once via `!lfg` and once via `/lfg` inside the same 60s window. Accepted transition cost, resolved when legacy retires (future, §9).
- **No lobby lifecycle** (Join/Edit/auto-expiry) — unchanged rejection from Revision 2; commit `8e219a6 "30 min removal"` remains prior negative signal on timer automation (§9).
- **No FAQ rework.** `faq`/`faqnew`/`faqdel`/`faqlist`/`faqhelp` (`lfg.py:235-425`) are untouched, including their inline blocked-channel literals.
- **No retirement of the reaction listeners.** `on_raw_reaction_add`/`on_raw_reaction_remove` (`lfg.py:469-554`) and `!lfg-region` (`:427-467`) are permanent infrastructure. Core scope does not touch them; optional PR 2 hardens one internal detail (§4.5).

---

## 2. Current State (references into `lfg.py`)

Single 572-line module, `class LFG(commands.Cog)`. Everything in this table is **frozen** unless a row says otherwise:

| Feature | Where | Status under this plan |
|---|---|---|
| Hardcoded IDs | Class constants `lfg.py:14-18`, now name-commented: `LFG_ROLE_ID` `:14`, `LFG_CHANNEL_ID = 1284536580941287598  # legacy-lfg channel` `:15`, `NEW_LFG_CHANNEL_ID = 1526690875470774312  # new-lfg channel` `:16`, `TEST_ROLE_ID` `:17`, `TEST_CHANNEL_ID = 1269794533923754089 # log channel` `:18`. Inline literals persist: #straftchat `1310689512615051345` at `:98` (and in sticky copy `:63`), booster role `1387554310832918528` at `:117`, FAQ blocked channels at `:241,275,295,311,386` (the former #legacy-lfg literal at `:130` was replaced by the `channel_id` parameter — Revision 4) | Frozen. The new system **reads** `LFG_ROLE_ID`, `NEW_LFG_CHANNEL_ID`, `REGION_ROLES`, and the booster role literal (§6); it adds two constants (`LFG_COOLDOWN_SECONDS`, `REGION_CHANNEL_ID`, §6) and never edits the existing ones |
| `!lfg` posting | `lfg()` `:146-156` → `_process_lfg()` `:97-144` (channel gate on #straftchat `:98-100`, digit check `:102-104`, sanitize regexes `:108-109`, easter-egg title `:116`, booster color `:117`, embed `:119-126`, send `:130-139`, ✅ reaction `:141-144`) | Frozen except the Revision 4 exemption: `:130` now sends to the passed `channel_id` (formerly a hardcoded #legacy-lfg literal). `!lfg` passes `LFG_CHANNEL_ID`, so its behavior is byte-identical |
| `!testlfg` | `:158-170`, gated on `TEST_ROLE_ID` `:166` | Frozen; **now correctly posts to the log channel** (`TEST_CHANNEL_ID`) via the Revision 4 fix — it previously misrouted to #legacy-lfg |
| `!lfg-role` toggle | `:172-195` | Frozen; `/lfgpings` is added **alongside** it, not replacing it |
| Sticky system | `_handle_sticky()` `:47-77` (instructional embed `:60-69`), `on_message` `:79-95`, `sticky-toggle` `:197-233` | Frozen. Standing footgun documented, not fixed: the anti-loop guard matches the embed **title string** `"How to use the LFG system"` at `:92` — any title edit causes an infinite delete/repost loop (warning threaded into §9 next to the "update sticky copy" idea) |
| Region reaction roles | `REGION_ROLES` `:21-30`, `_region_role_map()` `:43-45`, `lfg-region` `:427-467` (message-id append `:461-462`), `on_raw_reaction_add` `:469-525` (`add_roles` `:495`, `remove_roles` `:508`, strip-reactions loop `:512-525`), `on_raw_reaction_remove` `:527-554` | **Permanent, untouched in core scope.** The new `/lfg` gate reads the same `REGION_ROLES` constant — single source of truth for the listeners, the gate, and the embed's Region field. Optional PR 2 replaces the `add_roles`+`remove_roles` pair with one atomic `member.edit` (closes the double-region window; see §4.5 for the failure-mode caveats) |
| Cooldowns | `@commands.cooldown(1, 60, BucketType.user)` `:148,160` + shared error handler `:556-572` (`CommandOnCooldown` branch `:564-565`) | Frozen for legacy. The new system uses its **own, independent** member-scope Config timestamp store (§4.4) |
| FAQ system | `:235-425` | Frozen, out of scope |

Config (`:34-41`): identifier `2736452831`, `force_registration=True`, guild keys `active_sticky_channels`, `sticky_cache`, `faqs`, `region_message_ids`. All stay registered exactly as-is — the legacy features use them. The one addition is member-scope (§6).

`info.json`: `min_bot_version: "0.1"` (wrong) and `end_user_data_statement: "This cog does not store any End User Data."` (becomes false the moment we store member cooldown timestamps) — both fixed in PR 1, the same PR that adds the cooldown store and the EUD deletion hook (§5).

---

## 3. Target UX

### 3.1 Post an LFG via `/lfg` (happy path)

1. Member types `/lfg` (vanilla) or `/lfgmod` (modded — otherwise identical, Revision 7) anywhere it's usable (recommendation: everywhere — see §4.2 on channel restriction), optionally fills any of the five **optional lobby settings** right in the command picker (`first_to` 1-50, `lobby_type`, `friendly_fire`, `mid_match_joining`, `enemy_outlines` — all dropdown/range options Discord validates client-side, §4.2a), and hits Enter.
2. **Region gate** (synchronous — pure role-set check against the `REGION_ROLES` constant on `interaction.user.roles`; safe because in guild interactions `interaction.user` is a `discord.Member` with `.roles` populated — verified fact): member holds none of the eight region roles → ephemeral `You need a region role to post — pick one in <#REGION_CHANNEL_ID> first.` Nothing else happens, nothing is consumed, the modal never opens. If `REGION_CHANNEL_ID` is unset (`None`/`0`), the copy degrades to `You need a region role to post — ask an admin where to pick one.` — never a traceback (§4.4).
3. Cooldown **peek** (no consume; one fast memory-cached Config read that also warms the cache): still cooling → ephemeral `You can post again in Ns.`
4. Otherwise `interaction.response.send_modal(LFGPostModal(...))` — a valid **initial** response to an application-command interaction (Discord interaction callback type 9, MODAL, is available for APPLICATION_COMMAND interactions — verified fact). Modal **"Post an LFG"** (or **"Post a Modded LFG"** from `/lfgmod`) — exactly at the 5-component cap (§4.3): `Lobby ID` (short text, required), `Max Players` (required select: 2/3/4), `Gamemode` (required select: FFA/Teams), `Weapon Randomizer` (**optional** select: Fully Random/Custom/No), `Notes` (paragraph, optional, `max_length=200`). Discord blocks submission until the required fields are filled; there is no modded field — moddedness comes from the command.
5. On submit (`on_submit` receives a **fresh MODAL_SUBMIT interaction** with its own 3s response window — verified fact):
   - `lobby_id.strip()` fails `.isdigit()` or the 8-13 length re-check → ephemeral `The Lobby ID must be 8 to 13 numbers.` **Cooldown untouched** — the member re-runs `/lfg` immediately. (Typos never burn the cooldown; validation must be server-side per the verified modal facts — the client enforces required/length only.)
   - **Region gate re-checked authoritatively** (the modal could have sat open across a role removal): fails → same ephemeral deferral, cooldown untouched. On success, the member's region tuple is captured here for the embed.
   - Notes sanitized by the **new** `sanitize_notes` helper — the same two regexes as `lfg.py:108-109` (masked links unwrapped, raw URLs stripped), reimplemented verbatim in the helper; `_process_lfg` is not touched.
   - Cooldown **check-and-commit**: awaited `check_cooldown` (Config cache-warm) then synchronous `commit_cooldown_sync` — no `await` between check and stamp (§4.4). A racing second submit sees the stamp and is refused ephemerally.
   - `await interaction.response.defer(ephemeral=True, thinking=True)` — beats the 3s token deadline before the network sends.
   - Embed built by the **new** `build_lfg_embed` helper, same look as legacy (`lfg.py:116-126` reproduced): 1/1000 `Euuuuuugh!` easter-egg title, green if the member holds the booster role else blue, `Lobby ID` + `Host` inline fields, avatar footer — **plus a new inline `Region` field** showing `{emoji} {label}` (e.g. `2️⃣ EU`) from the gate-captured region, **the three mandatory settings** (Max Players, Gamemode, Modded Lobby — always present), **and each optional setting the invoker actually set** (First To, Lobby Type, Friendly Fire, Mid-Match Joining, Weapon Randomizer, Enemy Outlines). Inline fields flow three per row; worst case is 12 fields, well under Discord's 25-field embed cap.
   - Sent to **hardcoded `self.NEW_LFG_CHANNEL_ID` (#new-lfg)** with the LFG role ping and `AllowedMentions(roles=[role])`. **The moment this send succeeds, the per-invocation committed-stamp marker is cleared** — the post is live, and nothing after this point may trigger a refund (§4.4).
   - Stamp persisted to member Config; then ephemeral followup: `Posted! [Jump to your LFG](jump_url) — Lobby ID: 12345` (ID echoed as copyable text). Failures in these two post-send steps log and skip the refund — worst case the in-memory cooldown holds for the session and a restart grants amnesty on the unpersisted stamp, matching legacy behavior (§4.4).
6. Any failure after the cooldown commit but **before the #new-lfg send succeeds** (role/channel resolves to `None`, send raises `Forbidden`) → cooldown **refunded** (compare-and-clear, §4.4), ephemeral "not configured / no permission — contact an admin." A failure *after* the send succeeded never refunds — the cooldown of a live post is never erased.

**Every message in this flow is ephemeral.** The only non-ephemeral artifact of a `/lfg` invocation is the post in #new-lfg. That is the stakeholder's "only the author can see" requirement, satisfied end-to-end: no command message in chat, no public errors, no clutter in #straftchat.

### 3.2 Gate deferral IS the onboarding

Member without a region role runs `/lfg` → ephemeral pointer at the region channel → they react once on the permanent reaction message → run `/lfg` again → post immediately (no cooldown was consumed, nothing to wait out).

### 3.3 Toggle LFG pings via `/lfgpings`

1. Member types `/lfgpings` (declared with `@app_commands.guild_only()`, exactly like `/lfg` — §4.2).
2. 5-second per-user in-memory throttle, checked synchronously first → ephemeral `Slow down — try again in a moment.` as the initial response if mashed.
3. Otherwise defer ephemeral, `LFG_ROLE_ID` added or removed (`reason="LFG ping toggle (/lfgpings)"`) → ephemeral `You will now be pinged for LFG posts.` / `You will no longer be pinged.`
4. `discord.Forbidden` → ephemeral hierarchy error. `!lfg-role` keeps working untouched alongside it.

### 3.4 Legacy flows — unchanged (explicit)

- `!lfg <lobby_id> <notes>` in #straftchat → 60s decorator cooldown, channel gate, digit check, sanitize, embed, post to **#legacy-lfg** with role ping, ✅ reaction on the command message. Byte-identical to today.
- `!testlfg` → same, gated on `TEST_ROLE_ID`, posting to the **log channel** (`TEST_CHANNEL_ID` — Revision 4 fix; it previously misrouted to #legacy-lfg).
- `!lfg-role` → toggles `LFG_ROLE_ID` with the `delete_after` replies and reactions as today.
- Sticky instructional embed → still reposts in active channels after every message; `sticky-toggle` unchanged.
- Region reactions → still assign/remove roles exactly as today.

**Consequence, stated plainly:** the cooldowns are independent, so a member can post once via `!lfg` (→ #legacy-lfg) and once via `/lfg` (→ #new-lfg) inside the same 60s window. This is an accepted transition cost — two feeds, two limiters — resolved when legacy retires (future decision, §9).

---

## 4. Architecture

### 4.1 Module layout

No new modules. `views.py` from Revision 2 is dropped: **no message-attached components exist in core scope**, so no persistent views, no `custom_id` namespace, no `bot.add_view` in `cog_load`, and no view lifecycle in `cog_unload` are needed at all. The only `discord.ui` class is `LFGPostModal`, which resolves in-session (persistence machinery applies to views, not modals — verified fact; a member with a modal open across a restart gets one "interaction failed" and re-runs `/lfg`; accepted). Everything lands in `lfg.py`:

```
straftatlfg/
  __init__.py      # unchanged
  info.json        # min_bot_version + end_user_data_statement updated (PR 1)
  lfg.py           # frozen legacy code + the additive new-system block
```

**Type-hint note for the new code (PR 1):** all new signatures use `typing.Optional` / `typing.Tuple` / `typing.Dict` spellings, **not** PEP 604 unions (`X | None`) or builtin-generic subscripts (`tuple[...]`). Red 3.5.x's `python_requires` floor is 3.8.1, and PEP 604 unions evaluate — and raise `TypeError` — at function-definition time on Python < 3.10, so an install that legitimately satisfies `min_bot_version` could otherwise fail to *load* the cog. No `from __future__ import annotations` is added: the current `lfg.py` has none, and the module top stays untouched under the freeze.

### 4.2 Slash-command mechanics (all facts verified against Red 3.5 / dpy 2.4+)

- **Declaration:** `from redbot.core import app_commands` (re-exports `discord.app_commands`); `@app_commands.command(name="lfg", description="Post an LFG to #new-lfg")` and `@app_commands.command(name="lfgpings", ...)` on async methods **inside the existing `commands.Cog`**, callbacks `(self, interaction: discord.Interaction)`. `CogMeta` collects them into `__cog_app_commands__`; `Bot.add_cog` auto-adds them to the tree.
- **Not hybrid — deliberately.** `commands.hybrid_command` exists in Red 3.5, but a hybrid derives its slash options from the annotated function signature and its text-command path has no interaction, so a modal cannot be the primary UX for both halves. A plain app command whose callback immediately calls `interaction.response.send_modal(...)` is the correct, supported slash→modal pattern (verified: MODAL is a valid initial callback for APPLICATION_COMMAND interactions; not available for MODAL_SUBMIT — so the modal's own submit is answered with `defer(ephemeral=True)` + followup, never another modal).
- **Coexistence:** prefix `!lfg` and slash `/lfg` live in entirely separate namespaces (message content vs. Discord's application-command API) — same cog, zero conflict. The name `lfg` passes Discord's CHAT_INPUT name rules (`^[-_'\p{L}\p{N}…]{1,32}$`, lowercase). A duplicate slash name from another loaded cog would raise `CommandAlreadyRegistered` in `RedTree.add_command` — none exists here.
- **Disabled by default — owner-only enablement.** Red stores app commands internally until enabled. The **bot owner** (the entire `[p]slash` group is `@commands.is_owner()` — guild admins cannot run it) must run:
  - `[p]slash enable lfg` and `[p]slash enable lfgpings` (or `[p]slash enablecog LFG` — cog name is case-sensitive),
  - then `[p]slash sync` (changes take no effect on Discord until sync; "should be run sparingly").
  - `[p]slash list` shows enabled/disabled state with pending-sync markers (`-`, `+`, `++`). Users may need Ctrl+R in the Discord client to see new commands.
- **Limits:** 100 global CHAT_INPUT commands per app; Red enforces this (`CommandLimitReached` on `[p]slash enable`, `SLASH_CAP = 100` pre-check on `enablecog`). Two commands are nowhere near it.
- **DM blocking:** `@app_commands.guild_only()` on **both** commands — `/lfg` *and* `/lfgpings` (server-side, sent to Discord — no runtime check or error handler fires). dpy 2.4's `@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)` is the equivalent modern spelling; either satisfies the Red 3.5.10 floor.
- **Per-channel restriction without code:** admins with Manage Guild + Manage Roles can restrict `/lfg` per role/user/**channel** via Server Settings → Integrations → per-command overwrites; commands are hidden from members who can't use them. Documented as available; **recommendation: leave `/lfg` usable everywhere** — all invoker-visible output is ephemeral and the post lands in #new-lfg regardless, so there is no clutter to prevent.
- **Callback error handling: rely on Red's built-in.** No `cog_app_command_error` handler is defined — review verified that discord.py's `CommandTree._call` invokes both a cog handler *and* `tree.on_error` unconditionally, and Red's `RedTree.on_error` already logs the traceback (setting `bot._last_exception`) and sends its own ephemeral error message. A cog handler would therefore duplicate both the log entry and the ephemeral reply, with no supported way to suppress the tree handler. (Modal submission errors are handled separately by `LFGPostModal.on_error`, §4.3 — modals are not covered by the tree handler.)
- **Unload/reload:** nothing special needed. dpy's `Cog._inject` calls `bot.tree.add_command` on add and `Cog._eject` calls `bot.tree.remove_command` on remove — automatic for class-based app commands. Red persists the enabled state across reloads, and Discord-side registrations only change on `[p]slash sync`, so load/unload/reload cycles cannot create duplicates. **Do not call `tree.sync()` from the cog** — syncing is owner-controlled.
- **Cooldown decorator explicitly not used:** `@app_commands.checks.cooldown` exists (dpy 2.0+) but its state is a plain in-process dict inside the decorator closure — resets on every restart, exactly the amnesty window we're closing. The Config-backed timestamp store (§4.4) is the mechanism.

### 4.3 `LFGPostModal`

`discord.ui.Modal`, title `Post an LFG` — exactly **5 top-level components** (the Discord hard cap), every one Label-wrapped (`discord.ui.Label(text=..., component=...)`; selects **must** be Label-wrapped or Discord rejects the modal — verified fact):
1. **Lobby ID** — `TextInput`, short, required, `min_length=8`, `max_length=13`. The client enforces required/length but **not** numeric format (verified fact), hence the server-side digit check, which also re-checks the 8-13 length after stripping whitespace (padded input could otherwise sneak under the client-enforced minimum).
2. **Max Players** — string select, required, options `2` / `3` / `4`.
3. **Gamemode** — string select, required, options `FFA` / `Teams`.
4. **Weapon Randomizer** — string select, **optional** (`required=False`; an untouched optional select submits `values=[]` — verified fact), options `Fully Random` / `Custom` / `No` (Revision 7 — took the slot freed by the removed Modded Lobby select).
5. **Notes** — `TextInput`, paragraph, `required=False`, `max_length=200`. On `/lfgmod` and `/testlfgmod` the field's copy encourages listing the mods used (description: "Optional, but please list the mods your lobby is running!", matching placeholder), reworded per-instance in the modal constructor.

Moddedness is **derived, never asked**: `/lfgmod`/`/testlfgmod` construct the modal with `is_modded=True` (per-instance title override "Post a Modded LFG") and the embed always carries the derived `Modded Lobby: Yes/No` field so feed readers can tell — they can't see which command was used.

Required fields render Discord's native red asterisk and cannot be submitted empty; required selects therefore always yield exactly one value in `on_submit` (read via `self.field.component.value` / `.values` — with a defensive `IndexError` guard). Five of the six **optional lobby settings** arrive as slash-command options (§4.2a below) already client-validated, threaded in via the modal's `optional_settings` dict (display name → value-or-None); **Weapon Randomizer alone lives in the modal** as its optional select (Revision 7), landing in the dict's placeholder slot at submit time. All are merged into the embed's settings fields after the mandatory entries.

**§4.2a Optional settings as slash options** — on `/lfg` and `/lfgmod` (the test commands dropped their slash options in Revision 9; their chained stage-two modal carries the same five settings instead): `first_to` (`app_commands.Range[int, 1, 50]` — client-enforced range), `lobby_type` (`Public`/`Invite Only`/`Private`), `friendly_fire` (`Enabled`/`Disabled`), `mid_match_joining` (`Yes`/`No`), `enemy_outlines` (`Enabled`/`Disabled`) — all `Optional[Literal[...]]` parameters, which dpy turns into optional choice-picker options. Skipped options are `None` and simply don't render on the embed. `weapon_randomizer` moved into the modal as its optional select (Revision 7); `_collect_optional_settings` keeps a `"Weapon Randomizer": None` placeholder so the modal's pick lands in a stable display position. Constructed per-interaction with the cog instance; `on_submit(self, interaction)` runs the §4.4 pipeline. `on_error` logs via `logging.getLogger("red.straftatlfg")`, replies with a generic ephemeral apology — via `interaction.response.send_message` if the response is still unsent (exception before step 6's defer), else `interaction.followup.send` (exception after it); checking `interaction.response.is_done()` avoids `InteractionResponded` — and calls `cog.refund_cooldown(...)` **only if this submission committed and the #new-lfg send had not yet succeeded**. The committed stamp is tracked per invocation and cleared the moment the step-7 send succeeds (§4.4), so an exception in the pre-commit steps can never "refund" a stamp this submission didn't consume (which would erase a still-valid cooldown from the member's previous post), and an exception in the post-send steps can never erase the cooldown of a post that is already live.

### 4.4 Gate, cooldown, validation — the critical ordering (new system only; touches no legacy code)

**Region gate helper** — shared by the `/lfg` callback (fail-fast) and `on_submit` (authoritative):

```python
def get_member_region(self, member: discord.Member) -> Optional[Tuple[str, str, int]]:
    """First REGION_ROLES entry (emoji, label, role_id) the member holds, else None."""
    member_role_ids = {r.id for r in member.roles}
    return next((rg for rg in self.REGION_ROLES if rg[2] in member_role_ids), None)
```

(`Optional`/`Tuple` from `typing` — see the §4.1 type-hint note; PEP 604 syntax would crash at import time on Red's Python 3.8.1 floor.)

- Purely synchronous — a role-set membership check against the hardcoded constant; no Config read, no fetch. `REGION_ROLES` is the **single source of truth** for the reaction listeners (via `_region_role_map()`, `lfg.py:43-45`, unchanged), the gate, and the embed's Region field.
- The gate is **always-on for `/lfg`** (no config toggle in core scope; one arrives with config-ification, §9) and applies **only** to `/lfg` — legacy is frozen.
- Companion `_region_gate_message() -> str`: returns `You need a region role to post — pick one in <#{REGION_CHANNEL_ID}> first.` when the constant is set, or the canonical degraded variant `You need a region role to post — ask an admin where to pick one.` when it is `None`/`0`. It only formats a mention string — never fetches the channel, so a wrong or deleted id cannot raise.

**Cooldown store** — three **new** cog methods plus one member-scope Config key; no legacy code touched. Backing: `config.register_member(last_lfg_ts=0.0)` (float unix time) + in-memory cache `self._cooldown_cache: Dict[Tuple[int, int], float]`. Window: hardcoded `LFG_COOLDOWN_SECONDS = 60` class constant (hardcode-for-now, §6).

- `async def check_cooldown(member) -> float` — remaining seconds; **never writes a stamp** (cache first, Config on miss, cache backfill). **The backfill is non-clobbering — `self._cooldown_cache.setdefault(key, persisted)`, never a plain assignment** (equivalently: re-check the cache after the await and keep `max(cached, persisted)`). This matters because `commit_cooldown_sync` consults only the cache: if a cache-missing read's `await` resolves *after* a concurrent submission has committed (stamped the cache), an assigning backfill would overwrite the fresh in-memory stamp with the stale persisted value (Config isn't written until step 8) and let a second post through — narrow with the JSON driver, real on drivers whose reads yield (e.g. Postgres), and re-opened by `refund_cooldown` deleting the key while other modals for the same member sit open. `setdefault` closes it. This method is also the **mandatory cache-warm**: the cache is empty after a restart, so committing is valid only after `check_cooldown` has run for that member since cog load; the pipeline enforces this by always awaiting it immediately before committing.
- `def commit_cooldown_sync(member) -> Optional[float]` — **synchronous** check-and-stamp of the cache: `None` (still cooling) or stamps `time.time()` and returns it. No `await` between check and stamp → atomic on the single-threaded event loop; two racing modal submits cannot both pass. The Config write happens later, after the post succeeds.
- `async def refund_cooldown(member, stamp)` — **compare-and-clear, cache-only**: deletes the cache entry only if the stored stamp equals `stamp`, so a stale failure can never erase a newer legitimate stamp. No Config write: a refunded stamp was by construction never persisted (step 8 runs only after a successful send), and any previously persisted stamp is provably expired at commit time — writing `0.0` would only add a theoretical ordering race against a concurrent submission's step-8 persist on slow Config drivers. Callers may only invoke it while their per-invocation committed-stamp marker is still set — i.e. before the #new-lfg send has succeeded (see ordering below).

**Why not `commands.CooldownMapping` / `@commands.cooldown`?** The decorator does not run for interaction callbacks at all, and `CooldownMapping` keys buckets off `Message.author`, which for interactions is the **bot** — all users would share one bucket (verified facts). A timestamp store is the boring, correct primitive.

**Ordering in `on_submit`** (this exact order is load-bearing):

```
1. lobby = lobby_id.strip(); if not lobby.isdigit(): ephemeral error; RETURN   (nothing consumed)
2. region = get_member_region(member)                                          (authoritative re-check —
   if region is None: ephemeral _region_gate_message(); RETURN                  the modal may have sat open
                                                                                across a role removal; a failed
                                                                                gate NEVER consumes the cooldown)
3. notes = sanitize_notes(notes)                                               (NEW helper; the two regexes
                                                                                from lfg.py:108-109 verbatim;
                                                                                _process_lfg untouched)
4. await self.check_cooldown(member)                                           (LOAD-BEARING cache warm from
                                                                                Config — after a restart only
                                                                                this read sees the persisted
                                                                                stamp; backfill via setdefault,
                                                                                never assignment)
5. stamp = self.commit_cooldown_sync(member)                                   (atomic consume; track it as this
   if stamp is None: ephemeral "on cooldown"; RETURN                            invocation's committed stamp)
6. await interaction.response.defer(ephemeral=True, thinking=True)             (beat the 3s token deadline)
7. resolve LFG_ROLE_ID + NEW_LFG_CHANNEL_ID; build_lfg_embed(member, lobby,
   notes, region=region); send to #new-lfg with AllowedMentions(roles=[role])
   — any failure here: await refund_cooldown(member, stamp); ephemeral error; RETURN
   — on success: CLEAR the per-invocation committed-stamp marker FIRST — the
     post is live; from here on, no code path (including on_error) may refund
8. await config.member(member).last_lfg_ts.set(stamp)                          (persist; own try/except — a
                                                                                failure logs and SKIPS the refund;
                                                                                worst case the in-memory cooldown
                                                                                holds for the session and a restart
                                                                                grants amnesty on the unpersisted
                                                                                stamp, matching legacy behavior)
9. followup ephemeral success: jump link + echoed lobby ID                     (failure: log, no refund)
```

The refund is thereby scoped to **committed but not yet successfully sent**: steps 8–9 run after the post landed in #new-lfg, so their failures must never erase the cooldown of a live post (which would allow an immediate double post).

`build_lfg_embed(member, lobby_id, notes, region=None)` is a **new** helper reproducing the `lfg.py:116-126` look (easter-egg title, booster color via the same inline `1387554310832918528` literal check — see §6, Lobby ID + Host fields, avatar footer) plus the inline Region field (`{emoji} {label}`) when `region` is not `None`. Callers pass the exact region captured by their own gate check — the builder never re-derives it.

**The `/lfg` callback** does: region gate (synchronous constant + role check) → `await check_cooldown` peek (one fast memory-cached Config read, which also warms the cache) → `interaction.response.send_modal(...)`. Only fast Config reads and a sync role check precede `send_modal` — safely inside the 3s window; an abandoned modal costs nothing. Correctness never depends on the callback-time peek: step 4 re-warms unconditionally.

**`/lfgpings` throttle:** `self._ping_toggle_last: Dict[int, float]`, 5s window, checked/stamped synchronously at the very top; a refusal is the *initial* ephemeral response. Only after passing does the handler `defer(ephemeral=True)` and edit roles.

### 4.5 Region system (permanent, untouched in core scope)

The reaction message + raw-reaction listeners (`lfg.py:469-554`) remain the region-selection surface, reading `REGION_ROLES` and `region_message_ids` exactly as today. Nothing in PR 1 touches them. **Optional PR 2** (hardening): inside `on_raw_reaction_add`, replace the `add_roles` (`:495`) + `remove_roles` (`:508`) pair with one atomic `await member.edit(roles=[non-region roles] + [chosen], reason="LFG region reaction role")` — closes the double-region window and self-heals members somehow holding multiple region roles. The strip-other-reactions loop (`:512-525`) stays best-effort exactly as today.

Edge details PR 2's description must carry (the "tiny" framing is exactly what invites skipping them):

- **The roles payload preserving every non-region role verbatim is load-bearing, not incidental**: `member.edit(roles=[...])` replaces the member's entire role list in one PATCH, and the payload must include the member's **managed roles** (booster/integration roles) unchanged or the API rejects the edit. The `[non-region roles] + [chosen]` construction happens to do this — keep it that way deliberately, and say so in a code comment.
- **All-or-nothing failure mode shift**: today a `Forbidden` on the remove leg still leaves the add applied; after the change, one un-editable role fails the whole swap — and the single edit requires permission over every role being changed, evaluated together. Wrap the edit in the same `try/except discord.Forbidden: pass` as today.
- "Behavior-preserving" therefore describes the happy path; the failure modes shift slightly, which is part of why this PR stays optional.

### 4.6 Restart persistence summary

| Thing | Mechanism |
|---|---|
| `/lfg`, `/lfgpings` registrations | Red persists owner-enabled state; Discord registrations survive restarts by nature; dpy auto-adds cog app commands to the tree on load (`Cog._inject`) |
| Post cooldown (new system) | member-scope Config `last_lfg_ts`, always read (the `check_cooldown` cache-warm) before any commit |
| Legacy cooldowns | in-memory decorators, reset on restart — **as today, frozen** |
| Region membership | lives on member roles + the permanent reaction message (`region_message_ids`) — nothing new to persist |
| Modals in flight | intentionally not persisted (one re-run after a restart) |
| Ping throttle | in-memory only (5s window — restart amnesty irrelevant) |
| Persistent views | **none needed** — no message-attached components in core scope |

---

## 5. Implementation Steps

### PR 1 — New system core (the visible release)

**Adds (all additive; zero modifications to existing functions or config keys):**
- Helpers: `sanitize_notes(text) -> str` (the two regexes from `lfg.py:108-109`, verbatim, in a new function), `build_lfg_embed(member, lobby_id, notes, region=None)`, `get_member_region(member)`, `_region_gate_message()`. All new signatures use `typing` spellings per the §4.1 type-hint note (Python 3.8-safe; no `__future__` import added).
- Cooldown service: `check_cooldown` (non-clobbering `setdefault` backfill, §4.4) / `commit_cooldown_sync` / `refund_cooldown` + `config.register_member(last_lfg_ts=0.0)` + `LFG_COOLDOWN_SECONDS = 60` constant.
- `LFGPostModal` (§4.3), including the `on_error` responded-check and refund scoping.
- `/lfg` (`@app_commands.command(name="lfg")`, `@app_commands.guild_only()`; callback per §4.4) and `/lfgpings` (`@app_commands.command(name="lfgpings")`, **`@app_commands.guild_only()`** — same DM block as `/lfg`; behavior per §3.3).
- `/testlfg` (Revision 5): `@app_commands.guild_only()` + `@app_commands.default_permissions(administrator=True)` + runtime admin check; same `LFGPostModal` with `destination_id=TEST_CHANNEL_ID`, `enforce_gate=False`, `enforce_cooldown=False`, `silent_ping=True` (mention renders, nobody is notified). Owner must also `[p]slash enable testlfg` before sync.
- No `cog_app_command_error` handler — Red's `RedTree.on_error` already logs and replies ephemerally to unexpected app-command exceptions (§4.2); adding one would duplicate both.
- Red EUD deletion hook: `async def red_delete_data_for_user(self, *, requester, user_id)` clearing the stored `last_lfg_ts` for that user across guilds (per-guild `self.config.member_from_ids(guild_id, user_id).clear()` over the guilds Red reports data for) plus the matching in-memory `_cooldown_cache` and `_ping_toggle_last` entries — the updated `end_user_data_statement` is only half the compliance story without the handler that makes `[p]` data-deletion requests actually purge the timestamps.
- `REGION_CHANNEL_ID` constant, default `None` — the maintainer fills in the real id of the channel hosting the region reaction message; used **only** by the gate's deferral copy, degrades gracefully when unset (§4.4).
- `info.json`: `min_bot_version: "3.5.21"` — Red 3.5.21 (dpy 2.6.2) is the genuine floor for the modal's Label-wrapped selects (Revision 6); it also covers the follow-ups the earlier 3.5.10 pin anticipated — and `end_user_data_statement: "This cog stores the timestamp of each user's last LFG post to enforce a posting cooldown."` — shipped in the **same PR** that adds the member timestamp store.

**Rollout, written into the PR description:**
1. Merge → `[p]cog update` + `[p]reload straftatlfg`. At this point **nothing is member-visible**: Red app commands are disabled by default.
2. Owner runs `[p]slash enablecog LFG` (covers all five: `lfg`, `lfgmod`, `testlfg`, `testlfgmod`, `lfgpings` — per-command `[p]slash enable <name>` also works), then `[p]slash sync`. `[p]slash list` to verify; members may need Ctrl+R. **After the Revision 7 update, the two new commands (`lfgmod`, `testlfgmod`) need enabling + a sync even if the original three were already live.**
3. Announce `/lfg` and #new-lfg.

That sequencing **is the staging story**: `/lfg` is invisible to members until the owner enables + syncs, and #new-lfg is quiet until announced. For pre-merge testing, optionally point `NEW_LFG_CHANNEL_ID` at `1269794533923754089` in a **local checkout** — noting that per the maintainer's own comment (`lfg.py:18`) that channel is a **log channel**, not a clean staging channel, so expect log traffic around your test posts — and note that per Revision 4 `!testlfg` also posts to this same channel, so legacy-suite posts (test 11) will interleave with your `/lfg` staging posts (tell them apart by provenance markers: legacy `!testlfg` embeds never carry a Region field and their invoking command message gets a ✅ reaction; `/testlfg` posts use a silent, non-notifying mention and may carry a Region field if the admin holds one) — then restore the real id before merging.

**Deletes / modifies:** nothing. The legacy freeze is a review criterion: the PR diff must show no hunks inside `_handle_sticky`, `on_message`, `_process_lfg`, `lfg`, `testlfg`, `lfg_role`, `sticky_toggle`, the FAQ commands, `lfg_region`, the reaction listeners, or the error handler. (The Revision 4 one-line routing fix in `_process_lfg` was applied directly to the working tree before PR 1 and is not part of this diff.)

### PR 2 — Region listener hardening (optional, tiny)

**Changes:** the atomic `member.edit` swap in `on_raw_reaction_add` (§4.5) replacing `:495`/`:508`, wrapped in the same `try/except discord.Forbidden: pass` as today. Nothing else — no Config reads change, the strip loop stays, `on_raw_reaction_remove` untouched. The PR description must carry the §4.5 caveats: the payload preserving managed/non-region roles verbatim is load-bearing (the API rejects edits that drop managed roles), and the single PATCH is all-or-nothing where today's two calls could half-apply.
**Test:** hand a member two region roles, they react once → exactly one region role afterward (self-heal); react/unreact round-trip still works; a member with a managed booster role keeps it across the swap.

### Everything else → Future Extensions (§9)

Config-ification (`lfgset`, guild-config ids, region config keys, `region_message_channels`, schema migration), legacy retirement + cooldown unification, pinned info embed, the button-panel variant, Close-button `DynamicItem`, auto-expiry, FAQ browser, select-based region picker.

---

## 6. What Stays Hardcoded (and Why) + the One Config Addition

**Hardcode-for-now** is a deliberate match for both the stakeholder instruction ("Hardcode `/lfg` to use `1526690875470774312 # new-lfg` for now") and the codebase's existing style (every live id in `lfg.py` is a constant or inline literal). The new system reads:

| Constant | Value | Consumer in the new system |
|---|---|---|
| `LFG_ROLE_ID` (`lfg.py:14`) | `1358388775570637001` | `/lfg` ping + `/lfgpings` toggle |
| `NEW_LFG_CHANNEL_ID` (`:16`) | `1526690875470774312` # new-lfg | `/lfg` post destination (hardcoded for now) |
| `REGION_ROLES` (`:21-30`) | eight `(emoji, label, role_id)` tuples | gate + Region embed field (same constant the listeners read — single source of truth) |
| Booster role literal (inline at `:117`) | `1387554310832918528` | `build_lfg_embed` color check — the new helper repeats the same inline literal (no new named constant, matching the legacy inline style; the frozen `:117` is not touched) |
| `LFG_COOLDOWN_SECONDS` (**new**) | `60` | cooldown service window |
| `REGION_CHANNEL_ID` (**new**) | `None` until the maintainer fills it in | gate deferral copy only; `None`/`0` → the link-less degraded wording, never a traceback |

**The one Config change:**

```python
config.register_member(last_lfg_ts=0.0)
```

Existing guild keys (`active_sticky_channels`, `sticky_cache`, `faqs`, `region_message_ids`, `lfg.py:35-40`) stay registered as-is — the frozen legacy features use them. Identifier `2736452831` and `force_registration=True` unchanged.

**Migration: near-empty.** The deploy is purely additive — no keys renamed, no data moved, nothing seeded. Rollback is `git revert` + `[p]reload`; the stored member timestamps are ignored by old code (Red Config tolerates unknown keys) and the owner can withdraw the commands from Discord with `[p]slash disable lfg` then `[p]slash disable lfgpings` — each invocation takes **one** command name (the second positional is a command *type*, not another name, so `disable lfg lfgpings` is invalid syntax); `[p]slash disablecog LFG` withdraws both at once — followed by `[p]slash sync`. The entire Revision 2 config apparatus (guild-config ids with live defaults, `regions`, `region_channel_id`, `region_required`, `region_message_channels`, schema versioning, `lfgset`) is deferred to the config-ification future phase (§9).

---

## 7. Testing & Rollout

All items run against a checkout where `NEW_LFG_CHANNEL_ID` optionally points at the log channel `1269794533923754089` (see the PR 1 staging note — it is a log channel, not a clean staging room, and per Revision 4 it is also where `!testlfg` posts land, so `/lfg` and legacy-suite posts interleave there), or directly against #new-lfg pre-announcement (it's quiet until announced).

1. **Enable/sync:** `[p]slash enablecog LFG`, `[p]slash sync` → `/lfg`, `/lfgmod`, and `/lfgpings` appear in the command picker for everyone, `/testlfg` and `/testlfgmod` for Administrators only (Ctrl+R if not); `[p]slash list` shows all five enabled with no pending markers.
2. **Gate deferral:** account with no region role runs `/lfg` → ephemeral pointer at the region channel (or the degraded "ask an admin" wording if `REGION_CHANNEL_ID` is unset), **the modal never opens**, nothing consumed — re-adding the role and re-running posts immediately.
3. **Happy path:** `/lfg` → modal → valid submit → post lands in **#new-lfg** with the LFG role ping, correct embed (Lobby ID, Host, avatar footer) **and Region field matching the held role** (`{emoji} {label}`); invoker gets the ephemeral jump-link confirmation with echoed lobby id.
4. **Ephemeral verification:** a second account watching the invocation channel and #new-lfg sees **nothing except the #new-lfg post** — no command message, no errors, no confirmations.
5. **Invalid lobby id:** the client refuses to submit fewer than 8 characters; a non-numeric 8+ entry like `abc12345` → ephemeral error, cooldown untouched, immediate retry succeeds.
6. **Double-submit race:** same account, two sessions (desktop + browser), both modals open, submit both within 60s → exactly one post; the loser gets the ephemeral on-cooldown message.
7. **Reload + restart:** `[p]reload straftatlfg` → `/lfg` still works (auto tree re-add, no duplicate registration); post, then restart the bot mid-cooldown → `/lfg` still refuses until the window expires (the Config stamp survived; the `check_cooldown` cache-warm before commit is what makes this hold).
8. **`/lfgpings`:** toggle on, toggle off (ephemeral confirmations both ways); mash it → ephemeral throttle message.
9. **Refund path:** revoke the bot's Send Messages permission in #new-lfg → `/lfg` submit → ephemeral "no permission" error; restore the permission → immediate repost succeeds (the cooldown was refunded, not burned — the send never succeeded, so the refund is in scope per §4.4).
10. **Gate racing a role change:** with a region role, open the modal; remove the role from a second admin account; submit → ephemeral deferral, cooldown untouched; re-add, repost immediately succeeds.
11. **LEGACY-UNTOUCHED suite:** `!lfg 12345 notes` in #straftchat → posts to **#legacy-lfg** exactly as before (decorator cooldown fires on a second attempt, channel gate rejects other channels with the `delete_after` message, ✅ reaction lands on the command message); `!testlfg` → posts to the **log channel** (`TEST_CHANNEL_ID`), **not** #legacy-lfg (Revision 4 fix); `!lfg-role` toggles with its replies/reactions; the sticky still reposts after chat messages; region reactions still assign/remove roles.
12. **INDEPENDENCE check:** `!lfg` then `/lfg` within 60s → **both succeed** (one post in each feed). This is the documented transition behavior (§3.4), not a bug; record it in the PR description.
13. **`/testlfg` (Revision 5):** visible to and usable by Administrators only (a non-admin doesn't see it in the picker; if surfaced via an Integrations override, the runtime check refuses ephemerally); same modal as `/lfg`; post lands in the **log channel** with the role mention rendered but **nobody notified** (verify from an LFG-role account with log-channel access); works with no region role and no cooldown — repeated submissions all succeed and never consume or block a real `/lfg` cooldown.
14. **Mandatory modal fields (Revisions 6-7):** the modal cannot be submitted with Lobby ID, Max Players, or Gamemode missing (Discord blocks submission and marks them with a red asterisk); the Weapon Randomizer select can be left untouched and the modal still submits; a submitted post's embed always carries Max Players, Gamemode, and the **derived** Modded Lobby field.
15. **Optional slash options (Revisions 6-7):** `/lfg first_to:10 lobby_type:Private friendly_fire:Disabled` → those three fields appear on the embed with exactly those values; skipped options don't render. `first_to` rejects 0 and 51 client-side (Range 1-50). Same options work on `/lfgmod`; the test commands carry these settings in their chained stage-two modal instead (test 15b).
15b. **Chained test flow (Revision 9):** `/testlfg` → mandatory modal → ephemeral "Core details saved!" panel; `Post Now` → post lands in the log channel and the panel edits in place to the jump-link confirmation; re-running and choosing `Optional Settings` → second modal with the five optional fields → submit → post carries the chosen settings; First To rejects non-numeric and out-of-range (0/51) entries ephemerally with nothing saved; double-clicking Post Now produces exactly one post; letting the panel sit 5+ minutes → buttons disable with "draft expired", and a stage-two modal left open past the expiry is rejected on submit (the draft never posts after it has been declared expired); `/testlfgmod` variant shows the modded title, mod-listing Notes copy, and `Modded Lobby: Yes`.
15a. **Derived moddedness (Revision 7):** `/lfgmod` opens a modal titled "Post a Modded LFG" whose Notes field asks for the mods being run (the `/lfg` modal keeps the generic Notes copy); its post shows `Modded Lobby: Yes` (standard embed colors; moddedness is conveyed by the field, not the color); `/lfg` shows `Modded Lobby: No`; picking a Weapon Randomizer in the modal renders it on the embed between Mid-Match Joining and Enemy Outlines; `/lfg` then `/lfgmod` within 60s → second refused (shared cooldown store).
16. *(If PR 2 ships)* **Atomic swap self-heal:** two region roles by hand, one reaction → exactly one region role afterward; a managed booster role survives the swap.

**Production rollout:** merge PR 1 → reload (invisible) → owner enable/sync (the exact §5 commands) → announce, including where #new-lfg is and that `!lfg` keeps working. If `/lfg` misbehaves, `[p]slash disable lfg` + `[p]slash sync` withdraws it with zero impact on legacy — the two systems share no code paths (`[p]slash disable lfgpings` separately if needed, or `[p]slash disablecog LFG` for both).

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| **Two-feed split during the transition** — members watching only #legacy-lfg miss `/lfg` posts, and vice versa | The LFG ping role fires for **both** feeds (same `LFG_ROLE_ID` on both surfaces), so subscribers see every post regardless of channel; the announcement names both feeds; unification is the future legacy-retirement decision (§9), taken with adoption data in hand |
| **Independent cooldowns** — one `!lfg` + one `/lfg` per 60s | Documented and accepted (§3.4, test 12); bounded at 2× today's rate per member; resolved by legacy retirement (future) |
| **Owner forgets `[p]slash enable`/`sync`** — cog loads, `/lfg` never appears | The exact commands are in the PR description and §5 rollout as a deploy checklist; `[p]slash list` verifies; nothing breaks in the meantime (legacy keeps serving) |
| **Slash sync propagation latency** — command not visible immediately after sync | Known Discord behavior; users may need a client Ctrl+R (verified Red guidance); wait before announcing |
| **Discoverability without a panel** | The command picker surfaces `/lfg` by typing `/`; the announcement carries usage; a pinned info embed is a future option (§9) — with the sticky-title warning attached |
| **Mobile clients occasionally failing to open modals** | **Legacy `!lfg` IS the fallback** — a concrete benefit of keeping it frozen and parallel; no degraded mode to build |
| **Cooldown TOCTOU** (two modal submits in flight) | `commit_cooldown_sync` stamps with no intervening `await` (single-threaded event loop), always preceded by the awaited `check_cooldown` cache-warm (restart safety) whose backfill is **non-clobbering** (`setdefault`, never assignment — a slow Config read resolving after a concurrent commit cannot overwrite the fresh in-memory stamp and let a second post through, §4.4); commit precedes send; `refund_cooldown` is compare-and-clear against the exact committed stamp, and the committed-stamp marker is cleared once the send succeeds, so neither a stale `on_error` nor a post-send failure can ever erase a legitimate stamp or refund a live post. The §4.4 ordering is non-negotiable in review |
| **3s interaction token deadline** | Before `send_modal`: only a synchronous role check + one memory-cached Config read. In `on_submit`: `defer(ephemeral=True, thinking=True)` immediately after the synchronous cooldown commit, before any network sends. In `/lfgpings`: synchronous throttle first (refusal as initial response), then defer. Unexpected callback exceptions are caught by Red's `RedTree.on_error` (§4.2) — logged + ephemeral error reply, never a silent "did not respond" |
| **Gate check racing a role change** (role removed while the modal sits open) | Authoritative `get_member_region` re-check in `on_submit` *before* the cooldown commit (§4.4 step 2); its captured region is the one the embed renders; the callback-time check is only fail-fast UX |
| **`member.edit(roles=...)` clobbering a role granted concurrently by another bot** (PR 2 only) | Milliseconds window; the alternative (today's remove+add at `:495`/`:508`) is the exact multi-region race being closed; accepted trade-off, documented in code alongside the §4.5 caveats (managed roles must stay in the payload; the swap is all-or-nothing); PR 2 is optional |
| **Someone deletes the live region reaction message** | Listeners become no-ops for that id (harmless — the `region_message_ids` gate at `:480`/`:538`); members keep their roles, so the `/lfg` gate keeps passing; admin reposts with `!lfg-region` as today |
| **Red policy: storing member timestamps** | `end_user_data_statement` + `min_bot_version` fixed in PR 1, the same PR that first writes member data — together with the `red_delete_data_for_user` hook (§5) so data-deletion requests actually purge the stored timestamps |
| **`/lfgpings` mashing = role API spam** | 5s in-memory per-user throttle, checked before any awaits |

---

## 9. Future Extensions (explicitly out of scope now)

1. **Config-ification** — the entire Revision 2 §6 apparatus, deferred wholesale: a `[p]lfgset` group; guild-config ids with registered defaults equal to today's hardcoded values (`lfg_role_id`, `lfg_channel_id`/`new_lfg_channel_id`, `command_channel_id`, booster/test ids, `cooldown_seconds`, `notes_max_length`); `regions` / `region_channel_id` / `region_required` keys (bringing a gate toggle and Config-driven listeners); `region_message_channels`; schema versioning. Only then does `/lfg`'s destination stop being hardcoded.
2. **Legacy retirement + cooldown unification** — a future decision, not a scheduled phase: route `!lfg` through the shared pipeline (or degrade it to a `/lfg` pointer), remove the decorators and the `CommandOnCooldown` handler branch, retire the sticky, and collapse to one limiter — ending the two-feed split and the independent-cooldown window. Take it with `/lfg` adoption and mobile-reliability data in hand.
3. **Pinned info embed / updated sticky copy advertising `/lfg`.** ⚠️ **Warning for whoever picks this up:** if the legacy sticky embed copy is edited to mention `/lfg`, the embed **TITLE must not change** — the anti-loop guard in the frozen `on_message` listener matches the exact title string `"How to use the LFG system"` (`lfg.py:92`); a title edit causes an infinite delete/repost loop. Description-only edits are safe (and still count as a legacy-path modification, so they belong in this future phase, not core scope).
4. **The button-panel variant** — the Revision 2 Hub design (persistent `LFGHubView`, `straftatlfg:v1:*` custom_ids, sticky-panel engine, deploy preflight) kept on ice; if members want a click target in addition to the command picker, that document is the spec.
5. **`CloseLFGButton` as a `discord.ui.DynamicItem[Button]`** (template `r"straftatlfg:v1:close:(?P<host_id>[0-9]+)"`, `bot.add_dynamic_items()` in `cog_load` — dpy 2.4+/Red 3.5.10+, the follow-up the conservative `min_bot_version` pin anticipates): host-or-`manage_messages` "grey out this lobby" on #new-lfg posts. Zero storage, restart-proof, the natural v1.1.
6. **Auto-expiry of stale LFG posts** — still gated on first asking the maintainer why commit `8e219a6 "30 min removal"` removed the previous timer behavior.
7. **FAQ panel browser** — an ephemeral select over `faqs` (kills the DM dependency and its `Forbidden` failure mode at `lfg.py:373-378`); needs a pagination answer for >25 entries.
8. **A select-based region picker** as a *supplement* to the permanent reaction message — carries all the stale-options validation burdens documented in Revision 2; the reaction message stays the core surface regardless.
9. **Optional PR 2 follow-through** if it hasn't shipped: the atomic `member.edit` swap in `on_raw_reaction_add` (§4.5) — the only listener change this plan ever proposes.