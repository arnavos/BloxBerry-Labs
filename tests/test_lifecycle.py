"""Tests for bot lifecycle, backoff on 429/transient errors, signals, and single-instance lock."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import discord
import pytest

from bot.client import BloxBerryBot
from bot.config import load_settings
from bot.database import StateStore
from bot.main import SingleInstanceError, acquire_instance_lock, run_bot_with_backoff


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
