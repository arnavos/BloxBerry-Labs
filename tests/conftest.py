"""Shared mock factories and fixtures for Discord.py unit and integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.client import BloxBerryBot
from bot.config import MAIN_ROLES, SKILL_ROLES, Settings, load_settings
from bot.database import StateStore


def make_role(role_id: int, name: str = "Role") -> MagicMock:
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    role.mention = f"<@&{role_id}>"
    return role


def make_member(
    user_id: int,
    *,
    name: str = "tester",
    display_name: str | None = None,
    roles: list[MagicMock] | None = None,
    administrator: bool = False,
    bot: bool = False,
    joined_at: datetime | None = None,
) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.name = name
    member.display_name = display_name or name
    member.mention = f"<@{user_id}>"
    member.bot = bot
    member.roles = list(roles or [])
    member.joined_at = joined_at or datetime(2026, 1, 1, tzinfo=UTC)
    member.guild_permissions = discord.Permissions(administrator=administrator)
    member.display_avatar = MagicMock()
    member.display_avatar.url = f"https://cdn.example.com/avatars/{user_id}.png"
    member.dm_channel = None
    dm_channel = MagicMock(spec=discord.DMChannel)
    dm_channel.send = AsyncMock()
    member.create_dm = AsyncMock(return_value=dm_channel)
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    member.__str__ = MagicMock(return_value=f"{name}#0001")  # type: ignore[method-assign]
    return member


def make_text_channel(
    channel_id: int,
    *,
    name: str = "general",
    guild: MagicMock | None = None,
    permissions: discord.Permissions | None = None,
) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = name
    channel.mention = f"<#{channel_id}>"
    channel.guild = guild
    perms = permissions or discord.Permissions.all()
    channel.permissions_for = MagicMock(return_value=perms)
    sent_msg = MagicMock(spec=discord.Message)
    sent_msg.id = 999000111
    sent_msg.guild = guild
    sent_msg.embeds = []
    sent_msg.components = []
    sent_msg.edit = AsyncMock(return_value=sent_msg)
    channel.send = AsyncMock(return_value=sent_msg)
    channel.set_permissions = AsyncMock()
    channel.delete = AsyncMock()
    channel.delete_messages = AsyncMock()
    channel.fetch_message = AsyncMock()
    return channel


def make_guild(
    *,
    guild_id: int = 111222333,
    name: str = "BloxBerry Labs",
    member_count: int = 42,
    settings: Settings | None = None,
) -> MagicMock:
    cfg = settings or Settings()
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.name = name
    guild.member_count = member_count
    guild.icon = MagicMock()
    guild.icon.url = "https://cdn.example.com/icons/guild.png"
    guild.default_role = make_role(guild_id, "@everyone")

    roles_by_id: dict[int, MagicMock] = {
        cfg.roles.auto_member: make_role(cfg.roles.auto_member, "Member"),
        cfg.roles.pending_application: make_role(cfg.roles.pending_application, "Pending"),
        cfg.roles.tickets_access: make_role(cfg.roles.tickets_access, "Tickets Access"),
        cfg.roles.purge_auth: make_role(cfg.roles.purge_auth, "Purge Auth"),
    }
    for rname, rid in MAIN_ROLES.items():
        roles_by_id[rid] = make_role(rid, rname)
    for rname, rid in SKILL_ROLES.items():
        roles_by_id[rid] = make_role(rid, rname)
    for rid in cfg.roles.recruitment_reviewers:
        roles_by_id.setdefault(rid, make_role(rid, f"Reviewer-{rid}"))
    for dept in cfg.ticket_types.values():
        for rid in (*dept.ping, *dept.access, *dept.claim):
            roles_by_id.setdefault(rid, make_role(rid, f"Staff-{rid}"))

    guild.get_role = MagicMock(side_effect=lambda rid: roles_by_id.get(rid))
    guild._roles_by_id = roles_by_id

    bot_member = make_member(9000001, name="BloxBerryBot", bot=True, administrator=True)
    guild.me = bot_member
    guild.members = [bot_member]
    guild._members_by_id = {bot_member.id: bot_member}
    guild.get_member = MagicMock(side_effect=lambda uid: guild._members_by_id.get(uid))
    guild.fetch_member = AsyncMock(side_effect=lambda uid: guild._members_by_id.get(uid))

    channels_by_id: dict[int, Any] = {}
    guild._channels_by_id = channels_by_id
    guild.get_channel = MagicMock(side_effect=lambda cid: channels_by_id.get(cid))
    guild.fetch_channel = AsyncMock(side_effect=lambda cid: channels_by_id.get(cid))
    guild.create_text_channel = AsyncMock()
    return guild


def make_interaction(
    *,
    client: BloxBerryBot,
    user: MagicMock,
    guild: MagicMock,
    channel: MagicMock | None = None,
    message: MagicMock | None = None,
) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.client = client
    interaction.user = user
    interaction.guild = guild
    interaction.channel = channel
    interaction.message = message

    is_done_state = {"done": False}

    async def _send_message(*args: Any, **kwargs: Any) -> None:
        is_done_state["done"] = True

    async def _defer(*args: Any, **kwargs: Any) -> None:
        is_done_state["done"] = True

    async def _edit_message(*args: Any, **kwargs: Any) -> None:
        is_done_state["done"] = True

    async def _send_modal(*args: Any, **kwargs: Any) -> None:
        is_done_state["done"] = True

    interaction.response = MagicMock()
    interaction.response.is_done = MagicMock(side_effect=lambda: is_done_state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_send_message)
    interaction.response.defer = AsyncMock(side_effect=_defer)
    interaction.response.edit_message = AsyncMock(side_effect=_edit_message)
    interaction.response.send_modal = AsyncMock(side_effect=_send_modal)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return load_settings(
        {
            "DISCORD_TOKEN": "fake.test.token.value",
            "DATA_FILE": str(tmp_path / "bloxberry_data.json"),
            "SYNC_COMMANDS_MODE": "auto",
            "PANEL_REFRESH_DEBOUNCE_SECONDS": "0.1",
            "INSTANCE_LOCK_FILE": "none",
        }
    )


@pytest.fixture
def state_store(test_settings: Settings) -> StateStore:
    return StateStore(test_settings.data_file, test_settings.roles.main_roles.keys())


@pytest.fixture
def bot_instance(test_settings: Settings, state_store: StateStore) -> BloxBerryBot:
    return BloxBerryBot(settings=test_settings, state_store=state_store)
