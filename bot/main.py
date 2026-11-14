"""Process entrypoint, signal handling, single-instance guard, and backoff runner."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import aiohttp
import discord
from aiohttp import web

from bot.client import BloxBerryBot
from bot.config import Settings, load_settings
from bot.database import StateStore
from bot.utils import configure_logging, log_discord_http_error

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


class SingleInstanceError(RuntimeError):
    """Raised when another local bot process already holds the instance lock."""


@contextmanager
def acquire_instance_lock(lock_path: Path | None) -> Iterator[None]:
    """Acquire a non-blocking exclusive advisory file lock when configured on POSIX hosts."""
    if lock_path is None or fcntl is None:
        yield
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SingleInstanceError(
                f"Another bot process already holds instance lock {lock_path}. "
                "Stop the duplicate instance before starting a second copy."
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        with suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _extract_retry_after(exc: discord.HTTPException) -> float:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) if response else {}
    try:
        return max(0.0, float(headers.get("Retry-After", 0)))
    except (TypeError, ValueError):
        return 0.0


async def _sleep_interruptible(
    shutdown_event: asyncio.Event,
    seconds: float,
    sleep_fn: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> None:
    if seconds <= 0 or shutdown_event.is_set():
        return

    async def _do_sleep() -> None:
        await sleep_fn(seconds)

    wait_task: asyncio.Task[bool] = asyncio.create_task(shutdown_event.wait())
    sleep_task: asyncio.Task[None] = asyncio.create_task(_do_sleep())
    try:
        done, pending = await asyncio.wait(
            {wait_task, sleep_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        for task in done:
            # Propagate non-cancellation exceptions if custom sleep_fn raises.
            task.result()
    except BaseException:
        for task in (wait_task, sleep_task):
            if not task.done():
                task.cancel()
        raise


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    on_shutdown: Callable[[], None],
) -> list[signal.Signals]:
    installed: list[signal.Signals] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, on_shutdown)
            installed.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    return installed


async def create_health_app(
    bot_getter: Callable[[], BloxBerryBot | None],
) -> web.Application:
    """Create aiohttp web application for Render health checks."""
    app = web.Application()

    async def health_handler(_request: web.Request) -> web.Response:
        bot = bot_getter()
        discord_connected = bot is not None and not bot.is_closed()
        discord_ready = discord_connected and getattr(bot, "is_ready", lambda: False)()

        data = {
            "status": "ok",
            "service": "bloxberry-bot",
            "discord": {
                "connected": discord_connected,
                "ready": bool(discord_ready),
                "user": str(bot.user) if bot and bot.user else None,
            },
        }
        return web.json_response(data, status=200)

    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    return app


async def start_health_server(
    settings: Settings,
    bot_getter: Callable[[], BloxBerryBot | None],
) -> web.AppRunner | None:
    """Start the asynchronous health server on 0.0.0.0:$PORT without blocking."""
    app = await create_health_app(bot_getter)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.http_port)
    try:
        await site.start()
        logger.info(
            "[HTTP] Health server started on 0.0.0.0:%d (endpoints: / and /health).",
            settings.http_port,
        )
        return runner
    except Exception as exc:
        logger.error("[HTTP] Failed to start health server on port %d: %s", settings.http_port, exc)
        await runner.cleanup()
        return None


async def run_bot_with_backoff(
    settings: Settings,
    *,
    bot_factory: Callable[[Settings, StateStore], BloxBerryBot] | None = None,
    sleep_fn: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    install_signals: bool = True,
    start_http: bool = True,
) -> int:
    """Run the Discord bot as a resilient worker with rate-limit and transient retry handling."""
    if not settings.discord_token:
        logger.critical(
            "Missing DISCORD_TOKEN. Add your Discord bot token to the environment before starting."
        )
        return 1

    factory = bot_factory or (lambda cfg, store: BloxBerryBot(settings=cfg, state_store=store))
    shared_state = StateStore(settings.data_file, settings.roles.main_roles.keys())

    shutdown_event = asyncio.Event()
    active_bot: BloxBerryBot | None = None
    close_tasks: set[asyncio.Task[None]] = set()
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        if not shutdown_event.is_set():
            logger.info("[LIFECYCLE] Shutdown signal received; closing Discord connection...")
            shutdown_event.set()
        if active_bot is not None and not active_bot.is_closed():
            task = loop.create_task(active_bot.close())
            close_tasks.add(task)
            task.add_done_callback(close_tasks.discard)

    installed_signals = _install_signal_handlers(loop, request_shutdown) if install_signals else []

    http_runner: web.AppRunner | None = None
    if start_http:
        try:
            http_runner = await start_health_server(settings, lambda: active_bot)
        except Exception as exc:
            logger.error("[HTTP] Unexpected error starting health server: %s", exc)

    delay_seconds = settings.retry_base_delay_seconds

    try:
        while not shutdown_event.is_set():
            bot = factory(settings, shared_state)
            active_bot = bot
            wait_for = delay_seconds

            try:
                logger.info("[DISCORD] Connecting to Discord...")
                async with bot:
                    await bot.start(settings.discord_token)

                if shutdown_event.is_set():
                    break

                delay_seconds = settings.retry_base_delay_seconds
                wait_for = 5.0
                logger.warning(
                    "[DISCORD] bot.start() returned unexpectedly; reconnecting in %.0fs.",
                    wait_for,
                )
            except (discord.LoginFailure, discord.PrivilegedIntentsRequired) as exc:
                logger.critical(
                    "[DISCORD FATAL] Cannot authenticate or start with current configuration: %s: %s",
                    type(exc).__name__,
                    exc,
                )
                return 1
            except discord.RateLimited as exc:
                wait_for = max(delay_seconds, exc.retry_after)
                log_discord_http_error(logger, "Discord startup/gateway rate limit", exc)
                logger.warning(
                    "[DISCORD] Rate limited; waiting %.1fs (%.1f min) before retrying.",
                    wait_for,
                    wait_for / 60.0,
                )
                delay_seconds = min(
                    max(delay_seconds * 2, settings.retry_base_delay_seconds),
                    settings.retry_max_delay_seconds,
                )
            except discord.HTTPException as exc:
                if exc.status == 429:
                    retry_after = _extract_retry_after(exc)
                    wait_for = max(delay_seconds, retry_after) if retry_after > 0 else delay_seconds
                    log_discord_http_error(logger, "Discord gateway/login connection", exc)
                    logger.warning(
                        "[DISCORD] Login/API returned HTTP 429. Respecting Discord cooldown and "
                        "retrying in %d seconds (%.1f minutes).",
                        int(wait_for),
                        wait_for / 60.0,
                    )
                    delay_seconds = min(
                        max(delay_seconds * 2, settings.retry_base_delay_seconds),
                        settings.retry_max_delay_seconds,
                    )
                elif exc.status >= 500 or exc.status == 408:
                    wait_for = delay_seconds
                    log_discord_http_error(logger, "Discord server error during connection", exc)
                    logger.warning(
                        "[DISCORD] Transient HTTP %s; retrying in %d seconds.",
                        exc.status,
                        int(wait_for),
                    )
                    delay_seconds = min(
                        max(delay_seconds * 2, settings.retry_base_delay_seconds),
                        settings.retry_max_delay_seconds,
                    )
                else:
                    log_discord_http_error(logger, "Fatal Discord HTTP error", exc)
                    return 1
            except (
                TimeoutError,
                aiohttp.ClientError,
                OSError,
                discord.GatewayNotFound,
                discord.ConnectionClosed,
            ) as exc:
                wait_for = delay_seconds
                logger.warning(
                    "[DISCORD] Transient connection error (%s: %s); retrying in %d seconds.",
                    type(exc).__name__,
                    exc,
                    int(wait_for),
                )
                delay_seconds = min(
                    max(delay_seconds * 2, settings.retry_base_delay_seconds),
                    settings.retry_max_delay_seconds,
                )
            finally:
                active_bot = None
                if not bot.is_closed():
                    with suppress(Exception):
                        await bot.close()

            await _sleep_interruptible(shutdown_event, wait_for, sleep_fn=sleep_fn)
    finally:
        for sig in installed_signals:
            with suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(sig)

        if http_runner is not None:
            logger.info("[HTTP] Shutting down health server...")
            with suppress(Exception):
                await http_runner.cleanup()

    logger.info("[LIFECYCLE] BloxBerry Labs bot stopped cleanly.")
    return 0


def main() -> int:
    """Load settings, configure logging, enforce single-instance lock, and run the bot."""
    try:
        settings = load_settings()
    except ValueError as exc:
        configure_logging("INFO")
        logger.critical("Configuration error: %s", exc)
        return 1

    configure_logging(settings.log_level, secrets=(settings.discord_token,))

    if not settings.discord_token:
        logger.critical(
            "Missing DISCORD_TOKEN. Set DISCORD_TOKEN in your environment or Render Environment tab."
        )
        return 1

    try:
        with acquire_instance_lock(settings.instance_lock_file):
            return asyncio.run(run_bot_with_backoff(settings))
    except SingleInstanceError as exc:
        logger.critical("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
