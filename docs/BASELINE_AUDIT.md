# Baseline Audit — BloxBerry Labs Discord Bot

**Audit Date:** 2026-11-14  
**Scope:** Complete pre-refactor inspection of the existing repository (`bot.py`, `requirements.txt`, `README.txt`, `.gitignore`).

---

## 1. Repository Snapshot

| File | Size / Lines | Purpose |
| --- | --- | --- |
| `bot.py` | 68,013 bytes / 1,741 lines | Monolithic Discord bot containing configuration, embed builders, views, modals, slash commands, prefix message parser, state persistence, event listeners, HTTP keepalive server, and startup retry loop. |
| `requirements.txt` | 1 line | Declares `discord.py>=2.7.1,<3.0.0`. |
| `README.txt` | 41 lines | Render Web Service setup notes, `DISCORD_TOKEN` instructions, keepalive endpoint description, and 429 retry notes. |
| `.gitignore` | 5 lines | Ignores `.env`, `__pycache__/`, `*.pyc`, `bloxberry_data.json`, `bloxberry_data.json.tmp`. |

**Runtime & Environment Observations:**
- Local Python: `Python 3.14.5` (`/opt/homebrew/bin/python3`), `uv` available; `discord.py` and `pytest` are not installed in the system Python environment.
- No `.git` repository exists inside the project directory itself (parent home directory contains an unrelated git tree, so git branch/commit operations must not alter the parent tree).
- No automated test suite, linter config, `render.yaml`, or `.env.example` exists in the baseline.

---

## 2. Original Architecture Assessment

The baseline bot is a single-module `discord.Client` subclass (`BloxBerryBot`) running alongside:
1. **Module-level global configuration** (hardcoded Discord channel, category, and role snowflakes, seat limits, ticket department routing, embed theme color `0xE91EAD`, and panel footer markers).
2. **Module-level global synchronization primitives** (`data_lock = asyncio.Lock()` and `seat_locks = {name: asyncio.Lock()}`).
3. **Synchronous file persistence** (`load_data()`, `save_data()` reading/writing `bloxberry_data.json` via temporary file replacement).
4. **UI components & domain logic tightly coupled** (`RecruitmentModal`, `MainPanelView`, `SkillPanelView`, `ApplicationReviewView`, `TicketPanelView`, `TicketControlView`, `CloseConfirmView`, `finalize_application`, `create_ticket`, `close_ticket`).
5. **Command registration** via `app_commands.CommandTree` attached directly in `BloxBerryBot.__init__` (`TicketCommands` group) and message-based `purge <N>` dispatched inside `on_message`.
6. **Render Web Service keepalive workaround** (`KeepAliveHandler` via `ThreadingHTTPServer` in a daemon `threading.Thread` on `0.0.0.0:$PORT`).
7. **Startup wrapper** (`run_discord_with_rate_limit_retry()`) constructing a fresh `BloxBerryBot` in a `while True` loop and retrying on `HTTPException` with `status == 429`.

---

## 3. Complete Functionality Inventory

### 3.1 Configuration & Domain Constants

- **Environment Variables:**
  - `DISCORD_TOKEN` (required; stripped; raises `RuntimeError` if empty).
  - `PORT` (optional, default `"10000"`; used only by `KeepAliveHandler`).
- **Channels / Categories:**
  - `MAIN_RECRUITMENT_CHANNEL_ID = 1571114752653131836`
  - `SKILL_RECRUITMENT_CHANNEL_ID = 1571128273344401508`
  - `APPLICATION_REVIEW_CHANNEL_ID = 1571114159548923948`
  - `TICKET_PANEL_CHANNEL_ID = 1567899862022758564`
  - `TICKET_CATEGORY_ID = 1571094794933895241`
  - `TRANSCRIPT_CHANNEL_ID = 1571110540414292139`
  - `WELCOME_CHANNEL_ID = 1567899844062740510`
