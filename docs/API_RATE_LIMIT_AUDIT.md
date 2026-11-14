# Discord API Rate-Limit & Hosting Reliability Audit (`BloxBerry Labs`)

## 1. Executive Summary

The original single-file bot (`bot.py`, 1,741 lines) experienced HTTP `429` rate limits, startup instability, and restart storms when hosted on Render—primarily when run as a free/web-service container with an embedded HTTP keepalive server—even though local runs usually succeeded.

Audit of both the original monolith and `discord.py==2.7.1`'s HTTP/Gateway client revealed that **no external third-party HTTP APIs exist** in this bot; every external request goes strictly to Discord's Gateway WebSocket and REST API (`discord.py`). The instability stemmed from a combination of **unnecessary startup/event REST bursts**, **missing HTTP/Gateway log visibility**, **incomplete exception/retry handling causing Render crash-restart loops**, **process lifecycle overlaps on Render Web Services**, and **shared outbound IP exposure on Render's multi-tenant egress**.

---

## 2. Rate-Limit Domains & Distinctions

To avoid conflating unrelated failure modes, Discord traffic was separated into four distinct limits:

| Limit Category | Mechanism in Discord & `discord.py 2.7.1` | Original Bot Behavior |
|---|---|---|
| **Route-specific REST limits** | `X-RateLimit-Bucket`, `Remaining`, `Reset-After`, `Retry-After`. Handled internally by `discord.http.HTTPClient.request()` (up to 5 retries) when `max_ratelimit_timeout` allows. | Generated unnecessary REST bursts at startup (`fetch_channel`, `history`, `edit`, `tree.sync`) and unthrottled role-change edits (`1.0s` debounce without diffing). |
| **Global REST rate limit** | 50 requests/second across REST routes (`X-RateLimit-Global`, `X-RateLimit-Scope: global`). Shared via `HTTPClient._global_over` event. | Unconditional startup calls + overlapping instances (or Render crash-restart loops) rapidly exhaust global and login/session budgets. |
| **Shared / Cloudflare IP `429` bans** | Non-API edge `429` responses on shared cloud NAT/egress IPs (e.g., error `1015` / HTML `429` without Discord JSON headers). Surface immediately as `discord.HTTPException(status=429)`. | `bot.py` initially only caught `HTTPException(429)` on `bot.start()`; `aiohttp.ClientError`, 5xx, and `discord.RateLimited` escaped uncaught, forcing Render to restart the container repeatedly and compound the IP ban. |
| **Gateway session / IDENTIFY limits** | `GET /gateway/bot` session start limit (typically 1,000 `IDENTIFY` per 24h) and `max_concurrency` bucket. | Render Web Service sleep/wake cycles, rolling-deploy overlap, and uncaught transient exceptions caused repeated full logins and `IDENTIFY` handshakes. |
| **External third-party APIs** | N/A | **None.** The bot only calls Discord REST/Gateway APIs (`asyncio`/`aiohttp` inside `discord.py`). |

---

## 3. Root Causes Found in Baseline (`bot.py`)

1. **Render Web Service Architecture (`KeepAliveHandler`, `bot.py` lines 1641–1691)**
   - Running a Discord Gateway bot inside a Render Web Service (`ThreadingHTTPServer` on `$PORT` serving `"anshuman hi"` for `GET/HEAD /` and `/health`) introduces free-tier sleep/wake cold-start reconnect storms and zero-downtime rolling-deploy overlaps where two containers run the same token simultaneously until health checks cut over.
2. **Discord Library Logging Suppressed (`bot.py` lines 1684–1741)**
   - The original script used `print(..., flush=True)` and bypassed `discord.py`'s logging configuration. Calling `bot.start()` directly (instead of configuring `logging` for `discord`, `discord.http`, and `discord.gateway`) hid all built-in bucket preemptive delays, `Retry-After` notices, and gateway reconnect warnings.
   - Additionally, `bot.py`'s `log_discord_http_error()` ran `bot.start()` without surfacing `discord.RateLimited` (raised when a bucket retry delay exceeds `max_ratelimit_timeout`).
3. **Unconditional Slash-Command Sync on Every Boot/Retry (`bot.py` lines 1364–1371)**
   - `setup_hook()` called `await self.tree.sync()` unconditionally on every start and every reconnect loop iteration, issuing global `PUT /applications/{id}/commands` requests even though `/ticket add` had not changed.
