# Architecture Guide (`BloxBerry Labs` Bot)

## 1. Design Principles

The bot was refactored from a 1,741-line monolith (`bot.py`) into a layered Python package (`bot/`) while preserving **100%** of user-facing Discord features, role/channel IDs, embeds, persistent view `custom_id`s, permissions, slash commands, prefix moderation commands, and JSON state compatibility (`bloxberry_data.json`).

Key structural goals:
- **Zero circular imports:** Lower layers (`config`, `utils`, `database`) have no dependencies on `services`, `views`, `commands`, `events`, or `client`.
- **Separation of concerns:** Discord UI components (`views/`), gateway listeners (`events/`), and slash commands (`commands/`) delegate all business rules, locking, and persistence to `services/`.
- **Asyncio safety:** Non-blocking atomic disk writes, explicit per-entity locks to serialize concurrent interactions, bounded background tasks, and graceful signal handling (`SIGINT`/`SIGTERM`).
- **Maintainability:** Strong type hints (`mypy`), unified style/linting rules (`ruff`), and unit/integration tests (`pytest` + `pytest-asyncio`).

---

## 2. Repository Structure

```text
BloxBerry-Labs-main/
├── main.py                         # Thin worker entrypoint invoking bot.main:main()
├── requirements.txt                # Production runtime dependencies (discord.py)
├── requirements-dev.txt            # Local testing/lint/type tools (pytest, ruff, mypy)
├── pyproject.toml                  # Ruff, Mypy, and Pytest configuration (Python >=3.12)
├── render.yaml                     # Render Background Worker Blueprint
├── .python-version                 # Python runtime pin (3.12.12)
├── .env.example                    # Documented runtime environment variables
├── .gitignore
├── README.md
│
├── bot/
│   ├── __init__.py
│   ├── main.py                     # CLI/worker bootstrap, signal handlers, flock lock, backoff runner
│   ├── client.py                   # BloxBerryBot(commands.Bot): views, cogs, smart tree sync, on_ready
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py             # Domain constants (IDs, roles, seats, tickets) & env Settings loader
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   └── logging.py              # Stream logging config, TokenRedactingFilter, HTTP/429 logger
│   │
│   ├── database/
│   │   ├── __init__.py
│   │   └── state_store.py          # Atomic async JSON StateStore + scoped asyncio.Lock registries
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── discord_helpers.py      # Permission checks, cache-first lookups, single-pass role counts, DMs
│   │   ├── panel_service.py        # Embed builders, panel reconciliation, debounced diff-checked refreshes
│   │   ├── recruitment_service.py  # Application submission, pending-role rollback, accept/deny decisions
│   │   ├── ticket_service.py       # Private ticket creation, claims, unclaims, transcripts, closing, /ticket add
│   │   └── moderation_service.py   # Channel message purging (`purge N`) with 14-day bulk/single split
│   │
│   ├── views/
│   │   ├── __init__.py
│   │   ├── common.py               # SafeView & SafeModal error boundaries
│   │   ├── recruitment.py          # Main/Skill/Review views, dropdowns, buttons, and Application modal
│   │   └── tickets.py              # Ticket type selector, claim/unclaim/close buttons, close confirmation
│   │
│   ├── commands/
│   │   ├── __init__.py
│   │   └── tickets.py              # TicketCommandsCog (`/ticket add`)
│   │
│   └── events/
│       ├── __init__.py
│       ├── member_events.py        # MemberEventsCog (`on_member_join`, `on_member_update`, `on_member_remove`)
│       └── message_events.py       # MessageEventsCog (`on_message` dispatcher for `purge N`)
│
├── tests/                          # Automated pytest suite using mocked Discord objects
│   ├── conftest.py
│   ├── test_config_and_logging.py
│   ├── test_events.py
│   ├── test_lifecycle.py
│   ├── test_moderation.py
│   ├── test_panels_and_rate_limits.py
│   ├── test_recruitment.py
│   ├── test_state_store.py
│   └── test_tickets.py
│
└── docs/
    ├── BASELINE_AUDIT.md
    ├── API_RATE_LIMIT_AUDIT.md
    ├── ARCHITECTURE.md
    └── DEPLOYMENT.md
```

---

## 3. Dependency Flow

```text
main.py
  └── bot/main.py
        ├── bot/config/settings.py
        ├── bot/utils/logging.py
        ├── bot/database/state_store.py
        └── bot/client.py (BloxBerryBot)
              ├── bot/views/*           ──▶  bot/services/*  (via interaction.client)
              ├── bot/commands/*        ──▶  bot/services/*  (via self.bot)
              └── bot/events/*          ──▶  bot/services/*  (via self.bot)
                                               └── bot/database/state_store.py
                                               └── bot/config/settings.py
                                               └── bot/utils/logging.py
```