- **Roles:**
  - `AUTO_MEMBER_ROLE_ID = 1568182409957081108`
  - `PENDING_APPLICATION_ROLE_ID = 1571120110830100652`
  - `TICKETS_ACCESS_ROLE_ID = 1571063785081536562`
  - `PURGE_AUTH_ROLE_ID = 1567599630030610502`
  - `RECRUITMENT_REVIEWER_ROLE_IDS` (6 roles: Studio Director, Executive Manager, Project Manager, Technical Director, Creative Director, Community Director)
- **Recruitment Roles & Limits:**
  - `MAIN_ROLES` (9 roles) with `SEAT_LIMITS` (total 18 seats across 9 roles: 1, 2, 2, 2, 2, 1, 1, 5, 2)
  - `SKILL_ROLES` (12 roles)
- **Ticket Departments (`TICKET_TYPES`):**
  - `Creative Department Request` (`key="creative"`)
  - `Marketing Request` (`key="marketing"`)
  - `Technical Support` (`key="technical"`)
  - `Partnerships` (`key="partnerships"`)
  - `General Inquiry` (`key="general"`)
  - `Member Report` (`key="report"`)
  - Each defines `ping`, `access`, and `claim` role lists. Note: `create_ticket()` also grants `TICKETS_ACCESS_ROLE_ID` `view_channel`, `send_messages`, `read_message_history`, `attach_files` on every created ticket channel, while `claim` authorization comes only from `administrator` or `cfg["claim"]`.
- **Branding & Panel Markers:**
  - `COLOR = 0xE91EAD`
  - `MAIN_PANEL_MARKER = "BloxBerry Labs • Main Recruitment"`
  - `SKILL_PANEL_MARKER = "BloxBerry Labs • Skill Recruitment"`
  - `TICKET_PANEL_MARKER = "BloxBerry Labs • Ticket Panel"`
  - `DATA_FILE = "bloxberry_data.json"`

### 3.2 Commands

1. **Slash Command Group (`/ticket`, `@app_commands.guild_only()`):**
   - `TicketCommands.add(interaction, user: discord.Member)` (`/ticket add`):
     - Must run inside an open tracked ticket channel (`status == "open"`).
     - Authorized if `administrator`, ticket `claimed_by == interaction.user.id`, or `is_ticket_claim_authorized`.
     - Rejects `user.bot`.
     - Grants `user` `view_channel`, `send_messages`, `read_message_history`, `attach_files`, `embed_links`.
     - Sends public audit embed in ticket channel (`interaction.response.send_message`), attempts DM to `user`, and sends ephemeral followup with DM status.
2. **Prefix-less Message Command (`purge <N>` in `on_message`):**
   - Case-insensitive match on first whitespace token (`parts[0].casefold() == "purge"`), ignores bots and DMs.
   - Requires exactly 2 tokens with numeric second token and `1 <= amount <= 1000`.
   - Requires `PURGE_AUTH_ROLE_ID` in `message.author.roles` (no admin override in baseline).
   - Verifies bot `manage_messages`, `view_channel`, `read_message_history` permissions.
   - Deletes `amount` messages immediately preceding the command (`delete_messages` in chunks of 100 for `< 14` days, individual `msg.delete()` for `>= 14` days) and deletes the command message itself.

### 3.3 Event Listeners

- `setup_hook()`: Registers 5 persistent views (`MainPanelView`, `SkillPanelView`, `TicketPanelView`, `TicketControlView`, `ApplicationReviewView`) and calls `await self.tree.sync()`.
- `on_ready()`: Logs username, sets presence `"Watching BloxBerry Labs"` (`ActivityType.watching`, `Status.online`), logs intents and `WELCOME_CHANNEL_ID`, runs one-time panel setup (`ensure_panel` for main recruitment, skill recruitment, and ticket panels; explicitly leaves welcome channel static).
- `on_member_join(member)`: Assigns `AUTO_MEMBER_ROLE_ID` (catching `Forbidden` / `HTTPException` without aborting welcome), verifies channel permissions, computes `join_number = member.guild.member_count`, posts rich welcome embed (or plain-text fallback if `embed_links` is missing) to `WELCOME_CHANNEL_ID`.
- `on_member_update(before, after)`: Schedules debounced main panel refresh if added/removed roles intersect `MAIN_ROLES.values()`.
- `on_member_remove(member)`: Schedules debounced main panel refresh if departed member held any role in `MAIN_ROLES.values()`.
- `on_message(message)`: Dispatches `purge <N>`.

