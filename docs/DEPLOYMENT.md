# Deployment & Operations Guide (Render Background Worker)

## 1. Why Background Worker Instead of Web Service

The legacy `bot.py` started a Python `ThreadingHTTPServer` (`KeepAliveHandler` returning `"anshuman hi"`) so the bot could run inside a **Render Web Service**. That setup caused avoidable production failures:
- **Free/Idle Sleep Cycles & Reconnect Storms:** Web services without constant inbound traffic are spun down and restarted repeatedly, triggering fresh Discord Gateway `IDENTIFY` calls and startup REST bursts.
- **Rolling Deploy Overlaps:** Web services run old and new containers simultaneously during health-check cutovers, causing two bot processes to authenticate and respond to events/panels with the same token at once.

This refactored project targets a **Render Background Worker** (`type: worker` in `render.yaml`), which runs persistently without exposing an HTTP server or requiring `$PORT`.

> [!IMPORTANT]
> Render does **not** support Background Workers on the Free tier. `render.yaml` sets `plan: starter` (Render's minimum tier for `type: worker`).
> Before switching live traffic to the Worker, suspend or delete any legacy Render Web Service running the same `DISCORD_TOKEN` so two instances never share a token concurrently.

---

## 2. Discord Developer Portal Prerequisites

Ensure the bot application in the Discord Developer Portal has both **Privileged Gateway Intents** enabled (required by `build_bot_intents()` in `bot/client.py`):
1. **SERVER MEMBERS INTENT** (needed for `on_member_join`, `on_member_update`, `on_member_remove`, and accurate cached seat counts).
2. **MESSAGE CONTENT INTENT** (needed for the `purge <N>` command in `MessageEventsCog`).

The bot's OAuth2 scopes should include `bot` and `applications.commands`, with server permissions to manage channels, roles, messages, and view/send/embed in configured channels (`1567899844062740510`, `1571114752653131836`, `1571128273344401508`, `1571114159548923948`, `1567899862022758564`, `1571094794933895241`, `1571110540414292139`).

---

## 3. Environment Variables

All runtime settings are loaded and validated cleanly by `bot/config/settings.py` (`load_settings`):

| Variable | Default | Description |
|---|---|---|
| `DISCORD_TOKEN` | *(required)* | Discord bot token. Set `sync: false` in Render so you enter it securely in the Render Dashboard. **Never commit `.env` or tokens.** |
| `PYTHON_VERSION` | `3.12.12` | Pinned in `.python-version` and `render.yaml` (`runtime.txt` is ignored by Render). Avoids Python 3.14 `asyncio.iscoroutinefunction` deprecation warnings in `discord.py 2.7.1`. |
| `DATA_FILE` | `bloxberry_data.json` | JSON state file path (`applications`, `tickets`, `meta.panel_messages`, `meta.command_sync_hash`). Point to a persistent disk path such as `/var/data/bloxberry_data.json` on Render if attached. |
| `LOG_LEVEL` | `INFO` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). Controls both `bloxberry` and `discord` / `discord.http` / `discord.gateway` logs. |
| `SYNC_COMMANDS_MODE` | `auto` | Controls slash-command registration: `auto` syncs only when the command tree SHA-256 hash changes; `always` syncs on process startup; `never` skips syncing completely. |
| `DISCORD_GUILD_ID` | *(unset)* | Optional positive guild ID to sync slash commands to one server immediately rather than globally. |
| `PANEL_REFRESH_DEBOUNCE_SECONDS` | `2.5` | Debounce window (seconds, `>= 0.1`) coalescing bursts of main-role member updates before checking/editing the main recruitment panel. |
| `MAX_RATELIMIT_TIMEOUT` | `60.0` | Maximum seconds `discord.py` waits internally per rate-limit bucket before raising `discord.RateLimited` (`>= 30.0`, or `"none"` to wait without limit). |
| `RETRY_BASE_DELAY_SECONDS` | `60` | Base delay (seconds, `>= 5.0`) for outer startup/gateway backoff after transient HTTP `429`/5xx/network failures. |
| `RETRY_MAX_DELAY_SECONDS` | `900` | Maximum outer exponential backoff delay (seconds, `>= RETRY_BASE_DELAY_SECONDS`). |
| `INSTANCE_LOCK_FILE` | `.bloxberry_bot.lock` | POSIX advisory lock (`fcntl.flock`) path preventing duplicate local processes (`render.yaml` sets `/tmp/bloxberry_bot.lock`). Set `""`, `"none"`, `"off"`, or `"false"` to disable. |

