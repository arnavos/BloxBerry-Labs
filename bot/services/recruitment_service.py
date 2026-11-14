"""Recruitment workflow service: application submission, seat enforcement, and review."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import discord

from bot.config import Settings
from bot.database import StateStore
from bot.services.discord_helpers import (
    count_members_with_role,
    is_recruitment_reviewer,
    resolve_channel,
    resolve_member,
    send_interaction_response,
    send_user_dm,
    truncate,
)
from bot.utils import log_discord_http_error

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApplicationAnswers:
    """User-submitted form answers from the recruitment modal."""

    experience: str
    skills: str
    portfolio: str
    availability: str
    reason: str


def application_id_from_message(message: discord.Message) -> int | None:
    """Extract the applicant Discord user ID from a review embed footer."""
    if not message.embeds:
        return None
    footer = message.embeds[0].footer.text or ""
    prefix = "Recruitment Application ID:"
    if not footer.startswith(prefix):
        return None
    try:
        return int(footer.split(":", 1)[1].strip())
    except ValueError:
        return None


class RecruitmentService:
    """Handles main and skill-based recruitment applications and reviewer decisions."""

    def __init__(self, settings: Settings, state_store: StateStore) -> None:
        self.settings = settings
        self.state_store = state_store

    async def submit_application(
        self,
        interaction: discord.Interaction,
        app_type: str,
        selected_names: list[str],
        answers: ApplicationAnswers,
        review_view_factory: Callable[[], discord.ui.View],
    ) -> None:
        """Validate, role-tag, post for review, and persist a new recruitment application."""
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(
                "Recruitment applications can only be submitted inside the server.",
                ephemeral=True,
            )
            return

        applicant_lock = self.state_store.get_applicant_lock(interaction.user.id)
        async with applicant_lock:
            if self.state_store.has_pending_application(interaction.user.id):
                await interaction.followup.send(
                    "You already have a pending recruitment application.",
                    ephemeral=True,
                )
                return

            role_map = (
                self.settings.roles.main_roles
                if app_type == "main"
                else self.settings.roles.skill_roles
            )
            role_ids = [role_map[name] for name in selected_names]

            for rid in role_ids:
                if guild.get_role(rid) is None:
                    await interaction.followup.send(
                        f"One of the requested roles (`{rid}`) no longer exists.",
                        ephemeral=True,
                    )
                    return

            if app_type == "main":
                role_name = selected_names[0]
                async with self.state_store.seat_locks[role_name]:
                    if (
                        count_members_with_role(guild, role_ids[0])
                        >= self.settings.roles.seat_limits[role_name]
                    ):
                        await interaction.followup.send(
                            "Sorry, this position has just been filled.",
                            ephemeral=True,
                        )
                        return

            pending_role = guild.get_role(self.settings.roles.pending_application)
            added_pending_role = False
            try:
                if pending_role is not None and pending_role not in interaction.user.roles:
                    await interaction.user.add_roles(
                        pending_role,
                        reason="Recruitment application submitted",
                    )
                    added_pending_role = True
            except (discord.HTTPException, discord.RateLimited) as exc:
                log_discord_http_error(logger, "assign pending-application role", exc)
                await interaction.followup.send(
                    f"Could not assign the pending-application role: {exc}",
                    ephemeral=True,
                )
                return

            mentions = "\n".join(f"• <@&{rid}>" for rid in role_ids)
            type_label = "Main Position" if app_type == "main" else "Skill-Based Roles"
            embed = discord.Embed(
                title="📋 NEW BLOXBERRY LABS APPLICATION",
                description=(
                    f"**Applicant:** {interaction.user.mention}\n"
                    f"**Application Type:** {type_label}\n"
                    f"**Requested Role(s):**\n{mentions}\n\n"
                    "**Status:** 🟡 PENDING REVIEW"
                ),
                color=self.settings.color,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="Previous Experience",
                value=truncate(answers.experience),
                inline=False,
            )
            embed.add_field(
                name="Skills",
                value=truncate(answers.skills),
                inline=False,
            )
            embed.add_field(
                name="Portfolio / Previous Work",
                value=truncate(answers.portfolio or "Not provided"),
                inline=False,
            )
            embed.add_field(
                name="Availability",
                value=truncate(answers.availability),
                inline=False,
            )
            embed.add_field(
                name="Why BloxBerry Labs?",
                value=truncate(answers.reason),
                inline=False,
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(text=f"Recruitment Application ID: {interaction.user.id}")

            try:
                review_channel = await resolve_channel(
                    interaction.client,
                    guild,
                    self.settings.channels.application_review,
                )
                if not isinstance(review_channel, discord.abc.Messageable):
                    raise RuntimeError(
                        f"Application review channel {self.settings.channels.application_review} is not messageable."
                    )
                review_message = await review_channel.send(
                    embed=embed,
                    view=review_view_factory(),
                )
            except Exception as exc:
                if isinstance(exc, (discord.HTTPException, discord.RateLimited)):
                    log_discord_http_error(logger, "send application review embed", exc)
                else:
                    logger.error("Failed to send application review embed: %s", exc, exc_info=exc)
                if added_pending_role and pending_role is not None:
                    try:
                        await interaction.user.remove_roles(
                            pending_role,
                            reason="Rollback failed recruitment application submission",
                        )
                    except (discord.HTTPException, discord.RateLimited) as rollback_exc:
                        log_discord_http_error(
                            logger, "rollback pending-application role", rollback_exc
                        )
                await interaction.followup.send(
                    "Could not submit your application to the review channel right now. Please try again shortly.",
                    ephemeral=True,
                )
                return

            async with self.state_store.data_lock:
                self.state_store.set_application(
                    interaction.user.id,
                    {
                        "status": "pending",
                        "type": app_type,
                        "role_ids": role_ids,
                        "role_names": list(selected_names),
                        "review_message_id": review_message.id,
                        "review_channel_id": self.settings.channels.application_review,
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                )
                await self.state_store.save()

        await interaction.followup.send(
            "✅ Your application has been submitted!\n\n"
            f"**Requested role(s):**\n{mentions}\n\n"
            "Management will review your application.",
            ephemeral=True,
        )

    async def finalize_application(
        self,
        interaction: discord.Interaction,
        decision: str,
        reviewed_view_factory: Callable[[], discord.ui.View],
    ) -> None:
        """Accept or deny a pending recruitment application and notify the applicant."""
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await send_interaction_response(
                interaction,
                "❌ This action can only be used inside a server.",
                ephemeral=True,
            )
            return

        if not is_recruitment_reviewer(interaction.user, self.settings):
            await interaction.response.send_message(
                "❌ You are not authorized to review applications.",
                ephemeral=True,
            )
            return

        if interaction.message is None:
            await interaction.response.send_message(
                "Could not identify this application.",
                ephemeral=True,
            )
            return

        applicant_id = application_id_from_message(interaction.message)
        if applicant_id is None:
            await interaction.response.send_message(
                "Could not identify this application.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        applicant_lock = self.state_store.get_applicant_lock(applicant_id)
        async with applicant_lock:
            record = self.state_store.get_application(applicant_id)
            if (
                not record
                or record.get("status") != "pending"
                or record.get("review_message_id") != interaction.message.id
            ):
                await interaction.followup.send(
                    "This application has already been reviewed.",
                    ephemeral=True,
                )
                return

            applicant = await resolve_member(guild, applicant_id)
            if applicant is None:
                await interaction.followup.send(
                    "Applicant is no longer in the server.",
                    ephemeral=True,
                )
                return

            role_ids = [int(x) for x in record["role_ids"]]
            roles = [guild.get_role(rid) for rid in role_ids]
            if any(role is None for role in roles):
                await interaction.followup.send(
                    "One or more requested roles no longer exist. Application remains pending.",
                    ephemeral=True,
                )
                return

            resolved_roles: list[discord.Role] = [role for role in roles if role is not None]

            if decision == "accept":
                try:
                    if record["type"] == "main":
                        role_name = record["role_names"][0]
                        async with self.state_store.seat_locks[role_name]:
                            target_role = resolved_roles[0]
                            limit = self.settings.roles.seat_limits[role_name]
                            if (
                                target_role not in applicant.roles
                                and count_members_with_role(guild, target_role.id) >= limit
                            ):
                                await interaction.followup.send(
                                    "All seats for this position are full. Application remains pending.",
                                    ephemeral=True,
                                )
                                return
                            if target_role not in applicant.roles:
                                await applicant.add_roles(
                                    target_role,
                                    reason=f"Recruitment accepted by {interaction.user}",
                                )
                    else:
                        missing = [r for r in resolved_roles if r not in applicant.roles]
                        if missing:
                            await applicant.add_roles(
                                *missing,
                                reason=f"Skill recruitment accepted by {interaction.user}",
                            )
                except (discord.HTTPException, discord.RateLimited) as exc:
                    log_discord_http_error(logger, "assign accepted recruitment role(s)", exc)
                    await interaction.followup.send(
                        f"Could not assign requested role(s). Application remains pending: {exc}",
                        ephemeral=True,
                    )
                    return

            pending_role = guild.get_role(self.settings.roles.pending_application)
            if pending_role is not None and pending_role in applicant.roles:
                try:
                    await applicant.remove_roles(
                        pending_role,
                        reason=f"Recruitment application {decision}",
                    )
                except (discord.HTTPException, discord.RateLimited) as exc:
                    log_discord_http_error(logger, "remove pending-application role", exc)

            async with self.state_store.data_lock:
                record["status"] = decision
                record["reviewed_by"] = interaction.user.id
                record["reviewed_at"] = datetime.now(UTC).isoformat()
                await self.state_store.save()

        # Update the main panel embed to reflect the newly assigned seat
        if decision == "accept" and record.get("type") == "main":
            from bot.views.recruitment import MainPanelView

            panel_svc = getattr(interaction.client, "panel_service", None)
            if panel_svc is not None:
                panel_svc.schedule_main_panel_refresh(MainPanelView)

        # Staff review embeds keep role mentions; applicant DMs use plain role names.
        mentions = "\n".join(f"• <@&{rid}>" for rid in role_ids)
        role_names_text = "\n".join(f"• **{name}**" for name in record.get("role_names", []))
        type_label = "Main Position" if record["type"] == "main" else "Skill-Based Roles"

        if decision == "accept":
            status = "🟢 ACCEPTED"
            color = discord.Color.green()
            dm_title = "🎉 Your BloxBerry Labs Application Was Accepted!"
            dm_body = (
                "Congratulations! Your application has been accepted.\n\n"
                f"**Application type:** {type_label}\n"
                f"**Approved role(s):**\n{role_names_text}\n\n"
                "Welcome to BloxBerry Labs! 🍓"
            )
        else:
            status = "🔴 DENIED"
            color = discord.Color.red()
            dm_title = "BloxBerry Labs Application Update"
            dm_body = (
                "Thank you for applying to BloxBerry Labs.\n\n"
                f"**Application type:** {type_label}\n"
                f"**Role(s) applied for:**\n{role_names_text}\n\n"
                "Unfortunately, your application was not accepted this time.\n\n"
                "We appreciate your interest in our team."
            )

        embed = interaction.message.embeds[0]
        embed.color = color
        embed.description = (
            f"**Applicant:** <@{applicant_id}>\n"
            f"**Application Type:** {type_label}\n"
            f"**Requested Role(s):**\n{mentions}\n\n"
            f"**Decision:** {status}\n**Reviewed By:** {interaction.user.mention}"
        )
        try:
            await interaction.message.edit(embed=embed, view=reviewed_view_factory())
        except (discord.HTTPException, discord.RateLimited) as exc:
            log_discord_http_error(logger, "update reviewed application message", exc)

        dm_ok, dm_note = await send_user_dm(
            applicant,
            embed=discord.Embed(title=dm_title, description=dm_body, color=self.settings.color),
            reason="application decision",
        )
        if not dm_ok:
            logger.warning("Application DM to %s failed: %s", applicant_id, dm_note)

        await interaction.followup.send(
            f"Decision completed: **{status}**\n{dm_note}",
            ephemeral=True,
        )