### 3.4 Interactive Views & Modals

- **Persistent Views (`timeout=None`):**
  - `MainPanelView` -> `MainApplyButton` (`custom_id="bb_all_main_apply"`)
  - `SkillPanelView` -> `SkillRoleSelect` (`custom_id="bb_all_skill_select"`, `min_values=1`, `max_values=12`)
  - `TicketPanelView` -> `TicketTypeSelect` (`custom_id="bb_all_ticket_type"`)
  - `TicketControlView` -> `ClaimButton` (`custom_id="bb_all_ticket_claim"`), `UnclaimButton` (`custom_id="bb_all_ticket_unclaim"`), `CloseButton` (`custom_id="bb_all_ticket_close"`)
  - `ApplicationReviewView` -> `AcceptApplicationButton` (`custom_id="bb_all_application_accept"`), `DenyApplicationButton` (`custom_id="bb_all_application_deny"`)
- **Ephemeral / Transient Views:**
  - `MainRoleView` (`timeout=180`) -> `MainRoleSelect` (lists open positions with remaining seat counts or `"All positions are full"` / `"none"`).
  - `SkillContinueView` (`timeout=180`) -> `SkillContinueButton` (opens `RecruitmentModal("skill", selected_names)`).
  - `ApplicationReviewedView` (`timeout=None`, disabled button `"Application Reviewed"`, attached to edited review embed).
  - `CloseConfirmView` (`timeout=60`) -> `CloseConfirmButton`, `CloseCancelButton`.
- **Modal:**
  - `RecruitmentModal(app_type, selected_names)` (`timeout=300`): 5 inputs (`Previous Experience`, `Your Skills`, `Portfolio / Previous Work` [optional], `Availability`, `Why BloxBerry Labs?`). Validates no pending app, validates roles exist, enforces `SEAT_LIMITS` under `seat_locks[name]`, grants `PENDING_APPLICATION_ROLE_ID`, sends embed to `APPLICATION_REVIEW_CHANNEL_ID`, saves application record, sends ephemeral confirmation.

### 3.5 State Store & External Integrations

- **State Store (`bloxberry_data.json`):**
  - `applications`: keyed by `str(user_id)`.
  - `tickets`: keyed by `str(channel_id)`.
- **External Services:**
  - Discord Gateway & REST API exclusively (`discord.py`). Zero external third-party HTTP APIs or databases.

### 3.6 Background / Auxiliary Tasks

- `_run_debounced_main_panel_refresh()`: `asyncio.Task` spawned lazily from `schedule_main_panel_refresh()` with `1.0s` coalesce sleep.
- `run_discord_with_rate_limit_retry()`: top-level `while True` reconnect loop wrapping `bot.start(TOKEN)`.
- `start_keepalive_server()`: daemon thread running `ThreadingHTTPServer`.

---

## 4. Identified Issues, Bugs, and Risks

### 4.1 Rate-Limit & Render Hosting Issues

1. **Render Web Service Architecture Causes Instance Overlap & Restart Storms:**
   - Using `type: web` with `ThreadingHTTPServer` (`KeepAliveHandler`) means Render treats the bot as a web service: zero-downtime deploys start the new container while the old container stays connected to Discord, causing duplicate active sessions with identical tokens; free-tier inactivity spin-down and external pings trigger repeated cold boots.
   - Render shared egress IPs are frequently flagged by Cloudflare (`HTTP 429` without `Via` header / HTML block page), which `discord.py`'s `HTTPClient` deliberately raises immediately instead of retrying.
