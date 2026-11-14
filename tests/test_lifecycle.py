"""Tests for bot lifecycle, backoff on 429/transient errors, signals, and single-instance lock."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import discord
import pytest

from bot.client import BloxBerryBot
from bot.config import load_settings
from bot.database import StateStore
from bot.main import (
    SingleInstanceError,
    acquire_instance_lock,
    create_health_app,
    run_bot_with_backoff,
    start_health_server,
)


def _make_fake_bot_cls(outcomes: list[BaseException | None]):
    """Build a fake BloxBerryBot factory producing sequential start() outcomes."""
    calls = {"index": 0}

    def _factory(settings, state_store):
        bot = MagicMock(spec=BloxBerryBot)
        bot.is_closed = MagicMock(return_value=False)
        bot.close = AsyncMock(side_effect=lambda: bot.is_closed.configure_mock(return_value=True))
        bot.__aenter__ = AsyncMock(return_value=bot)
        bot.__aexit__ = AsyncMock(return_value=None)

        idx = calls["index"]
        calls["index"] += 1
        outcome = outcomes[idx] if idx < len(outcomes) else None

        async def _start(_token: str) -> None:
            if outcome is not None:
                raise outcome

        bot.start = AsyncMock(side_effect=_start)
        return bot

    return _factory


def _http_exc(status: int, *, retry_after: str | None = None) -> discord.HTTPException:
    resp = MagicMock()
    resp.status = status
    resp.reason = "Rate Limited" if status == 429 else "Error"
    resp.headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return discord.HTTPException(resp, f"HTTP {status}")


async def test_missing_token_returns_exit_code_one(tmp_path: Path) -> None:
    settings = load_settings(
        {"DISCORD_TOKEN": "", "DATA_FILE": str(tmp_path / "s.json"), "INSTANCE_LOCK_FILE": "none"}
    )
    code = await run_bot_with_backoff(settings, install_signals=False)
    assert code == 1


@pytest.mark.parametrize(
    "fatal_exc_factory",
    [
        lambda: discord.LoginFailure("Improper token"),
        lambda: discord.PrivilegedIntentsRequired(shard_id=None),
        lambda: _http_exc(403),
    ],
)
async def test_fatal_auth_or_intent_errors_fail_fast(
    tmp_path: Path,
    fatal_exc_factory,
) -> None:
    settings = load_settings(
        {
            "DISCORD_TOKEN": "token",
            "DATA_FILE": str(tmp_path / "s.json"),
            "INSTANCE_LOCK_FILE": "none",
        }
    )
    sleep_calls: list[float] = []

    async def _fake_sleep(sec: float) -> None:
        sleep_calls.append(sec)

    code = await run_bot_with_backoff(
        settings,
        bot_factory=_make_fake_bot_cls([fatal_exc_factory()]),
        sleep_fn=_fake_sleep,
        install_signals=False,
    )
    assert code == 1
    assert sleep_calls == []


async def test_rate_limit_429_respects_retry_after_and_exponential_backoff(tmp_path: Path) -> None:
    settings = load_settings(
        {
            "DISCORD_TOKEN": "token",
            "DATA_FILE": str(tmp_path / "s.json"),
            "RETRY_BASE_DELAY_SECONDS": "60",
            "RETRY_MAX_DELAY_SECONDS": "900",
            "INSTANCE_LOCK_FILE": "none",
        }
    )
    sleep_calls: list[float] = []

    async def _fake_sleep(sec: float) -> None:
        sleep_calls.append(sec)
        if len(sleep_calls) >= 3:
            # Trigger KeyboardInterrupt/abort cleanly via LoginFailure on 4th attempt
            return

    outcomes: list[BaseException | None] = [
        _http_exc(429, retry_after="120"),  # max(60, 120) = 120, next delay=120
        discord.RateLimited(retry_after=45.0),  # max(120, 45) = 120, next delay=240
        aiohttp.ClientError("network blip"),  # delay=240, next delay=480
        discord.LoginFailure("stop test"),
    ]

    code = await run_bot_with_backoff(
        settings,
        bot_factory=_make_fake_bot_cls(outcomes),
        sleep_fn=_fake_sleep,
        install_signals=False,
    )
    assert code == 1
    assert sleep_calls == [120.0, 120.0, 240.0]


def test_single_instance_lock_prevents_concurrent_process(tmp_path: Path) -> None:
    lock_file = tmp_path / "bot.lock"
    with (
        acquire_instance_lock(lock_file),
        pytest.raises(SingleInstanceError),
        acquire_instance_lock(lock_file),
    ):
        pass
    # Lock released after exiting context manager
    with acquire_instance_lock(lock_file):
        assert lock_file.exists()


async def test_interruptible_sleep_wakes_immediately_on_shutdown() -> None:
    from bot.main import _sleep_interruptible

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.call_later(0.05, shutdown_event.set)
    started = loop.time()
    await _sleep_interruptible(shutdown_event, 60.0)
    elapsed = loop.time() - started
    assert elapsed < 1.0


async def test_bot_setup_hook_and_clean_close(tmp_path: Path) -> None:
    settings = load_settings(
        {
            "DISCORD_TOKEN": "token",
            "DATA_FILE": str(tmp_path / "s.json"),
            "SYNC_COMMANDS_MODE": "never",
            "INSTANCE_LOCK_FILE": "none",
        }
    )
    store = StateStore(settings.data_file, settings.roles.main_roles.keys())
    bot = BloxBerryBot(settings=settings, state_store=store)
    async with bot:
        await bot.setup_hook()
        assert "TicketCommandsCog" in bot.cogs
        assert "MemberEventsCog" in bot.cogs
        assert "MessageEventsCog" in bot.cogs
        # Schedule a refresh task and ensure close() cancels it cleanly
        bot.panel_service._refresh_pending = True
        bot.panel_service._refresh_task = asyncio.create_task(asyncio.sleep(10))

    assert bot.is_closed()
    assert bot.panel_service._refresh_task is None


def test_cli_entrypoint_exits_cleanly_without_token(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["DISCORD_TOKEN"] = ""
    env["DATA_FILE"] = str(tmp_path / "state.json")
    env["INSTANCE_LOCK_FILE"] = "none"

    result = subprocess.run(
        [sys.executable, "main.py"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 1
    assert "Missing DISCORD_TOKEN" in result.stdout
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


async def test_http_health_endpoint_and_readiness() -> None:
    bot_ref: list[MagicMock | None] = [None]
    app = await create_health_app(lambda: bot_ref[0])

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    # Ephemeral port
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()

    # Get bound port
    assert site._server is not None
    sockets = site._server.sockets
    assert sockets
    port = sockets[0].getsockname()[1]

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Bot is None (initializing/connecting)
            async with session.get(f"http://127.0.0.1:{port}/health") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"
                assert data["service"] == "bloxberry-bot"
                assert data["discord"]["connected"] is False
                assert data["discord"]["ready"] is False
                assert data["discord"]["user"] is None

            # Test root route /
            async with session.get(f"http://127.0.0.1:{port}/") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"

            # 2. Bot connected but not ready
            mock_bot = MagicMock(spec=BloxBerryBot)
            mock_bot.is_closed.return_value = False
            mock_bot.is_ready.return_value = False
            mock_bot.user = None
            bot_ref[0] = mock_bot

            async with session.get(f"http://127.0.0.1:{port}/health") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["discord"]["connected"] is True
                assert data["discord"]["ready"] is False

            # 3. Bot connected and ready with user
            mock_bot.is_ready.return_value = True
            mock_user = MagicMock()
            mock_user.__str__.return_value = "BloxBerryBot#1234"
            mock_bot.user = mock_user

            async with session.get(f"http://127.0.0.1:{port}/health") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["discord"]["connected"] is True
                assert data["discord"]["ready"] is True
                assert data["discord"]["user"] == "BloxBerryBot#1234"
    finally:
        await runner.cleanup()


async def test_start_health_server_startup_and_shutdown(tmp_path: Path) -> None:
    # Test valid startup on free port
    settings = load_settings(
        {
            "DISCORD_TOKEN": "token",
            "DATA_FILE": str(tmp_path / "s.json"),
            "PORT": "0",  # OS assigns ephemeral port
            "INSTANCE_LOCK_FILE": "none",
        }
    )
    runner = await start_health_server(settings, lambda: None)
    assert runner is not None
    await runner.cleanup()


async def test_run_bot_with_backoff_http_reconnection_and_graceful_shutdown(tmp_path: Path) -> None:
    # Use ephemeral port
    settings = load_settings(
        {
            "DISCORD_TOKEN": "token",
            "DATA_FILE": str(tmp_path / "s.json"),
            "PORT": "0",
            "RETRY_BASE_DELAY_SECONDS": "5",
            "RETRY_MAX_DELAY_SECONDS": "10",
            "INSTANCE_LOCK_FILE": "none",
        }
    )

    reconnect_attempts = 0

    class ControlledBot:
        def __init__(self, *args, **kwargs):
            self.is_closed = MagicMock(return_value=False)
            self.user = MagicMock()
            self.user.__str__.return_value = "TestBot#0001"
            self.is_ready = MagicMock(return_value=True)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            self.is_closed.return_value = True

        async def close(self):
            self.is_closed.return_value = True

        async def start(self, token: str):
            nonlocal reconnect_attempts
            reconnect_attempts += 1
            if reconnect_attempts == 1:
                # First run: raise transient error to simulate disconnect/reconnect
                raise aiohttp.ClientError("transient network blip")
            elif reconnect_attempts == 2:
                # Second run: stay running until stopped
                await asyncio.sleep(100)

    # Let sleep_fn advance reconnect quickly
    async def fast_sleep(_sec: float) -> None:
        await asyncio.sleep(0.01)

    # Run bot in a background task
    bot_task = asyncio.create_task(
        run_bot_with_backoff(
            settings,
            bot_factory=lambda cfg, store: ControlledBot(),
            sleep_fn=fast_sleep,
            install_signals=False,
            start_http=True,
        )
    )

    # Allow bot and HTTP server to start and trigger reconnect
    await asyncio.sleep(0.1)
    assert reconnect_attempts >= 2

    # Clean shutdown
    bot_task.cancel()
    with suppress(asyncio.CancelledError):
        await bot_task
