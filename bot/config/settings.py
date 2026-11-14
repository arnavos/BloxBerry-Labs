"""Centralized configuration and domain constants for BloxBerry Labs."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

# Channels / categories
MAIN_RECRUITMENT_CHANNEL_ID = 1571114752653131836
SKILL_RECRUITMENT_CHANNEL_ID = 1571128273344401508
APPLICATION_REVIEW_CHANNEL_ID = 1571114159548923948

TICKET_PANEL_CHANNEL_ID = 1567899862022758564
TICKET_CATEGORY_ID = 1571094794933895241
TRANSCRIPT_CHANNEL_ID = 1571110540414292139
WELCOME_CHANNEL_ID = 1567899844062740510

# Roles
AUTO_MEMBER_ROLE_ID = 1568182409957081108
PENDING_APPLICATION_ROLE_ID = 1571120110830100652
TICKETS_ACCESS_ROLE_ID = 1571063785081536562
PURGE_AUTH_ROLE_ID = 1567599630030610502

RECRUITMENT_REVIEWER_ROLE_IDS: frozenset[int] = frozenset(
    {
        1571062526232236223,  # Studio Director
        1571065103413944401,  # Executive Manager
        1571065681670180975,  # Project Manager
        1571065040390586409,  # Technical Director
        1571063896977055855,  # Creative Director
        1571064979686432869,  # Community Director
    }
)

MAIN_ROLES: Mapping[str, int] = MappingProxyType(
    {
        "Executive Manager": 1571065103413944401,
        "Project Manager": 1571065681670180975,
        "Technical Director": 1571065040390586409,
        "Creative Director": 1571063896977055855,
        "Game Design Lead": 1571065144010743818,
        "Art Director": 1571063939213566044,
        "Community Director": 1571064979686432869,
        "Marketing Team": 1571065232778989679,
        "Social Media Manager": 1571071998086611064,
    }
)

SEAT_LIMITS: Mapping[str, int] = MappingProxyType(
    {
        "Executive Manager": 1,
        "Project Manager": 2,
        "Technical Director": 2,
        "Creative Director": 2,
        "Game Design Lead": 2,
        "Art Director": 1,
        "Community Director": 1,
        "Marketing Team": 5,
        "Social Media Manager": 2,
    }
)

SKILL_ROLES: Mapping[str, int] = MappingProxyType(
    {
        "AI Lead Developer": 1571068709454348328,
        "Programmers": 1571068590235328563,
        "Game Testers": 1571078210907668480,
        "UI Designer": 1571068770112512000,
        "3D Model Designer": 1571068769978163250,
        "Animator": 1571068771911602196,
        "Character Designer": 1571074678481485954,
        "Map Designer": 1571071574751313972,
        "Texture Artist": 1571071230545625098,
        "VFX Artist": 1571071267723939941,
        "Community Mods": 1571071401249472512,
        "Ads Specialist": 1571073021630881833,
    }
)


@dataclass(frozen=True)
class TicketDepartmentConfig:
    """Routing and permissions for a single ticket department."""

    key: str
    ping: tuple[int, ...]
    access: tuple[int, ...]
    claim: tuple[int, ...]


TICKET_TYPES: Mapping[str, TicketDepartmentConfig] = MappingProxyType(
    {
        "Creative Department Request": TicketDepartmentConfig(
            key="creative",
            ping=(1571063896977055855,),
            access=(
                1571063939213566044,  # Art Director
                1571063896977055855,  # Creative Director
                1571065144010743818,  # Game Design Lead
            ),
            claim=(
                1571063939213566044,
                1571063896977055855,
                1571065144010743818,
            ),
        ),
        "Marketing Request": TicketDepartmentConfig(
            key="marketing",
            ping=(1571065232778989679,),
            access=(
                1571065232778989679,  # Marketing Team
                1571071998086611064,  # Social Media Manager
                1571073021630881833,  # Ads Specialist
            ),
            claim=(
                1571065232778989679,
                1571071998086611064,
                1571073021630881833,
            ),
        ),
        "Technical Support": TicketDepartmentConfig(
            key="technical",
            ping=(1571065040390586409,),
            access=(1571065040390586409,),  # Technical Director only
            claim=(1571065040390586409,),
        ),
        "Partnerships": TicketDepartmentConfig(
            key="partnerships",
            ping=(1571065232778989679,),
            access=(1571065232778989679,),  # Marketing Team
            claim=(1571065232778989679,),
        ),
        "General Inquiry": TicketDepartmentConfig(
            key="general",
            ping=(1571064979686432869,),
            access=(
                1571064979686432869,  # Community Director
                TICKETS_ACCESS_ROLE_ID,
            ),
            claim=(1571064979686432869,),
        ),
        "Member Report": TicketDepartmentConfig(
            key="report",
            ping=(1571062526232236223,),
            access=(
                1571062526232236223,  # Studio Director
                1571065681670180975,  # Project Manager
                1571065103413944401,  # Executive Manager
                TICKETS_ACCESS_ROLE_ID,
            ),
            claim=(
                1571062526232236223,
                1571065681670180975,
                1571065103413944401,
            ),
        ),
    }
)

DATA_FILE = "bloxberry_data.json"
COLOR = 0xE91EAD
MAIN_PANEL_MARKER = "BloxBerry Labs • Main Recruitment"
SKILL_PANEL_MARKER = "BloxBerry Labs • Skill Recruitment"
TICKET_PANEL_MARKER = "BloxBerry Labs • Ticket Panel"

VALID_SYNC_MODES = frozenset({"auto", "always", "never"})


@dataclass(frozen=True)
class ChannelConfig:
    """Discord channel and category IDs."""

    main_recruitment: int = MAIN_RECRUITMENT_CHANNEL_ID
    skill_recruitment: int = SKILL_RECRUITMENT_CHANNEL_ID
    application_review: int = APPLICATION_REVIEW_CHANNEL_ID
    ticket_panel: int = TICKET_PANEL_CHANNEL_ID
    ticket_category: int = TICKET_CATEGORY_ID
    transcript: int = TRANSCRIPT_CHANNEL_ID
    welcome: int = WELCOME_CHANNEL_ID


@dataclass(frozen=True)
class RoleConfig:
    """Discord role IDs and recruitment role mappings."""

    auto_member: int = AUTO_MEMBER_ROLE_ID
    pending_application: int = PENDING_APPLICATION_ROLE_ID
    tickets_access: int = TICKETS_ACCESS_ROLE_ID
    purge_auth: int = PURGE_AUTH_ROLE_ID
    recruitment_reviewers: frozenset[int] = RECRUITMENT_REVIEWER_ROLE_IDS
    main_roles: Mapping[str, int] = field(default_factory=lambda: MAIN_ROLES)
    seat_limits: Mapping[str, int] = field(default_factory=lambda: SEAT_LIMITS)
    skill_roles: Mapping[str, int] = field(default_factory=lambda: SKILL_ROLES)


@dataclass(frozen=True)
class Settings:
    """Runtime settings and domain configuration for the bot."""

    discord_token: str = ""
    data_file: Path = Path(DATA_FILE)
    log_level: str = "INFO"
    sync_commands_mode: str = "auto"
    discord_guild_id: int | None = None
    panel_refresh_debounce_seconds: float = 2.5
    max_ratelimit_timeout: float | None = 60.0
    retry_base_delay_seconds: float = 60.0
    retry_max_delay_seconds: float = 900.0
    instance_lock_file: Path | None = None
    color: int = COLOR
    main_panel_marker: str = MAIN_PANEL_MARKER
    skill_panel_marker: str = SKILL_PANEL_MARKER
    ticket_panel_marker: str = TICKET_PANEL_MARKER
    channels: ChannelConfig = field(default_factory=ChannelConfig)
    roles: RoleConfig = field(default_factory=RoleConfig)
    ticket_types: Mapping[str, TicketDepartmentConfig] = field(default_factory=lambda: TICKET_TYPES)


def _parse_optional_int(name: str, raw: str) -> int | None:
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        value = int(stripped)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {stripped!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value}")
    return value


def _parse_float(name: str, raw: str, *, minimum: float) -> float:
    stripped = raw.strip()
    try:
        value = float(stripped)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {stripped!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _parse_optional_timeout(name: str, raw: str) -> float | None:
    stripped = raw.strip()
    if not stripped or stripped.lower() == "none":
        return None
    return _parse_float(name, stripped, minimum=30.0)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load and validate runtime configuration from environment variables."""
    source = os.environ if env is None else env

    token = source.get("DISCORD_TOKEN", "").strip()
    data_file_raw = source.get("DATA_FILE", DATA_FILE).strip() or DATA_FILE
    log_level = (source.get("LOG_LEVEL", "INFO").strip() or "INFO").upper()

    sync_mode = (source.get("SYNC_COMMANDS_MODE", "auto").strip() or "auto").lower()
    if sync_mode not in VALID_SYNC_MODES:
        raise ValueError(
            f"SYNC_COMMANDS_MODE must be one of {sorted(VALID_SYNC_MODES)}, got {sync_mode!r}"
        )

    guild_id = _parse_optional_int("DISCORD_GUILD_ID", source.get("DISCORD_GUILD_ID", ""))
    debounce_seconds = _parse_float(
        "PANEL_REFRESH_DEBOUNCE_SECONDS",
        source.get("PANEL_REFRESH_DEBOUNCE_SECONDS", "2.5"),
        minimum=0.1,
    )
    max_ratelimit_timeout = _parse_optional_timeout(
        "MAX_RATELIMIT_TIMEOUT",
        source.get("MAX_RATELIMIT_TIMEOUT", "60.0"),
    )
    retry_base = _parse_float(
        "RETRY_BASE_DELAY_SECONDS",
        source.get("RETRY_BASE_DELAY_SECONDS", "60"),
        minimum=5.0,
    )
    retry_max = _parse_float(
        "RETRY_MAX_DELAY_SECONDS",
        source.get("RETRY_MAX_DELAY_SECONDS", "900"),
        minimum=retry_base,
    )

    lock_raw = source.get("INSTANCE_LOCK_FILE", ".bloxberry_bot.lock").strip()
    instance_lock_file = (
        None if lock_raw.lower() in {"", "none", "off", "false"} else Path(lock_raw)
    )

    return Settings(
        discord_token=token,
        data_file=Path(data_file_raw),
        log_level=log_level,
        sync_commands_mode=sync_mode,
        discord_guild_id=guild_id,
        panel_refresh_debounce_seconds=debounce_seconds,
        max_ratelimit_timeout=max_ratelimit_timeout,
        retry_base_delay_seconds=retry_base,
        retry_max_delay_seconds=retry_max,
        instance_lock_file=instance_lock_file,
    )