Views, cogs, and listeners remain thin adapters:
- **Views (`bot/views/`):** Define `custom_id`s, button/select options, and modals using `SafeView` / `SafeModal`, forwarding callbacks directly to `client.recruitment_service` or `client.ticket_service`.
- **Services (`bot/services/`):** Own business validation, permission enforcement, Discord API error logging, and state updates.
- **Database (`bot/database/state_store.py`):** Owns JSON serialization, atomic temp-file writes, legacy schema compatibility (`applications`, `tickets`, plus `meta.panel_messages` and `meta.command_sync_hash`), and per-entity lock dictionaries.

---

## 4. Concurrency, State, and Lifecycle Guarantees

### 4.1 Non-Blocking Atomic State Persistence
`StateStore.save()` snapshots state in memory and runs `_write_atomic_sync()` inside `asyncio.to_thread(...)` serialized by `_persist_lock`. Writes target `<data_file>.tmp` and call `os.replace()` so sudden termination never leaves a truncated JSON file. Legacy `bloxberry_data.json` files without a `"meta"` key are upgraded automatically on load.

### 4.2 Scoped Async Locks
To eliminate double-click races without serializing unrelated users globally:
- `StateStore.seat_locks[role_name]`: serializes seat availability checks + role assignment per main role.
- `StateStore.get_applicant_lock(user_id)`: serializes application submission vs reviewer accept/deny for the same applicant.
- `StateStore.get_ticket_create_lock(user_id)`: prevents one member from creating duplicate concurrent tickets in the same category.
- `StateStore.get_ticket_channel_lock(channel_id)`: serializes claim, unclaim, `/ticket add`, and close/delete inside a ticket channel.
- `StateStore.get_purge_channel_lock(channel_id)`: prevents overlapping `purge` runs in the same text channel.

### 4.3 Verified Bugs Fixed During Refactoring
In addition to REST/rate-limit improvements, five latent bugs discovered in `bot.py` were corrected:
1. **Literal `\\n` in Recruitment Review/DM Messages:** `bot.py` lines 725–748 escaped newlines twice (`"\\n"`), rendering literal `\n` characters in reviewer embeds and applicant DMs. Fixed to `"\n"` in `bot/services/recruitment_service.py`.
2. **Invalid `reason=` Argument on Old-Message Deletion:** `bot.py` line 1305 called `await msg.delete(reason=...)` on messages older than 14 days. In `discord.py`, `Message.delete(self, *, delay=None)` does not accept `reason`, which raises `TypeError`. Fixed in `bot/services/moderation_service.py`.
3. **Purge Off-By-One History Scan:** `channel.history(limit=amount + 1)` was replaced with `channel.history(limit=amount, before=message, oldest_first=False)` so messages arriving concurrently after the `purge` command are not accidentally deleted.
4. **Synchronous File I/O Blocking the Event Loop:** `save_data()` ran synchronously inside async handlers; moved off the event loop via `asyncio.to_thread`.
5. **Startup Failure Poisoning Live Panels:** Setting `_startup_done = True` before panels finished initializing left `main_panel_message` unset if startup hit a transient Discord error; replaced with `PanelService.panels_ready = True` only upon full completion and guarded by `_setup_lock`.

---

## 5. Preserved Contracts & Persistent Identifiers

All external contracts remain identical to the original bot so existing Discord panels, roles, channels, and `bloxberry_data.json` files continue working seamlessly:
- **Gateway Intents:** `guilds` (default), `members=True`, `message_content=True`; `chunk_guilds_at_startup=False`.
- **Slash Commands:** `/ticket add user:<Member>` (guild-only).
- **Text Commands:** `purge <1..1000>` in guild channels (restricted to `PURGE_AUTH_ROLE_ID`).
- **Persistent Custom IDs:** `bb_all_main_apply`, `bb_all_skill_select`, `bb_all_application_accept`, `bb_all_application_deny`, `bb_all_ticket_type`, `bb_all_ticket_claim`, `bb_all_ticket_unclaim`, `bb_all_ticket_close`.
- **Panel Footer Markers:** `BloxBerry Labs • Main Recruitment`, `BloxBerry Labs • Skill Recruitment`, `BloxBerry Labs • Ticket Panel`.
- **Role / Seat / Channel / Department Config:** Exact values in `bot/config/settings.py`.
