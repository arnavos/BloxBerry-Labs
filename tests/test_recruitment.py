"""Tests for recruitment UI, modal submission, seat enforcement, rollback, and review."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord

from bot.client import BloxBerryBot
from bot.config import MAIN_ROLES
from bot.services.recruitment_service import ApplicationAnswers
from bot.views import (
    ApplicationReviewedView,
    ApplicationReviewView,
    MainApplyButton,
    MainPanelView,
    MainRoleSelect,
    RecruitmentModal,
    SkillContinueButton,
    SkillPanelView,
)
from tests.conftest import make_guild, make_interaction, make_member, make_text_channel


async def test_main_role_select_filters_full_positions(bot_instance: BloxBerryBot) -> None:
    guild = make_guild(settings=bot_instance.settings)
    exec_role = guild.get_role(MAIN_ROLES["Executive Manager"])
    holder = make_member(501, roles=[exec_role])
    guild.members.append(holder)
    guild._members_by_id[holder.id] = holder

    select = MainRoleSelect(guild, bot_instance.settings)
    values = [opt.value for opt in select.options]
    assert "Executive Manager" not in values
    assert "Project Manager" in values


async def test_main_role_select_all_full_fallback(bot_instance: BloxBerryBot) -> None:
    guild = make_guild(settings=bot_instance.settings)
    for idx, (role_name, rid) in enumerate(MAIN_ROLES.items(), start=1):
        role = guild.get_role(rid)
        limit = bot_instance.settings.roles.seat_limits[role_name]
        for seat in range(limit):
            m = make_member(1000 + idx * 10 + seat, roles=[role])
            guild.members.append(m)
            guild._members_by_id[m.id] = m

    select = MainRoleSelect(guild, bot_instance.settings)
    assert len(select.options) == 1
    assert select.options[0].value == "none"

    applicant = make_member(2001)
    interaction = make_interaction(client=bot_instance, user=applicant, guild=guild)
    select._values = ["none"]
    await select.callback(interaction)
    interaction.response.send_message.assert_awaited_once_with(
        "All main positions are full.", ephemeral=True
    )


async def test_recruitment_submit_and_accept_flow_fixes_newlines(
    bot_instance: BloxBerryBot,
) -> None:
    guild = make_guild(settings=bot_instance.settings)
    review_channel = make_text_channel(
        bot_instance.settings.channels.application_review, name="app-review", guild=guild
    )
    guild._channels_by_id[review_channel.id] = review_channel

    applicant = make_member(3001, name="berry_applicant")
    guild.members.append(applicant)
    guild._members_by_id[applicant.id] = applicant

    submit_interaction = make_interaction(client=bot_instance, user=applicant, guild=guild)
    answers = ApplicationAnswers(
        experience="3 years scripting",
        skills="Luau and Python",
        portfolio="https://example.com",
        availability="15 hours/week",
        reason="Love the team",
    )
    await bot_instance.recruitment_service.submit_application(
        submit_interaction,
        "skill",
        ["Programmers", "UI Designer"],
        answers,
        ApplicationReviewView,
    )

    pending_role = guild.get_role(bot_instance.settings.roles.pending_application)
    applicant.add_roles.assert_awaited_once_with(
        pending_role, reason="Recruitment application submitted"
    )
    # Simulate Discord role state update after add_roles
    applicant.roles.append(pending_role)

    sent_kwargs = review_channel.send.await_args.kwargs
    review_embed = sent_kwargs["embed"]
    assert "Recruitment Application ID: 3001" in review_embed.footer.text
    assert bot_instance.has_pending_application(3001) is True

    # Now reviewer accepts the application
    reviewer_role = guild.get_role(next(iter(bot_instance.settings.roles.recruitment_reviewers)))
    reviewer = make_member(4001, name="reviewer", roles=[reviewer_role])

    review_msg = MagicMock(spec=discord.Message)
    review_msg.id = review_channel.send.return_value.id
    review_msg.embeds = [review_embed]
    review_msg.edit = AsyncMock()

    review_interaction = make_interaction(
        client=bot_instance, user=reviewer, guild=guild, message=review_msg
    )
    await bot_instance.recruitment_service.finalize_application(
        review_interaction, "accept", ApplicationReviewedView
    )

    # Verify both skill roles granted and pending role removed
    added_call_args = applicant.add_roles.await_args_list[-1]
    added_ids = {r.id for r in added_call_args.args}
    assert added_ids == {
        bot_instance.settings.roles.skill_roles["Programmers"],
        bot_instance.settings.roles.skill_roles["UI Designer"],
    }
    applicant.remove_roles.assert_awaited_once_with(
        pending_role, reason="Recruitment application accept"
    )

    # Verify verified newline fix (no literal \n escapes in embed or DM)
    dm_channel = applicant.create_dm.return_value
    dm_embed = dm_channel.send.await_args.kwargs["embed"]
    assert "\\n" not in dm_embed.description
    assert "\n• **Programmers**\n• **UI Designer**" in dm_embed.description

    edited_embed = review_msg.edit.await_args.kwargs["embed"]
    assert "\\n" not in edited_embed.description
    assert "🟢 ACCEPTED" in edited_embed.description
    assert bot_instance.has_pending_application(3001) is False


async def test_submit_application_rolls_back_pending_role_if_review_send_fails(
    bot_instance: BloxBerryBot,
) -> None:
    guild = make_guild(settings=bot_instance.settings)
    review_channel = make_text_channel(
        bot_instance.settings.channels.application_review, name="app-review", guild=guild
    )
    review_channel.send = AsyncMock(side_effect=RuntimeError("Discord outage"))
    guild._channels_by_id[review_channel.id] = review_channel

    applicant = make_member(3002, name="rollback_user")
    pending_role = guild.get_role(bot_instance.settings.roles.pending_application)
    applicant.add_roles = AsyncMock(side_effect=lambda *roles, **kw: applicant.roles.extend(roles))

    interaction = make_interaction(client=bot_instance, user=applicant, guild=guild)
    answers = ApplicationAnswers("exp", "skills", "", "10h", "reason")

    await bot_instance.recruitment_service.submit_application(
        interaction, "main", ["Project Manager"], answers, ApplicationReviewView
    )

    applicant.remove_roles.assert_awaited_once_with(
        pending_role, reason="Rollback failed recruitment application submission"
    )
    assert bot_instance.has_pending_application(3002) is False
    assert "Could not submit your application" in interaction.followup.send.await_args.args[0]


async def test_concurrent_finalize_application_prevents_double_review(
    bot_instance: BloxBerryBot,
) -> None:
    guild = make_guild(settings=bot_instance.settings)
    applicant = make_member(3003, name="double_review")
    guild.members.append(applicant)
    guild._members_by_id[applicant.id] = applicant

    review_embed = discord.Embed(title="App")
    review_embed.set_footer(text="Recruitment Application ID: 3003")

    review_msg = MagicMock(spec=discord.Message)
    review_msg.id = 888777666
    review_msg.embeds = [review_embed]
    review_msg.edit = AsyncMock()

    bot_instance.state_store.set_application(
        3003,
        {
            "status": "pending",
            "type": "main",
            "role_ids": [MAIN_ROLES["Art Director"]],
            "role_names": ["Art Director"],
            "review_message_id": review_msg.id,
            "review_channel_id": bot_instance.settings.channels.application_review,
        },
    )

    reviewer_role = guild.get_role(next(iter(bot_instance.settings.roles.recruitment_reviewers)))
    r1 = make_member(4010, name="rev1", roles=[reviewer_role])
    r2 = make_member(4011, name="rev2", roles=[reviewer_role])

    i1 = make_interaction(client=bot_instance, user=r1, guild=guild, message=review_msg)
    i2 = make_interaction(client=bot_instance, user=r2, guild=guild, message=review_msg)

    await asyncio.gather(
        bot_instance.recruitment_service.finalize_application(
            i1, "accept", ApplicationReviewedView
        ),
        bot_instance.recruitment_service.finalize_application(i2, "deny", ApplicationReviewedView),
    )

    followups = [
        i1.followup.send.await_args.args[0],
        i2.followup.send.await_args.args[0],
    ]
    assert any("Decision completed:" in msg for msg in followups)
    assert any(msg == "This application has already been reviewed." for msg in followups)
    assert review_msg.edit.await_count == 1


async def test_recruitment_views_and_modal_instantiation_and_auth(
    bot_instance: BloxBerryBot,
) -> None:
    guild = make_guild(settings=bot_instance.settings)
    non_reviewer = make_member(5001, name="pleb")
    review_msg = MagicMock(spec=discord.Message)
    review_msg.embeds = [discord.Embed(title="x").set_footer(text="Recruitment Application ID: 9")]
    i_unauth = make_interaction(
        client=bot_instance, user=non_reviewer, guild=guild, message=review_msg
    )

    await bot_instance.recruitment_service.finalize_application(
        i_unauth, "accept", ApplicationReviewedView
    )
    assert "not authorized" in i_unauth.response.send_message.await_args.args[0]

    # Verify persistent custom_ids and UI callbacks exist
    panel = MainPanelView()
    assert isinstance(panel.children[0], MainApplyButton)
    assert panel.children[0].custom_id == "bb_all_main_apply"

    skill_panel = SkillPanelView(bot_instance.settings)
    assert skill_panel.children[0].custom_id == "bb_all_skill_select"  # type: ignore[attr-defined]

    modal = RecruitmentModal("main", ["Executive Manager"])
    assert modal.title == "Main Role Application"
    btn = SkillContinueButton(["Programmers"])
    assert btn.selected_names == ["Programmers"]