2. **Unconditional `self.tree.sync()` on Every Startup / Retry:**
   - `setup_hook()` invokes `await self.tree.sync()` (`PUT /applications/{id}/commands`) unconditionally on every startup and every retry-loop iteration, wasting command-registration rate-limit budget during restarts.
3. **Redundant `on_ready()` REST Traffic & Uncached Lookups:**
   - `on_ready()` calls `fetch_channel()` **5 times** (including duplicate `fetch_channel` calls for `MAIN_RECRUITMENT_CHANNEL_ID` and `SKILL_RECRUITMENT_CHANNEL_ID`) without checking `self.get_channel()` cache first.
   - `ensure_panel()` fetches up to 100 historical messages across 3 channels (`3 REST calls`) and unconditionally sends `message.edit(...)` (`3 PATCH calls`) on every cold startup, even when the embed and view have not changed.
4. **Incomplete Startup Error Handling in Retry Loop:**
   - `run_discord_with_rate_limit_retry()` only handles `HTTPException` when `exc.status == 429`. Transient `aiohttp.ClientError`, `OSError`, `asyncio.TimeoutError`, `GatewayNotFound`, `RateLimited`, or 5xx `HTTPException` crash the process, causing Render's crash-restart supervisor to restart immediately with zero backoff — worsening Cloudflare IP bans.
   - Conversely, permanent `discord.LoginFailure` (401) or `discord.PrivilegedIntentsRequired` causes an immediate crash and infinite Render restart loop rather than clear fail-fast reporting.
5. **Silent `discord.py` Rate-Limit Telemetry:**
   - Python `logging` is never configured (`print` only), hiding `discord.http` and `discord.gateway` logs (bucket 429 retries, global rate-limit pauses, gateway disconnects/resumes).
6. **Panel Refresh Debounce & Stale `_startup_done` Bug:**
   - `self._startup_done = True` is set *before* `ensure_panel()` completes; if panel setup fails due to a transient error, `self.main_panel_message` remains `None` permanently.
   - `refresh_main_panel()` performs `PATCH` even when the embed payload is unchanged, and `_run_debounced_main_panel_refresh()` uses a short 1.0s sleep with an untracked task not cancelled on shutdown.

### 4.2 Verified Functional & Concurrency Bugs

7. **Literal `\\n` Escape Bug in `finalize_application()` (`bot.py` lines 725–748):**
   - `mentions = "\\n".join(...)`, `role_names_text = "\\n".join(...)`, and `dm_body` strings contain double-escaped `\\n` instead of `\n`, rendering literal `\n` characters in reviewed application embeds and applicant DMs.
8. **Missing Locks / Race Conditions in Ticket Workflows:**
   - `create_ticket()`, `ClaimButton`, `UnclaimButton`, and `close_ticket()` do not acquire locks around state checks/mutations, allowing duplicate ticket channels, concurrent claim overrides, or double transcript generation / deletion errors on double-clicks.
9. **Overly Broad `data_lock` Scope & Missing Rollback:**
   - `RecruitmentModal.on_submit()` and `finalize_application()` hold the global `data_lock` across multiple Discord REST requests (`add_roles`, `fetch_member`, `send`), blocking all unrelated state operations.
   - In `RecruitmentModal.on_submit()`, if `review_channel.send()` raises after `PENDING_APPLICATION_ROLE_ID` is granted, the user retains the pending role with no saved application record.
   - In `finalize_application()`, if `remove_roles()` fails after `add_roles()`, state is never saved and the review message is never updated.
10. **Synchronous Blocking File I/O on Event Loop:**
    - `save_data()` performs blocking file open/write/replace inside async handlers while holding `data_lock`.
11. **`purge_channel_messages()` Off-by-One Risk & Unbounded Sequential Deletes:**
    - `channel.history(limit=amount + 1)` without `before=message` can collect messages posted after the purge command.
    - Deleting up to 1,000 messages older than 14 days fires sequential `DELETE` requests without per-channel concurrency guards or explicit rate-limit / exception resilience.