4. **Redundant Startup REST Calls in `on_ready()` (`bot.py` lines 1401–1472)**
   - `on_ready()` executed **5 separate `fetch_channel()` REST calls** (including fetching `MAIN_RECRUITMENT_CHANNEL_ID` and `SKILL_RECRUITMENT_CHANNEL_ID` **twice** in lines 1454 & 1461 before `ensure_panel()` called `fetch_channel()` again at line 1402), plus **3 `channel.history(limit=100)` scans** and **3 unconditional `message.edit()` calls**, ignoring Discord's in-memory gateway channel cache and stored message IDs.
5. **Post-Ready Presence Call (`bot.py` lines 1427–1437)**
   - `on_ready()` sent a separate presence update (`await self.change_presence(...)`) on every READY/RESUME event instead of passing initial `activity` and `status` in the Gateway `IDENTIFY` payload.
6. **Fragile Startup Panel Flag (`bot.py` lines 1445–1476)**
   - `_startup_done = True` was assigned *before* `ensure_panel()` ran. If any startup REST call hit a transient `429` or HTTP 5xx, panels stayed `None` (`main_panel_message = None`) for the lifetime of the process, silently breaking live seat updates.
7. **Unchecked Panel Edits & Aggressive 1.0s Debounce (`bot.py` lines 1373–1399)**
   - `schedule_main_panel_refresh()` waited only `1.0s` and always executed `main_panel_message.edit(...)` even when the rendered embed/view matched what was already live on Discord.
8. **Incomplete Outer Retry / Shutdown Loop (`bot.py` lines 1693–1741)**
   - `run_discord_with_rate_limit_retry()` only caught `discord.HTTPException` when `status == 429`. Transient `discord.RateLimited`, `aiohttp.ClientError`, `discord.ConnectionClosed`, `discord.GatewayNotFound`, `OSError`, `TimeoutError`, and `5xx` errors crashed the Python process—causing Render's supervisor to restart the container immediately without backoff—while fatal errors like `LoginFailure` or `PrivilegedIntentsRequired` could trigger platform restart loops if left unmanaged.
   - `asyncio.sleep(wait_for)` was not interruptible on `SIGTERM`/`SIGINT` during Render deploys, delaying shutdowns and inviting process kills (`SIGKILL`) before sockets/files closed cleanly.
9. **Missing Concurrency Locks & Blocking Disk I/O Under Lock (`bot.py` lines 164–188, 995–1352)**
   - Synchronous `save_data()` ran inline inside `asyncio.Lock` while holding the event loop.
   - Missing per-user / per-channel locks in `create_ticket`, `ClaimButton`, `UnclaimButton`, `close_ticket`, and `purge_channel_messages` allowed duplicate rapid clicks/commands to race and issue duplicate channel creations, role updates, or `404`/`429` bursts.

---

## 4. Implemented Engineering Fixes

