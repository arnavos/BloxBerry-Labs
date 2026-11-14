"""Tests for member join welcome flow and role-based panel refresh triggers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord

from bot.client import BloxBerryBot
from bot.config import MAIN_ROLES, SKILL_ROLES
from bot.events.member_events import MemberEventsCog
from tests.conftest import make_guild, make_member, make_text_channel


async def test_on_member_join_assigns_role_and_posts_welcome_even_if_role_forbidden(
    bot_instance: BloxBerryBot,
) -> None:
    cog = MemberEventsCog(bot_instance)
    guild = make_guild(member_count=128, settings=bot_instance.settings)
    welcome_ch = make_text_channel(
        bot_instance.settings.channels.welcome,
        name="welcome",
        guild=guild,
        permissions=discord.Permissions(view_channel=True, send_messages=True, embed_links=True),
    )
    guild._channels_by_id[welcome_ch.id] = welcome_ch

    # Simulate Forbidden on role assignment — welcome message must still post
    resp = MagicMock()
    resp.status = 403
    resp.reason = "Forbidden"
    member = make_member(8001, name="newberry")
    member.guild = guild
    member.add_roles = AsyncMock(side_effect=discord.Forbidden(resp, "Missing Permissions"))

    await cog.on_member_join(member)

    member.add_roles.assert_awaited_once()
    welcome_ch.send.assert_awaited_once()
    send_kwargs = welcome_ch.send.await_args.kwargs
    embed = send_kwargs["embed"]
    assert embed.title == "🍓 A new berry just joined!"
    assert any("**#128**" in f.value for f in embed.fields)


async def test_on_member_join_plain_text_fallback_when_no_embed_links(
    bot_instance: BloxBerryBot,
) -> None:
    cog = MemberEventsCog(bot_instance)
    guild = make_guild(member_count=77, settings=bot_instance.settings)
    welcome_ch = make_text_channel(
        bot_instance.settings.channels.welcome,
        name="welcome",
        guild=guild,
        permissions=discord.Permissions(view_channel=True, send_messages=True, embed_links=False),
    )
    guild._channels_by_id[welcome_ch.id] = welcome_ch

    member = make_member(8002, name="plainberry")
    member.guild = guild
    await cog.on_member_join(member)

    send_kwargs = welcome_ch.send.await_args.kwargs
    assert "embed" not in send_kwargs
    assert "#77" in send_kwargs["content"]


async def test_member_update_and_remove_only_refresh_for_main_roles(
    bot_instance: BloxBerryBot,
) -> None:
    cog = MemberEventsCog(bot_instance)
    guild = make_guild(settings=bot_instance.settings)
    bot_instance.panel_service.schedule_main_panel_refresh = MagicMock()  # type: ignore[method-assign]

    skill_role = guild.get_role(SKILL_ROLES["Programmers"])
    main_role = guild.get_role(MAIN_ROLES["Art Director"])
    auto_role = guild.get_role(bot_instance.settings.roles.auto_member)

    # Skill/auto role change should NOT trigger refresh
    before = make_member(8003, roles=[auto_role])
    after_skill = make_member(8003, roles=[auto_role, skill_role])
    await cog.on_member_update(before, after_skill)
    bot_instance.panel_service.schedule_main_panel_refresh.assert_not_called()

    # Main role change MUST trigger refresh
    after_main = make_member(8003, roles=[auto_role, main_role])
    await cog.on_member_update(before, after_main)
    bot_instance.panel_service.schedule_main_panel_refresh.assert_called_once()

    # Departure without main role should NOT trigger refresh; with main role MUST trigger
    bot_instance.panel_service.schedule_main_panel_refresh.reset_mock()
    await cog.on_member_remove(after_skill)
    bot_instance.panel_service.schedule_main_panel_refresh.assert_not_called()

    await cog.on_member_remove(after_main)
    bot_instance.panel_service.schedule_main_panel_refresh.assert_called_once()
