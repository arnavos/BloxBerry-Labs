"""Tests for message-based `purge <N>` command parsing, authorization, and deletion."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import discord

from bot.client import BloxBerryBot
from bot.events.message_events import MessageEventsCog
from tests.conftest import make_guild, make_member, make_text_channel


def _make_message(
    msg_id: int,
    *,
    content: str,
    author: MagicMock,
    guild: MagicMock | None,
    channel: MagicMock,
    age_days: int = 1,
) -> MagicMock:
    msg = MagicMock(spec=discord.Message)
    msg.id = msg_id
    msg.content = content
    msg.author = author
    msg.guild = guild
    msg.channel = channel
    msg.created_at = discord.utils.utcnow() - timedelta(days=age_days)
    msg.reply = AsyncMock()
    msg.delete = AsyncMock()
    return msg


async def test_purge_command_validation_and_role_check(bot_instance: BloxBerryBot) -> None:
    cog = MessageEventsCog(bot_instance)
    guild = make_guild(settings=bot_instance.settings)
    ch = make_text_channel(555001, guild=guild)

    # Admin WITHOUT PURGE_AUTH_ROLE_ID must still be denied (exact original parity)
    admin_no_role = make_member(7001, administrator=True, roles=[])
    bad_syntax = _make_message(1, content="purge", author=admin_no_role, guild=guild, channel=ch)
    await cog.on_message(bad_syntax)
    bad_syntax.reply.assert_awaited_once()
    assert bad_syntax.reply.await_args.kwargs["delete_after"] == 7

    out_of_range = _make_message(
        2, content="purge 1001", author=admin_no_role, guild=guild, channel=ch
    )
    await cog.on_message(out_of_range)
    assert "1 to 1000" in out_of_range.reply.await_args.args[0]

    unauth = _make_message(3, content="PuRgE 5", author=admin_no_role, guild=guild, channel=ch)
    await cog.on_message(unauth)
    unauth.reply.assert_awaited_once_with("You are not authorized to use purge.", delete_after=5)


async def test_purge_deletes_recent_and_old_messages_without_invalid_reason_kwarg(
    bot_instance: BloxBerryBot,
) -> None:
    cog = MessageEventsCog(bot_instance)
    guild = make_guild(settings=bot_instance.settings)
    ch = make_text_channel(555002, guild=guild)
    purge_role = guild.get_role(bot_instance.settings.roles.purge_auth)
    mod = make_member(7002, roles=[purge_role])

    cmd_msg = _make_message(100, content="purge 4", author=mod, guild=guild, channel=ch)
    recent_a = _make_message(99, content="a", author=mod, guild=guild, channel=ch, age_days=1)
    recent_b = _make_message(98, content="b", author=mod, guild=guild, channel=ch, age_days=2)
    old_c = _make_message(97, content="c", author=mod, guild=guild, channel=ch, age_days=20)
    old_d = _make_message(96, content="d", author=mod, guild=guild, channel=ch, age_days=30)

    captured_history_kwargs: dict[str, object] = {}

    async def _history(*args: object, **kwargs: object):
        captured_history_kwargs.update(kwargs)
        for m in (recent_a, recent_b, old_c, old_d):
            yield m

    ch.history = MagicMock(side_effect=_history)

    await cog.on_message(cmd_msg)

    assert captured_history_kwargs["limit"] == 4
    assert captured_history_kwargs["before"] is cmd_msg
    ch.delete_messages.assert_awaited_once_with(
        [recent_a, recent_b], reason=f"Purge requested by {mod}"
    )
    # Old message delete() does not take `reason` in discord.py 2.x
    old_c.delete.assert_awaited_once_with()
    old_d.delete.assert_awaited_once_with()
    cmd_msg.delete.assert_awaited_once_with()