| Area | Module(s) | Fix Applied |
|---|---|---|
| **Hosting Model** | `render.yaml`, `bot/main.py` | Switched deployment target from Web Service (`KeepAliveHandler`) to **Render Background Worker** (`type: worker`, no public port, no sleep/wake cycles). Removed forced HTTP server. |
| **Single-Instance Protection** | `bot/main.py` (`acquire_instance_lock`) | Added configurable non-blocking POSIX `fcntl.flock` lock file (`INSTANCE_LOCK_FILE`, default `.bloxberry_bot.lock`, `/tmp/bloxberry_bot.lock` on Render) so two local or worker processes cannot share one filesystem simultaneously. |
| **Observability & Secret Safety** | `bot/utils/logging.py` (`configure_logging`, `TokenRedactingFilter`, `log_discord_http_error`) | Configured unified stdout logging for `bloxberry`, `discord`, `discord.http`, and `discord.gateway`; logs rich `429` headers (`Retry-After`, `X-RateLimit-Global`, `Scope`, `Bucket`, `Remaining`, `Reset-After`) and `discord.RateLimited`; redacts bot tokens from all log output. |
| **Smart Slash-Command Sync** | `bot/client.py` (`compute_command_tree_digest`, `sync_commands_if_needed`) | Computes deterministic SHA-256 digest of registered `app_commands` payloads, persists `meta.command_sync_hash` in `StateStore`, and skips `tree.sync()` when unchanged (`SYNC_COMMANDS_MODE=auto`), or skips completely (`never`), or targets a single test guild (`DISCORD_GUILD_ID`). |
| **Cache-First Channel/Member Resolution** | `bot/services/discord_helpers.py` (`resolve_channel`, `resolve_member`, `count_members_for_roles`) | Checks `guild.get_channel` / `client.get_channel` and `guild.get_member` before issuing REST `fetch_*` calls; counts all main-role seats in a single iteration over cached members. |
| **Idempotent Panel Reconciliation** | `bot/services/panel_service.py` (`_find_existing_panel_message`, `ensure_panel`, `ensure_all_panels`) | Persists panel message IDs in `meta.panel_messages` (`StateStore`) so restarts do one `fetch_message(known_id)` rather than `history(limit=100)`; compares embed/view signatures via `panel_message_matches()` and **skips `edit()` when unchanged**. Sets `panels_ready = True` only after all three panels succeed. |
| **Coalesced Diff-Checked Refreshes** | `bot/services/panel_service.py` (`schedule_main_panel_refresh`, `refresh_main_panel`) | Increased default debounce from `1.0s` to `2.5s` (`PANEL_REFRESH_DEBOUNCE_SECONDS`), checks `panel_message_matches()` prior to calling `.edit()`, attaches done-callback error logging, and cancels cleanly on `bot.close()`. |
| **Gateway Presence via `IDENTIFY`** | `bot/client.py` (`BloxBerryBot.__init__`) | Passes `activity` (`Watching BloxBerry Labs`) and `status` (`online`) directly in `super().__init__(...)` instead of sending `change_presence()` on every `on_ready`. Also sets `chunk_guilds_at_startup=False` and `max_ratelimit_timeout=60.0` (configurable). |
| **Resilient Outer Retry & Clean Shutdown** | `bot/main.py` (`run_bot_with_backoff`, `_sleep_interruptible`) | Fails fast (exit `1`) on `LoginFailure`, `PrivilegedIntentsRequired`, and non-transient `4xx` errors (never hammering Discord with bad tokens/intents). Applies bounded exponential backoff (`60s`–`900s`, respecting `Retry-After`) on HTTP `429`, `discord.RateLimited`, HTTP `5xx`/`408`, and `aiohttp`/`OSError`/Gateway disconnects. Wakes immediately on `SIGINT`/`SIGTERM`. |
| **Non-Blocking Atomic Persistence & Locks** | `bot/database/state_store.py`, services | Moves atomic `json.dump` + `os.replace` onto worker thread via `asyncio.to_thread` protected by `_persist_lock`; adds per-applicant, per-ticket-creator, per-ticket-channel, and per-channel-purge `asyncio.Lock`s alongside `seat_locks`. |

---

## 5. Validation Results

All unit and integration validations in `tests/` were executed locally against mocked Discord objects and controlled retry stubs (live Discord Gateway/REST calls require a live `DISCORD_TOKEN`):

- **Rate-Limit & Panel Optimization (`tests/test_panels_and_rate_limits.py`):**
  - Verified `ensure_all_panels()` resolves channels from cache, locates panels via persisted IDs without calling `channel.history()`, and skips `.edit()` when panel embeds/components match.
  - Verified transient startup failure leaves `panels_ready == False` so the next `on_ready` retries cleanly.
  - Verified bursts of `schedule_main_panel_refresh()` coalesce into one refresh check and skip `.edit()` when computed seat counts remain identical.
  - Verified `sync_commands_if_needed()` performs one initial `PUT` sync, persists the SHA-256 command tree digest, and skips redundant syncs across subsequent startups.
- **Outer Retry, Backoff & Shutdown (`tests/test_lifecycle.py`):**
  - Verified missing tokens and fatal `LoginFailure` / `PrivilegedIntentsRequired` fail fast with exit code `1` (no retry loop).
  - Verified HTTP `429` honors `max(backoff, Retry-After)` and doubles backoff up to `retry_max_delay_seconds`.
  - Verified POSIX `SingleInstanceError` blocks a concurrent second worker process sharing the lock file.
  - Verified `_sleep_interruptible()` wakes immediately when shutdown is requested and `bot.close()` cancels pending background tasks.