12. **Module-Level `asyncio.Lock()` Instances & Missing Graceful Shutdown:**
    - `data_lock` and `seat_locks` instantiated at import time cause event-loop binding hazards in multi-loop test suites.
    - No `SIGTERM`/`SIGINT` signal handling, and `bloxberry_data.json` persistence path is hardcoded to CWD (risky on Render without persistent disk path configuration).
13. **Missing Slash Command & Interaction Error Handling:**
    - No `CommandTree.on_error` or centralized interaction exception handler; transient Discord/permission failures inside `/ticket add` or buttons can leave interactions unacknowledged or raw tracebacks unhandled.

### 4.3 Security & Configuration Review

- No hardcoded tokens or secrets exist in the baseline (`DISCORD_TOKEN` is read from env).
- `.gitignore` excludes `.env`, `bloxberry_data.json`, `bloxberry_data.json.tmp`, `__pycache__/`, `*.pyc`.
- No SQL database or shell subprocess calls exist (no SQLi/command injection risk).
- Improvement opportunities: sanitize logs so tokens/secrets can never leak; add `.env.example` and runtime startup validation; preserve all role-based authorization boundaries strictly.

---

## 5. Proposed Target Architecture & Migration Plan (Stage B)

### 5.1 Target Project Layout

```text
BloxBerry-Labs-main/
├── main.py
├── requirements.txt
├── render.yaml
├── .python-version
├── .env.example
├── .gitignore
├── README.md
├── bot/
│   ├── __init__.py
│   ├── main.py                  # Async entrypoint, signal handling, retry/backoff runner
│   ├── client.py                # BloxBerryBot(commands.Bot) lifecycle, cog loading, sync logic
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py          # Typed Settings dataclass + domain IDs/constants
│   ├── database/
│   │   ├── __init__.py
│   │   └── state_store.py       # Async-safe JSON StateStore (atomic writes, per-entity locks)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── discord_helpers.py   # DM delivery, HTTP/429 diagnostics, embed/content helpers
│   │   ├── panel_service.py     # Cached panel setup, signature diffing, debounced refresh
│   │   ├── recruitment_service.py # Application creation, seat locks, accept/deny, rollback
│   │   ├── ticket_service.py    # Ticket creation, claim/unclaim, transcript, close, add-member
│   │   └── moderation_service.py # Channel message purge logic with concurrency guard
│   ├── views/
│   │   ├── __init__.py
│   │   ├── recruitment.py       # MainPanelView, MainRoleView, SkillPanelView, SkillContinueView,
│   │   │                        # RecruitmentModal, ApplicationReviewView, ApplicationReviewedView
│   │   └── tickets.py           # TicketPanelView, TicketControlView, CloseConfirmView
│   ├── commands/
│   │   ├── __init__.py
│   │   └── tickets.py           # TicketCommands Cog/GroupCog (/ticket add)
│   ├── events/
│   │   ├── __init__.py
│   │   ├── member_events.py     # Welcome flow, auto Member role, role/leave panel triggers
│   │   └── message_events.py    # `purge <N>` listener
│   └── utils/
│       ├── __init__.py
│       └── logging.py           # Structured Render-friendly logging + discord.py logger config
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_state_store.py
│   ├── test_recruitment.py
│   ├── test_tickets.py
│   ├── test_moderation.py
│   ├── test_events.py
│   ├── test_panels_and_rate_limits.py
│   └── test_lifecycle.py
└── docs/
    ├── BASELINE_AUDIT.md
    ├── ARCHITECTURE.md
    ├── API_RATE_LIMIT_AUDIT.md
    └── DEPLOYMENT.md
```

### 5.2 Migration Mapping

