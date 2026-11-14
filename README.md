# BloxBerry Labs Discord Bot

Production-hardened Discord bot for **BloxBerry Labs** built on `discord.py` (`>=2.7.1,<3.0.0`), refactored into a modular package (`bot/`) and configured for reliable deployment as a **Render Background Worker**.

---

## Features Preserved

- **Main Position Recruitment (`#main-recruitment`)**
  - Interactive apply button (`bb_all_main_apply`), live seat counters across 9 staff roles with configured capacity limits, full-role filtering, modal application form, staff review channel notifications (`Accept` / `Deny` buttons), `Pending Application` role assignment/cleanup with automatic rollback on review-post failures, and applicant DM notifications.
  - Real-time panel seat updates triggered by `on_member_update` / `on_member_remove` using debounced diff-checked refreshes.
- **Skill-Based Recruitment (`#skill-recruitment`)**
  - Multi-select skill role picker (`bb_all_skill_select`, up to 12 roles), step-2 confirmation flow, modal submission, reviewer approval/denial, and DM notifications.
- **Department Ticket System (`#ticket-panel`)**
  - Dropdown category selector (`bb_all_ticket_type`) covering 6 departments (`Creative Department Request`, `Marketing Request`, `Technical Support`, `Partnerships`, `General Inquiry`, `Member Report`) with duplicate open-ticket checks per category, private channel permission overwrites, department pings, `Claim` / `Unclaim` / `Close` controls, confirmation prompt, plain-text transcript archival to `#transcripts`, and `/ticket add <user>`.
- **Welcome & Auto-Role (`on_member_join`)**
  - Automatically assigns the Member role (`1568182409957081108`) and posts a styled welcome embed (with plain-text fallback when `Embed Links` is absent) without disturbing static welcome embeds.
- **Moderation (`purge <1..1000>`)**
  - Role-restricted channel purge command deleting messages immediately preceding the command message, splitting `<=14`-day bulk deletions and `>14`-day individual deletions safely.

---

## Project Layout

- `main.py`: Thin process entrypoint (`python main.py`)
- `bot/main.py`: Logging bootstrap, single-instance lock, signal handlers, rate-limit backoff loop
- `bot/client.py`: `BloxBerryBot` client, persistent view registration, extension loading, smart command sync
- `bot/config/settings.py`: Immutable IDs/mappings (`ChannelConfig`, `RoleConfig`, `TICKET_TYPES`) & env `Settings`
- `bot/database/state_store.py`: Thread-offloaded atomic JSON persistence + scoped `asyncio.Lock` registries
- `bot/services/`: `discord_helpers.py`, `panel_service.py`, `recruitment_service.py`, `ticket_service.py`, `moderation_service.py`
- `bot/views/`: `common.py`, `recruitment.py`, `tickets.py`
- `bot/commands/`: `tickets.py` (`/ticket add`)
- `bot/events/`: `member_events.py`, `message_events.py`
- `tests/`: Automated unit and integration suite (`pytest`)
- `render.yaml`: Render Background Worker Blueprint

---

## Quick Start (Local)

1. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN` (keep `.env` private; it is ignored by `.gitignore`):
   ```bash
   cp .env.example .env
   ```
2. Install dependencies into a Python `3.12+` virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements-dev.txt
   ```
3. Run tests, lint, formatting check, and type check:
   ```bash
   pytest -v
   ruff check bot main.py tests
   ruff format --check bot main.py tests
   mypy bot main.py
   ```
4. Export environment variables and start the bot:
   ```bash
   set -a && source .env && set +a
   python main.py
   ```

---

## Documentation

- [Baseline Audit](docs/BASELINE_AUDIT.md) — Pre-refactor inventory and issue analysis of original `bot.py`
- [Discord API Rate-Limit Audit](docs/API_RATE_LIMIT_AUDIT.md) — Root causes of Render 429s/restarts and implemented mitigations
- [Architecture](docs/ARCHITECTURE.md) — Module layout, concurrency/locking design, and verified bug fixes
- [Deployment Guide](docs/DEPLOYMENT.md) — Render Background Worker setup, persistent disk notes, env reference, and troubleshooting