---

## 4. Deploying on Render

### Option A: Render Blueprint (`render.yaml`)
1. Push this repository to GitHub/GitLab with `.env` excluded (already enforced by `.gitignore`).
2. In Render Dashboard, choose **New + -> Blueprint** and select the repository.
3. Render reads [`render.yaml`](../render.yaml):
   - Service type: `worker` (`bloxberry-labs-bot`)
   - Runtime: `python` (`PYTHON_VERSION=3.12.12`, matching `.python-version`)
   - Plan: `starter`
   - Build command: `pip install -r requirements.txt`
   - Start command: `python main.py`
4. Enter `DISCORD_TOKEN` when prompted (if creating initially) or under **Environment -> Environment Variables**.

### Option B: Persistent Disk (Recommended)
By default, Render containers have an ephemeral filesystem that resets on every deploy. Without persistent storage:
- `meta.command_sync_hash` resets on redeploy (`SYNC_COMMANDS_MODE=auto` will issue one `tree.sync()` after each redeploy).
- `meta.panel_messages` resets (redeploy falls back once to `history(limit=100)` per panel channel to rediscover existing panels).
- Pending `applications` and `tickets` metadata in `bloxberry_data.json` are lost across container rebuilds.

To persist state across deploys on a paid Render disk:
1. Uncomment the `disk:` section in [`render.yaml`](../render.yaml) (mounting `/var/data`).
2. Set `DATA_FILE=/var/data/bloxberry_data.json`.

---

## 5. Local Execution & Verification

Create a virtual environment with Python 3.12+ and install runtime + dev tools:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Run checks before deploying:

```bash
pytest -v
ruff check bot main.py tests
ruff format --check bot main.py tests
mypy bot main.py
python -m compileall -q bot main.py tests
```

Copy `.env.example` to `.env`, set `DISCORD_TOKEN`, export `.env` (e.g., `set -a && source .env && set +a`), and start the worker:

```bash
python main.py
```

---

## 6. Operational Troubleshooting

- **Bot logs `Missing DISCORD_TOKEN` and exits `1`:** Ensure `DISCORD_TOKEN` is set cleanly in Render **Environment** (or exported locally) with no surrounding quotes/whitespace.
- **Bot logs `[DISCORD FATAL] ... LoginFailure` and exits `1`:** The token is invalid or revoked; regenerate it in Discord Developer Portal. Fail-fast exit prevents Render from hammering Discord's login API.
- **Bot logs `[DISCORD FATAL] ... PrivilegedIntentsRequired` and exits `1`:** Enable both **SERVER MEMBERS INTENT** and **MESSAGE CONTENT INTENT** in Discord Developer Portal.
- **Bot logs `[DISCORD 429]` or `[DISCORD RATELIMIT]`:** Check log fields (`retry_after`, `global`, `scope`, `bucket`, `remaining`). When running on Render shared IPs, Cloudflare may return temporary IP-wide `429` cooldowns (`global=unknown` / non-JSON body); the worker automatically obeys `Retry-After` and backs off exponentially up to 15 minutes (`RETRY_MAX_DELAY_SECONDS=900`). Do not restart the service manually mid-cooldown, as doing so wastes login attempts.
- **`SingleInstanceError` on startup:** Another process already holds `INSTANCE_LOCK_FILE`. Stop the existing worker/process before launching another copy.