| Original `bot.py` Component | Target Module | Notes |
| --- | --- | --- |
| Constants, channel/role IDs, `MAIN_ROLES`, `SEAT_LIMITS`, `SKILL_ROLES`, `TICKET_TYPES`, `COLOR`, markers, env vars | `bot/config/settings.py` | Preserves all exact default IDs and mappings; adds configurable `DATA_FILE`, `LOG_LEVEL`, `SYNC_COMMANDS_ON_STARTUP`, `PANEL_REFRESH_DEBOUNCE_SECONDS`. |
| `load_data()`, `save_data()`, `data_lock`, `seat_locks` | `bot/database/state_store.py` | Adds async-safe atomic persistence (`asyncio.to_thread`), per-user/per-channel/per-seat locks bound safely per instance, panel message ID cache, and command-sync signature storage. |
| `log_discord_http_error()`, `send_user_dm()`, `truncate()`, `member_has_any()`, role auth helpers | `bot/services/discord_helpers.py` & `bot/utils/logging.py` | Preserves exact DM messages and permission logic; configures `logging` for `discord` & `bloxberry`. |
| `main_recruitment_embed()`, `skill_recruitment_embed()`, `ticket_panel_embed()`, `ensure_panel()`, `schedule_main_panel_refresh()`, `refresh_main_panel()` | `bot/services/panel_service.py` | Uses `get_channel()` cache first, tracks panel message IDs, skips unchanged edits, manages debounce task lifecycle cleanly on shutdown. |
| `RecruitmentModal`, `MainRole*`, `MainPanel*`, `Skill*`, `ApplicationReview*`, `finalize_application()` | `bot/views/recruitment.py` + `bot/services/recruitment_service.py` | Fixes `\\n` DM/embed bug, eliminates broad lock holding across REST calls, adds pending-role rollback on send failure. |
| `TicketTypeSelect`, `TicketPanelView`, `create_ticket()`, `ClaimButton`, `UnclaimButton`, `CloseButton`, `TicketControlView`, `CloseConfirmView`, `build_transcript()`, `close_ticket()` | `bot/views/tickets.py` + `bot/services/ticket_service.py` | Adds per-user ticket creation lock and per-channel ticket mutation locks to prevent duplicate tickets/closes/claims. |
| `TicketCommands` (`/ticket add`) | `bot/commands/tickets.py` | Preserves `@app_commands.guild_only()` and exact permissions/DM/audit embed behavior; adds tree error handling. |
| `purge_channel_messages()`, `on_message` (`purge N`) | `bot/services/moderation_service.py` + `bot/events/message_events.py` | Uses `before=message` in history query, per-channel purge lock, and rate-limit-safe error handling. |
| `on_member_join`, `on_member_update`, `on_member_remove` | `bot/events/member_events.py` | Preserves exact welcome embed/fallback and role triggers. |
| `BloxBerryBot` | `bot/client.py` | Subclasses `commands.Bot` (with `command_prefix=lambda _b, _m: []` so prefix parser never interferes with `purge N`), loads extensions, registers persistent views, smart command sync, presence via `Client` kwargs. |
| `run_discord_with_rate_limit_retry()` & entrypoint | `bot/main.py` & `main.py` | Removes forced Render Web Service keepalive HTTP server; adds `SIGTERM`/`SIGINT` shutdown, transient network vs 429 vs fatal auth error handling, and clean resource closing. |

### 5.3 Migration Sequence

1. **Stage A & B (Current):** Baseline documented in `docs/BASELINE_AUDIT.md`. Keep `bot.py` intact until full migration is verified.
2. **Stage C (Controlled Refactoring):** Build `bot/config`, `bot/utils`, `bot/database`, `bot/services`, `bot/views`, `bot/commands`, `bot/events`, `bot/client.py`, `bot/main.py`, `main.py` incrementally, verifying imports and unit tests after each component.
3. **Stage D (Reliability Engineering):** Apply rate-limit reductions, smart command sync, panel caching/diffing, async persistence, per-entity locks, and signal/retry hardening. Document in `docs/API_RATE_LIMIT_AUDIT.md`.
4. **Stage E (Deployment Engineering):** Author `render.yaml` (Background Worker), `.python-version`, `.env.example`, `requirements.txt`, updated `.gitignore`, `README.md`, `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md`.
5. **Stage F (Final Verification):** Run full pytest suite, static checks, syntax/import checks, parity audit against original `bot.py`, and remove monolithic `bot.py` once verified.
