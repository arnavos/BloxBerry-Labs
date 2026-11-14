"""Tests for configuration defaults, environment parsing, and logging diagnostics."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import discord
import pytest

from bot.config import (
    APPLICATION_REVIEW_CHANNEL_ID,
    AUTO_MEMBER_ROLE_ID,
    COLOR,
    MAIN_PANEL_MARKER,
    MAIN_RECRUITMENT_CHANNEL_ID,
    MAIN_ROLES,
    PENDING_APPLICATION_ROLE_ID,
    PURGE_AUTH_ROLE_ID,
    RECRUITMENT_REVIEWER_ROLE_IDS,
    SEAT_LIMITS,
    SKILL_PANEL_MARKER,
    SKILL_RECRUITMENT_CHANNEL_ID,
    SKILL_ROLES,
    TICKET_CATEGORY_ID,
    TICKET_PANEL_CHANNEL_ID,
    TICKET_PANEL_MARKER,
    TICKET_TYPES,
    TICKETS_ACCESS_ROLE_ID,
    TRANSCRIPT_CHANNEL_ID,
    WELCOME_CHANNEL_ID,
    load_settings,
)
from bot.utils import TokenRedactingFilter, log_discord_http_error


def test_domain_constants_match_baseline() -> None:
    assert MAIN_RECRUITMENT_CHANNEL_ID == 1571114752653131836
    assert SKILL_RECRUITMENT_CHANNEL_ID == 1571128273344401508
    assert APPLICATION_REVIEW_CHANNEL_ID == 1571114159548923948
    assert TICKET_PANEL_CHANNEL_ID == 1567899862022758564
    assert TICKET_CATEGORY_ID == 1571094794933895241
    assert TRANSCRIPT_CHANNEL_ID == 1571110540414292139
    assert WELCOME_CHANNEL_ID == 1567899844062740510

    assert AUTO_MEMBER_ROLE_ID == 1568182409957081108
    assert PENDING_APPLICATION_ROLE_ID == 1571120110830100652
    assert TICKETS_ACCESS_ROLE_ID == 1571063785081536562
    assert PURGE_AUTH_ROLE_ID == 1567599630030610502

    assert len(RECRUITMENT_REVIEWER_ROLE_IDS) == 6
    assert set(MAIN_ROLES.keys()) == set(SEAT_LIMITS.keys())
    assert len(MAIN_ROLES) == 9
    assert len(SKILL_ROLES) == 12
    assert len(TICKET_TYPES) == 6
    assert COLOR == 0xE91EAD
    assert MAIN_PANEL_MARKER == "BloxBerry Labs • Main Recruitment"
    assert SKILL_PANEL_MARKER == "BloxBerry Labs • Skill Recruitment"
    assert TICKET_PANEL_MARKER == "BloxBerry Labs • Ticket Panel"


def test_load_settings_defaults_and_overrides() -> None:
    defaults = load_settings({})
    assert defaults.discord_token == ""
    assert str(defaults.data_file) == "bloxberry_data.json"
    assert defaults.sync_commands_mode == "auto"
    assert defaults.discord_guild_id is None
    assert defaults.max_ratelimit_timeout == 60.0

    custom = load_settings(
        {
            "DISCORD_TOKEN": "  my-token  ",
            "DATA_FILE": "/var/data/state.json",
            "LOG_LEVEL": "debug",
            "SYNC_COMMANDS_MODE": "always",
            "DISCORD_GUILD_ID": "123456789",
            "PANEL_REFRESH_DEBOUNCE_SECONDS": "3.0",
            "MAX_RATELIMIT_TIMEOUT": "none",
            "RETRY_BASE_DELAY_SECONDS": "30",
            "RETRY_MAX_DELAY_SECONDS": "600",
            "INSTANCE_LOCK_FILE": "off",
        }
    )
    assert custom.discord_token == "my-token"
    assert str(custom.data_file) == "/var/data/state.json"
    assert custom.log_level == "DEBUG"
    assert custom.sync_commands_mode == "always"
    assert custom.discord_guild_id == 123456789
    assert custom.panel_refresh_debounce_seconds == 3.0
    assert custom.max_ratelimit_timeout is None
    assert custom.retry_base_delay_seconds == 30.0
    assert custom.retry_max_delay_seconds == 600.0
    assert custom.instance_lock_file is None


@pytest.mark.parametrize(
    "env",
    [
        {"SYNC_COMMANDS_MODE": "bogus"},
        {"DISCORD_GUILD_ID": "not-an-int"},
        {"DISCORD_GUILD_ID": "-5"},
        {"MAX_RATELIMIT_TIMEOUT": "10"},
        {"RETRY_BASE_DELAY_SECONDS": "100", "RETRY_MAX_DELAY_SECONDS": "50"},
    ],
)
def test_load_settings_validation_errors(env: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        load_settings(env)


def test_token_redacting_filter_and_429_logging(caplog: pytest.LogCaptureFixture) -> None:
    token = "super.secret.bot.token"
    flt = TokenRedactingFilter((token,))
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "Connecting with %s", (token,), None
    )
    assert flt.filter(record) is True
    assert token not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()

    response = MagicMock()
    response.status = 429
    response.reason = "Too Many Requests"
    response.headers = {
        "Retry-After": "42",
        "X-RateLimit-Global": "true",
        "X-RateLimit-Scope": "global",
        "X-RateLimit-Bucket": "abcd",
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset-After": "42.0",
    }
    exc = discord.HTTPException(response, "You are being rate limited.")

    test_logger = logging.getLogger("test.429")
    with caplog.at_level(logging.WARNING):
        log_discord_http_error(test_logger, "test context", exc)
    assert "[DISCORD 429]" in caplog.text
    assert "retry_after=42" in caplog.text
    assert "global=true" in caplog.text
    assert "bucket=abcd" in caplog.text
