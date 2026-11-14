"""Tests for ticket creation, permissions, claim/unclaim, transcript, close, and /ticket add."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.client import BloxBerryBot
from bot.commands.tickets import TicketCommandsCog
from bot.services.ticket_service import ticket_channel_name
from bot.views import CloseConfirmView, TicketControlView, TicketPanelView
from tests.conftest import make_guild, make_interaction, make_member, make_text_channel


def test_ticket_channel_name_slugification(bot_instance: BloxBerryBot) -> None:
    name = ticket_channel_name(
        "Technical Support", "Arnav Dev! 1234567890 Extra", bot_instance.settings
    )
    assert name.startswith("technical-")
    assert len(name) <= 95


async def test_create_ticket_concurrency_and_overwrites(bot_instance: BloxBerryBot) -> None:
    guild = make_guild(settings=bot_instance.settings)
    category = MagicMock(spec=discord.CategoryChannel)
    category.id = bot_instance.settings.channels.ticket_category
    guild._channels_by_id[category.id] = category

    created_channels: list[MagicMock] = []

    async def _create_text_channel(**kwargs: object) -> MagicMock:
        ch = make_text_channel(
            777000 + len(created_channels), name=str(kwargs["name"]), guild=guild
        )
        guild._channels_by_id[ch.id] = ch
        created_channels.append(ch)
        return ch

    guild.create_text_channel = AsyncMock(side_effect=_create_text_channel)

    requester = make_member(6001, name="requester")
    i1 = make_interaction(client=bot_instance, user=requester, guild=guild)
    i2 = make_interaction(client=bot_instance, user=requester, guild=guild)

    await asyncio.gather(
        bot_instance.ticket_service.create_ticket(i1, "Technical Support", TicketControlView),
        bot_instance.ticket_service.create_ticket(i2, "Technical Support", TicketControlView),
    )

    assert len(created_channels) == 1
    assert guild.create_text_channel.await_count == 1
    create_kwargs = guild.create_text_channel.await_args.kwargs
    overwrites = create_kwargs["overwrites"]
    tickets_access_role = guild.get_role(bot_instance.settings.roles.tickets_access)
    tech_role = guild.get_role(bot_instance.settings.ticket_types["Technical Support"].access[0])
    assert overwrites[guild.default_role].view_channel is False
    assert overwrites[requester].view_channel is True
    assert overwrites[tickets_access_role].view_channel is True
    assert overwrites[tech_role].view_channel is True

    # Second call sees existing open ticket
    followups = [
        i1.followup.send.await_args.args[0],
        i2.followup.send.await_args.args[0],
    ]
    assert any("Your ticket has been created" in m for m in followups)
    assert any("already have an open ticket" in m for m in followups)


async def test_claim_unclaim_and_add_member_permissions(bot_instance: BloxBerryBot) -> None:
    guild = make_guild(settings=bot_instance.settings)
    ticket_ch = make_text_channel(888001, name="technical-user", guild=guild)
    owner = make_member(6100, name="owner")
    tickets_access_role = guild.get_role(bot_instance.settings.roles.tickets_access)
    helper_only = make_member(6101, name="helper", roles=[tickets_access_role])

    tech_role = guild.get_role(bot_instance.settings.ticket_types["Technical Support"].claim[0])
    tech_staff = make_member(6102, name="techstaff", roles=[tech_role])
    other_tech = make_member(6103, name="othertech", roles=[tech_role])
    admin = make_member(6104, name="admin", administrator=True)
    added_member = make_member(6105, name="friend")
    bot_user = make_member(6106, name="somebot", bot=True)

    bot_instance.state_store.set_ticket(
        ticket_ch.id,
        {
            "owner_id": owner.id,
            "ticket_type": "Technical Support",
            "status": "open",
            "claimed_by": None,
        },
    )

    # Tickets Access role does NOT allow claiming Technical Support
    i_helper = make_interaction(
        client=bot_instance, user=helper_only, guild=guild, channel=ticket_ch
    )
    await bot_instance.ticket_service.claim_ticket(i_helper)
    assert "not authorized to claim" in i_helper.response.send_message.await_args.args[0]

    # Authorized tech staff claims ticket
    i_claim = make_interaction(client=bot_instance, user=tech_staff, guild=guild, channel=ticket_ch)
    await bot_instance.ticket_service.claim_ticket(i_claim)
    assert bot_instance.state_store.get_ticket(ticket_ch.id)["claimed_by"] == tech_staff.id  # type: ignore[index]

    # Another staff member cannot unclaim
    i_other_unclaim = make_interaction(
        client=bot_instance, user=other_tech, guild=guild, channel=ticket_ch
    )
    await bot_instance.ticket_service.unclaim_ticket(i_other_unclaim)
    assert (
        "Only the claimant or an administrator"
        in i_other_unclaim.response.send_message.await_args.args[0]
    )

    # /ticket add rejects bots, allows authorized claimant/staff
    cog = TicketCommandsCog(bot_instance)
    i_add_bot = make_interaction(
        client=bot_instance, user=tech_staff, guild=guild, channel=ticket_ch
    )
    await cog.add.callback(cog, i_add_bot, bot_user)
    assert "cannot add a bot" in i_add_bot.response.send_message.await_args.args[0]

    i_add_user = make_interaction(
        client=bot_instance, user=tech_staff, guild=guild, channel=ticket_ch
    )
    await cog.add.callback(cog, i_add_user, added_member)
    ticket_ch.set_permissions.assert_awaited_once()
    assert i_add_user.followup.send.await_args.args[0] == "📩 The added member was notified by DM."

    # Admin can unclaim
    i_admin_unclaim = make_interaction(
        client=bot_instance, user=admin, guild=guild, channel=ticket_ch
    )
    await bot_instance.ticket_service.unclaim_ticket(i_admin_unclaim)
    assert bot_instance.state_store.get_ticket(ticket_ch.id)["claimed_by"] is None  # type: ignore[index]


async def test_close_ticket_transcript_failure_aborts_and_success_deletes(
    bot_instance: BloxBerryBot,
) -> None:
    guild = make_guild(settings=bot_instance.settings)
    ticket_ch = make_text_channel(888002, name="general-user", guild=guild)
    transcript_ch = make_text_channel(
        bot_instance.settings.channels.transcript, name="transcripts", guild=guild
    )
    guild._channels_by_id[transcript_ch.id] = transcript_ch

    owner = make_member(6200, name="owner")
    guild.members.append(owner)
    guild._members_by_id[owner.id] = owner

    async def _empty_history(*args: object, **kwargs: object):
        msg = SimpleNamespace(
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            author=owner,
            clean_content="hello support",
            attachments=[],
            embeds=[],
        )
        yield msg

    ticket_ch.history = MagicMock(side_effect=_empty_history)
    bot_instance.state_store.set_ticket(
        ticket_ch.id,
        {
            "owner_id": owner.id,
            "ticket_type": "General Inquiry",
            "status": "open",
            "claimed_by": None,
        },
    )

    # Transcript failure must preserve open ticket and NOT delete channel
    transcript_ch.send = AsyncMock(side_effect=RuntimeError("transcript upload failed"))
    i_fail = make_interaction(client=bot_instance, user=owner, guild=guild, channel=ticket_ch)
    await bot_instance.ticket_service.close_ticket(i_fail)
    assert bot_instance.state_store.get_ticket(ticket_ch.id)["status"] == "open"  # type: ignore[index]
    ticket_ch.delete.assert_not_awaited()

    # Transcript success saves transcript, marks closed, DMs owner, deletes channel
    transcript_ch.send = AsyncMock()
    i_ok = make_interaction(client=bot_instance, user=owner, guild=guild, channel=ticket_ch)
    with patch("bot.services.ticket_service.asyncio.sleep", new=AsyncMock()):
        await bot_instance.ticket_service.close_ticket(i_ok)

    assert bot_instance.state_store.get_ticket(ticket_ch.id)["status"] == "closed"  # type: ignore[index]
    transcript_ch.send.assert_awaited_once()
    assert "file" in transcript_ch.send.await_args.kwargs
    ticket_ch.delete.assert_awaited_once()

    # Persistent custom_ids check
    tp = TicketPanelView(bot_instance.settings)
    assert tp.children[0].custom_id == "bb_all_ticket_type"  # type: ignore[attr-defined]
    tc = TicketControlView()
    assert [c.custom_id for c in tc.children] == [  # type: ignore[attr-defined]
        "bb_all_ticket_claim",
        "bb_all_ticket_unclaim",
        "bb_all_ticket_close",
    ]
    assert len(CloseConfirmView().children) == 2
