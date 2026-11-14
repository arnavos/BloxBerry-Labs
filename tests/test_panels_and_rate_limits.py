"""Tests for panel REST-call avoidance, debouncing, and smart command tree syncing."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from bot.client import BloxBerryBot
from bot.config import MAIN_ROLES, load_settings
from bot.database import StateStore
from bot.services.panel_service import (
    build_main_recruitment_embed,
    build_skill_recruitment_embed,
    build_ticket_panel_embed,
)
from bot.views import MainPanelView, SkillPanelView, TicketPanelView
from tests.conftest import make_guild, make_member, make_text_channel


def _components_for_view(view: discord.ui.View) -> list[SimpleNamespace]:
    children = []
    for item in view.children:
        children.append(
            SimpleNamespace(
                custom_id=getattr(item, "custom_id", None),
                label=getattr(item, "label", None),
                placeholder=getattr(item, "placeholder", None),
                options=getattr(item, "options", None),
            )
        )
    return [SimpleNamespace(children=children)]


async def test_ensure_all_panels_uses_cache_and_skips_unchanged_edits(
    bot_instance: BloxBerryBot,
) -> None:
    guild = make_guild(settings=bot_instance.settings)
    bot_user = SimpleNamespace(id=9000001)
    bot_instance._connection.user = bot_user  # type: ignore[assignment]

    main_ch = make_text_channel(bot_instance.settings.channels.main_recruitment, guild=guild)
    skill_ch = make_text_channel(bot_instance.settings.channels.skill_recruitment, guild=guild)
    ticket_ch = make_text_channel(bot_instance.settings.channels.ticket_panel, guild=guild)

    main_view = MainPanelView()
    skill_view = SkillPanelView(bot_instance.settings)
    ticket_view = TicketPanelView(bot_instance.settings)

    main_msg = MagicMock(spec=discord.Message)
    main_msg.id = 11101
    main_msg.author = bot_user
    main_msg.guild = guild
    main_msg.embeds = [build_main_recruitment_embed(guild, bot_instance.settings)]
    main_msg.components = _components_for_view(main_view)
    main_msg.edit = AsyncMock(return_value=main_msg)

    skill_msg = MagicMock(spec=discord.Message)
    skill_msg.id = 11102
    skill_msg.author = bot_user
    skill_msg.guild = guild
    skill_msg.embeds = [build_skill_recruitment_embed(guild, bot_instance.settings)]
    skill_msg.components = _components_for_view(skill_view)
    skill_msg.edit = AsyncMock(return_value=skill_msg)

    ticket_msg = MagicMock(spec=discord.Message)
    ticket_msg.id = 11103
    ticket_msg.author = bot_user
    ticket_msg.guild = guild
    ticket_msg.embeds = [build_ticket_panel_embed(bot_instance.settings)]
    ticket_msg.components = _components_for_view(ticket_view)
    ticket_msg.edit = AsyncMock(return_value=ticket_msg)

    # Pre-seed known panel IDs and cached channels
    bot_instance.state_store.set_panel_message_id("main", main_msg.id)
    bot_instance.state_store.set_panel_message_id("skill", skill_msg.id)
    bot_instance.state_store.set_panel_message_id("ticket", ticket_msg.id)

    main_ch.fetch_message = AsyncMock(return_value=main_msg)
    skill_ch.fetch_message = AsyncMock(return_value=skill_msg)
    ticket_ch.fetch_message = AsyncMock(return_value=ticket_msg)
    main_ch.history = MagicMock()
    skill_ch.history = MagicMock()
    ticket_ch.history = MagicMock()

    channels_map = {main_ch.id: main_ch, skill_ch.id: skill_ch, ticket_ch.id: ticket_ch}
    bot_instance.get_channel = MagicMock(side_effect=lambda cid: channels_map.get(cid))  # type: ignore[method-assign]
    bot_instance.fetch_channel = AsyncMock()  # type: ignore[method-assign]

    ok = await bot_instance.panel_service.ensure_all_panels(
        main_view_factory=MainPanelView,
        skill_view_factory=lambda: SkillPanelView(bot_instance.settings),
        ticket_view_factory=lambda: TicketPanelView(bot_instance.settings),
    )

    assert ok is True
    assert bot_instance.panel_service.panels_ready is True
    # Zero fetch_channel calls, zero channel.history scans, zero redundant PATCH edits
    bot_instance.fetch_channel.assert_not_awaited()
    main_ch.history.assert_not_called()
    skill_ch.history.assert_not_called()
    ticket_ch.history.assert_not_called()
    main_msg.edit.assert_not_awaited()
    skill_msg.edit.assert_not_awaited()
    ticket_msg.edit.assert_not_awaited()


async def test_debounced_panel_refresh_coalesces_bursts_and_edits_only_on_change(
    bot_instance: BloxBerryBot,
) -> None:
    guild = make_guild(settings=bot_instance.settings)
    main_view = MainPanelView()
    panel_msg = MagicMock(spec=discord.Message)
    panel_msg.guild = guild
    panel_msg.embeds = [build_main_recruitment_embed(guild, bot_instance.settings)]
    panel_msg.components = _components_for_view(main_view)

    async def _edit(**kwargs: object) -> MagicMock:
        panel_msg.embeds = [kwargs["embed"]]  # type: ignore[list-item]
        return panel_msg

    panel_msg.edit = AsyncMock(side_effect=_edit)
    bot_instance.panel_service.main_panel_message = panel_msg

    # No seat change -> refresh skips edit
    await bot_instance.panel_service.refresh_main_panel(MainPanelView)
    panel_msg.edit.assert_not_awaited()

    # Add a main role holder -> burst of 5 schedule calls should produce 1 edit
    exec_role = guild.get_role(MAIN_ROLES["Executive Manager"])
    holder = make_member(9010, roles=[exec_role])
    guild.members.append(holder)
    for _ in range(5):
        bot_instance.panel_service.schedule_main_panel_refresh(MainPanelView)

    await asyncio.sleep(0.25)
    assert panel_msg.edit.await_count == 1
    assert "FULL" in panel_msg.embeds[0].description

    await bot_instance.panel_service.shutdown()


async def test_startup_synchronization_corrects_outdated_embed(
    bot_instance: BloxBerryBot,
) -> None:
    """1. Correct seat counts on startup: ensures actual member roles update the embed on start."""
    guild = make_guild(settings=bot_instance.settings)
    bot_user = SimpleNamespace(id=9000001)
    bot_instance._connection.user = bot_user  # type: ignore[assignment]

    main_ch = make_text_channel(bot_instance.settings.channels.main_recruitment, guild=guild)
    skill_ch = make_text_channel(bot_instance.settings.channels.skill_recruitment, guild=guild)
    ticket_ch = make_text_channel(bot_instance.settings.channels.ticket_panel, guild=guild)

    # 1 member manually holds Technical Director in guild
    tech_role = guild.get_role(MAIN_ROLES["Technical Director"])
    human = make_member(9020, roles=[tech_role])
    # Also add a bot holding the role - bots must NOT be counted
    bot_member = make_member(9021, roles=[tech_role], bot=True)
    guild.members.extend([human, bot_member])

    # Outdated existing panel showing 0 seats filled
    empty_guild = make_guild(settings=bot_instance.settings)
    outdated_embed = build_main_recruitment_embed(empty_guild, bot_instance.settings)
    main_msg = MagicMock(spec=discord.Message)
    main_msg.id = 12001
    main_msg.author = bot_user
    main_msg.guild = guild
    main_msg.embeds = [outdated_embed]
    main_msg.components = _components_for_view(MainPanelView())

    async def _edit(**kwargs: object) -> MagicMock:
        main_msg.embeds = [kwargs["embed"]]  # type: ignore[list-item]
        return main_msg

    main_msg.edit = AsyncMock(side_effect=_edit)
    main_ch.fetch_message = AsyncMock(return_value=main_msg)
    main_ch.send = AsyncMock()
    bot_instance.state_store.set_panel_message_id("main", main_msg.id)

    channels_map = {main_ch.id: main_ch, skill_ch.id: skill_ch, ticket_ch.id: ticket_ch}
    bot_instance.get_channel = MagicMock(side_effect=lambda cid: channels_map.get(cid))  # type: ignore[method-assign]

    # Run on_ready panel setup
    await bot_instance.on_ready()

    # 8. No duplicate messages are created
    main_ch.send.assert_not_awaited()
    # Embed edited to reflect actual member roles (1/2 seats filled, bot ignored)
    assert main_msg.edit.await_count == 1
    assert "Technical Director** — `1/2` seats filled" in main_msg.embeds[0].description

    await bot_instance.panel_service.shutdown()


async def test_manual_role_assignment_and_removal_updates_embed(
    bot_instance: BloxBerryBot,
) -> None:
    """2 & 3. Manual role assignment and removal update the embed."""
    guild = make_guild(settings=bot_instance.settings)
    main_view = MainPanelView()
    panel_msg = MagicMock(spec=discord.Message)
    panel_msg.guild = guild
    panel_msg.embeds = [build_main_recruitment_embed(guild, bot_instance.settings)]
    panel_msg.components = _components_for_view(main_view)

    async def _edit(**kwargs: object) -> MagicMock:
        panel_msg.embeds = [kwargs["embed"]]  # type: ignore[list-item]
        return panel_msg

    panel_msg.edit = AsyncMock(side_effect=_edit)
    bot_instance.panel_service.main_panel_message = panel_msg

    # 2. Administrator manually assigns Technical Director to a member
    tech_role = guild.get_role(MAIN_ROLES["Technical Director"])
    member = make_member(9030, roles=[])
    guild.members.append(member)

    # Simulate manual assignment via on_member_update
    from bot.events.member_events import MemberEventsCog

    cog = MemberEventsCog(bot_instance)
    before_member = make_member(9030, roles=[])
    updated_member = make_member(9030, roles=[tech_role])
    member.roles.append(tech_role)

    await cog.on_member_update(before_member, updated_member)
    await asyncio.sleep(0.25)

    assert panel_msg.edit.await_count == 1
    assert "Technical Director** — `1/2` seats filled" in panel_msg.embeds[0].description

    # 9. No unnecessary message edits occur when nothing has changed
    await bot_instance.panel_service.refresh_main_panel(MainPanelView)
    assert panel_msg.edit.await_count == 1

    # 3. Administrator manually removes Technical Director
    panel_msg.edit.reset_mock()
    member.roles.remove(tech_role)
    no_role_member = make_member(9030, roles=[])
    await cog.on_member_update(updated_member, no_role_member)
    await asyncio.sleep(0.25)

    assert panel_msg.edit.await_count == 1
    assert "Technical Director** — `0/2` seats filled" in panel_msg.embeds[0].description

    await bot_instance.panel_service.shutdown()


async def test_periodic_reconciliation_corrects_outdated_embed_and_avoids_duplicate_tasks(
    bot_instance: BloxBerryBot,
) -> None:
    """7 & 11. Periodic reconciliation corrects embed and only one task runs across repeated on_ready."""
    guild = make_guild(settings=bot_instance.settings)
    main_view = MainPanelView()
    panel_msg = MagicMock(spec=discord.Message)
    panel_msg.guild = guild

    # Intentionally start with an outdated embed (showing 0 seats)
    panel_msg.embeds = [build_main_recruitment_embed(guild, bot_instance.settings)]
    panel_msg.components = _components_for_view(main_view)

    async def _edit(**kwargs: object) -> MagicMock:
        panel_msg.embeds = [kwargs["embed"]]  # type: ignore[list-item]
        return panel_msg

    panel_msg.edit = AsyncMock(side_effect=_edit)
    bot_instance.panel_service.main_panel_message = panel_msg
    bot_instance.panel_service.panels_ready = True

    # Manually add a member with Game Design Lead outside bot awareness
    gdl_role = guild.get_role(MAIN_ROLES["Game Design Lead"])
    member = make_member(9040, roles=[gdl_role])
    guild.members.append(member)

    # 11. Start periodic reconciliation twice (e.g. repeated on_ready)
    bot_instance.panel_service.start_periodic_reconciliation(MainPanelView, interval_seconds=0.05)
    first_task = bot_instance.panel_service._reconciliation_task
    assert first_task is not None

    bot_instance.panel_service.start_periodic_reconciliation(MainPanelView, interval_seconds=0.05)
    second_task = bot_instance.panel_service._reconciliation_task
    assert first_task is second_task  # Only one task exists

    # Wait for periodic cycle to trigger reconciliation
    await asyncio.sleep(0.12)
    assert panel_msg.edit.await_count >= 1
    assert "Game Design Lead** — `1/2` seats filled" in panel_msg.embeds[0].description

    # 10. Failed Discord API requests are handled without crashing
    resp = MagicMock()
    resp.status = 429
    resp.reason = "Rate Limited"
    resp.headers = {"Retry-After": "5"}
    panel_msg.edit = AsyncMock(side_effect=discord.HTTPException(resp, "429"))
    # Reconciliation cycle runs without unhandled exception
    await bot_instance.panel_service.refresh_main_panel(MainPanelView)

    await bot_instance.panel_service.shutdown()
    assert bot_instance.panel_service._reconciliation_task is None


async def test_ensure_all_panels_failure_keeps_panels_ready_false(
    bot_instance: BloxBerryBot,
) -> None:
    bot_instance.get_channel = MagicMock(return_value=None)  # type: ignore[method-assign]
    bot_instance.fetch_channel = AsyncMock(side_effect=RuntimeError("network error"))  # type: ignore[method-assign]

    ok = await bot_instance.panel_service.ensure_all_panels(
        main_view_factory=MainPanelView,
        skill_view_factory=lambda: SkillPanelView(bot_instance.settings),
        ticket_view_factory=lambda: TicketPanelView(bot_instance.settings),
    )
    assert ok is False
    assert bot_instance.panel_service.panels_ready is False


async def test_smart_command_sync_avoids_redundant_puts(tmp_path) -> None:
    settings = load_settings(
        {
            "DISCORD_TOKEN": "token",
            "DATA_FILE": str(tmp_path / "sync_state.json"),
            "SYNC_COMMANDS_MODE": "auto",
            "INSTANCE_LOCK_FILE": "none",
        }
    )
    store1 = StateStore(settings.data_file, settings.roles.main_roles.keys())
    bot1 = BloxBerryBot(settings=settings, state_store=store1)
    async with bot1:
        await bot1.load_extension("bot.commands.tickets")
        bot1.tree.sync = AsyncMock(return_value=["ticket"])  # type: ignore[method-assign]

        count1 = await bot1.sync_commands_if_needed()
        assert count1 == 1
        assert bot1.tree.sync.await_count == 1

        # Second call in same process is skipped
        count2 = await bot1.sync_commands_if_needed()
        assert count2 is None
        assert bot1.tree.sync.await_count == 1

    # New bot instance loading same persisted hash skips sync automatically
    store2 = StateStore(settings.data_file, settings.roles.main_roles.keys())
    bot2 = BloxBerryBot(settings=settings, state_store=store2)
    async with bot2:
        await bot2.load_extension("bot.commands.tickets")
        bot2.tree.sync = AsyncMock(return_value=["ticket"])  # type: ignore[method-assign]
        count3 = await bot2.sync_commands_if_needed()
        assert count3 is None
        bot2.tree.sync.assert_not_awaited()
