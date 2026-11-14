"""Ticket workflow service: creation, claiming, transcripts, closing, and member access."""

from __future__ import annotations

import asyncio
import io
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime

import discord

from bot.config import Settings
from bot.database import StateStore
from bot.services.discord_helpers import (
    is_ticket_claim_authorized,
    is_ticket_staff_authorized,
    resolve_channel,
    resolve_member,
    send_interaction_response,
    send_user_dm,
)
from bot.utils import log_discord_http_error

logger = logging.getLogger(__name__)


def ticket_channel_name(ticket_type: str, username: str, settings: Settings) -> str:
    """Build a sanitized Discord channel slug for a ticket category and username."""
    cfg = settings.ticket_types[ticket_type]
    slug = re.sub(r"[^a-z0-9]+", "-", cfg.key.lower()).strip("-")
    user = re.sub(r"[^a-z0-9]+", "-", username.lower()).strip("-")[:18] or "member"
    return f"{slug}-{user}"[:95]


async def build_transcript(channel: discord.TextChannel | discord.Thread) -> str:
    """Build a plain-text chronological transcript of all messages in a ticket channel."""
    lines = [
        "BloxBerry Labs Ticket Transcript",
        f"Channel: #{channel.name} ({channel.id})",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "=" * 70,
    ]
    async for msg in channel.history(limit=None, oldest_first=True):
        timestamp = msg.created_at.isoformat()
        author = f"{msg.author} ({msg.author.id})"
        content = msg.clean_content or ""
        if msg.attachments:
            content += " | Attachments: " + ", ".join(a.url for a in msg.attachments)
        if msg.embeds:
            for emb in msg.embeds:
                if emb.title:
                    content += f" | Embed title: {emb.title}"
                if emb.description:
                    content += f" | Embed: {emb.description}"
        lines.append(f"[{timestamp}] {author}: {content}")
    return "\n".join(lines)


class TicketService:
    """Handles support ticket creation, staff claims, member additions, and closing."""

    def __init__(self, settings: Settings, state_store: StateStore) -> None:
        self.settings = settings
        self.state_store = state_store

    async def create_ticket(
        self,
        interaction: discord.Interaction,
        ticket_type: str,
        control_view_factory: Callable[[], discord.ui.View],
    ) -> None:
        """Create a private ticket channel and notify the user and department."""
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(
                "Tickets can only be opened inside the server.",
                ephemeral=True,
            )
            return

        cfg = self.settings.ticket_types.get(ticket_type)
        if cfg is None:
            await interaction.followup.send(
                "Unknown ticket category.",
                ephemeral=True,
            )
            return

        user_lock = self.state_store.get_ticket_create_lock(interaction.user.id)
        async with user_lock:
            for channel_id, _record in self.state_store.iter_open_tickets_for_user(
                interaction.user.id, ticket_type
            ):
                existing = guild.get_channel(channel_id)
                if existing is not None:
                    await interaction.followup.send(
                        f"You already have an open ticket: {existing.mention}",
                        ephemeral=True,
                    )
                    return

            try:
                category = await resolve_channel(
                    interaction.client,
                    guild,
                    self.settings.channels.ticket_category,
                )
            except (discord.HTTPException, discord.RateLimited):
                category = None

            if not isinstance(category, discord.CategoryChannel):
                await interaction.followup.send(
                    "Ticket category was not found.",
                    ephemeral=True,
                )
                return

            overwrites: dict[
                discord.Role | discord.Member | discord.Object,
                discord.PermissionOverwrite,
            ] = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                ),
            }
            if guild.me is not None:
                overwrites[guild.me] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True,
                    manage_messages=True,
                    attach_files=True,
                )

            # Tickets Access role has visibility in every ticket for assistance,
            # but does not automatically get claim permissions.
            tickets_access = guild.get_role(self.settings.roles.tickets_access)
            if tickets_access is not None:
                overwrites[tickets_access] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                )

            for rid in cfg.access:
                role = guild.get_role(rid)
                if role is not None:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        attach_files=True,
                        embed_links=True,
                    )

            try:
                channel = await guild.create_text_channel(
                    name=ticket_channel_name(ticket_type, interaction.user.name, self.settings),
                    category=category,
                    overwrites=overwrites,
                    topic=(
                        f"BloxBerry ticket | Owner ID: {interaction.user.id} | Type: {ticket_type}"
                    ),
                    reason=f"Ticket opened by {interaction.user}",
                )
            except (discord.HTTPException, discord.RateLimited) as exc:
                log_discord_http_error(logger, "create_text_channel(ticket)", exc)
                await interaction.followup.send(
                    f"Could not create your ticket channel right now: {exc}",
                    ephemeral=True,
                )
                return

            record = {
                "owner_id": interaction.user.id,
                "ticket_type": ticket_type,
                "status": "open",
                "claimed_by": None,
                "created_at": datetime.now(UTC).isoformat(),
            }
            async with self.state_store.data_lock:
                self.state_store.set_ticket(channel.id, record)
                await self.state_store.save()

        role_pings = " ".join(f"<@&{rid}>" for rid in cfg.ping)
        embed = discord.Embed(
            title=f"🎟️ {ticket_type}",
            description=(
                f"Welcome {interaction.user.mention}!\n\n"
                "Please describe your request with as much detail as possible. "
                "A member of the appropriate department will assist you.\n\n"
                f"**Ticket owner:** {interaction.user.mention}\n"
                f"**Department:** {ticket_type}\n"
                "**Status:** 🟢 Open"
            ),
            color=self.settings.color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(
            text=f"Ticket Owner ID: {interaction.user.id} • Ticket Channel ID: {channel.id}"
        )

        try:
            await channel.send(
                content=f"{interaction.user.mention} {role_pings}",
                embed=embed,
                view=control_view_factory(),
                allowed_mentions=discord.AllowedMentions(users=True, roles=True),
            )
        except (discord.HTTPException, discord.RateLimited) as exc:
            log_discord_http_error(logger, f"send ticket panel in channel {channel.id}", exc)

        dm_ok, dm_note = await send_user_dm(
            interaction.user,
            embed=discord.Embed(
                title="🎟️ Your BloxBerry Labs ticket is open",
                description=(
                    f"Your **{ticket_type}** ticket has been created in **{guild.name}**.\n\n"
                    f"**Ticket:** #{channel.name}\n"
                    f"[Open your ticket](https://discord.com/channels/{guild.id}/{channel.id})"
                ),
                color=self.settings.color,
            ),
            reason="ticket created",
        )
        if not dm_ok:
            logger.warning("Ticket creation DM to %s failed: %s", interaction.user.id, dm_note)

        await interaction.followup.send(
            f"✅ Your ticket has been created: {channel.mention}\n"
            + ("📩 I also sent you a DM." if dm_ok else f"⚠️ I couldn't DM you: {dm_note}"),
            ephemeral=True,
        )

    async def claim_ticket(self, interaction: discord.Interaction) -> None:
        """Claim an open unclaimed ticket for the interacting authorized staff member."""
        if not isinstance(interaction.channel, discord.TextChannel) or not isinstance(
            interaction.user, discord.Member
        ):
            await send_interaction_response(
                interaction, "This action can only be used inside a server ticket.", ephemeral=True
            )
            return

        channel = interaction.channel
        channel_lock = self.state_store.get_ticket_channel_lock(channel.id)
        async with channel_lock:
            record = self.state_store.get_ticket(channel.id)
            if not record or record.get("status") != "open":
                await interaction.response.send_message(
                    "This ticket is not open.",
                    ephemeral=True,
                )
                return

            if not is_ticket_claim_authorized(
                interaction.user, str(record["ticket_type"]), self.settings
            ):
                await interaction.response.send_message(
                    "You are not authorized to claim this department's tickets.",
                    ephemeral=True,
                )
                return

            if record.get("claimed_by"):
                await interaction.response.send_message(
                    f"This ticket is already claimed by <@{record['claimed_by']}>.",
                    ephemeral=True,
                )
                return

            async with self.state_store.data_lock:
                record["claimed_by"] = interaction.user.id
                await self.state_store.save()

        embed = discord.Embed(
            title="🙋 Ticket Claimed",
            description=f"This ticket has been claimed by {interaction.user.mention}.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        await interaction.response.send_message(embed=embed)
        try:
            await channel.send(f"{interaction.user.mention} is now handling this ticket.")
        except (discord.HTTPException, discord.RateLimited) as exc:
            log_discord_http_error(logger, f"send claim notice in {channel.id}", exc)

    async def unclaim_ticket(self, interaction: discord.Interaction) -> None:
        """Release a claimed ticket back to the department queue."""
        if not isinstance(interaction.channel, discord.TextChannel) or not isinstance(
            interaction.user, discord.Member
        ):
            await send_interaction_response(
                interaction, "This action can only be used inside a server ticket.", ephemeral=True
            )
            return

        channel = interaction.channel
        channel_lock = self.state_store.get_ticket_channel_lock(channel.id)
        async with channel_lock:
            record = self.state_store.get_ticket(channel.id)
            if not record or record.get("status") != "open":
                await interaction.response.send_message(
                    "This ticket is not open.",
                    ephemeral=True,
                )
                return

            claimant = record.get("claimed_by")
            if claimant is None:
                await interaction.response.send_message(
                    "This ticket is not currently claimed.",
                    ephemeral=True,
                )
                return

            if (
                interaction.user.id != claimant
                and not interaction.user.guild_permissions.administrator
            ):
                await interaction.response.send_message(
                    "Only the claimant or an administrator can unclaim this ticket.",
                    ephemeral=True,
                )
                return

            async with self.state_store.data_lock:
                record["claimed_by"] = None
                await self.state_store.save()

        embed = discord.Embed(
            title="↩️ Ticket Unclaimed",
            description=f"{interaction.user.mention} unclaimed this ticket.",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed)

    async def request_close_ticket(
        self,
        interaction: discord.Interaction,
        confirm_view_factory: Callable[[], discord.ui.View],
    ) -> None:
        """Prompt for confirmation before archiving and deleting a ticket channel."""
        if not isinstance(interaction.channel, discord.TextChannel) or not isinstance(
            interaction.user, discord.Member
        ):
            await send_interaction_response(
                interaction, "This action can only be used inside a server ticket.", ephemeral=True
            )
            return

        record = self.state_store.get_ticket(interaction.channel.id)
        if not record or record.get("status") != "open":
            await interaction.response.send_message(
                "This ticket is already closed or is not recognized.",
                ephemeral=True,
            )
            return

        owner_id = record["owner_id"]
        is_owner = interaction.user.id == owner_id
        is_staff = is_ticket_staff_authorized(interaction.user, record, self.settings)
        if not (is_owner or is_staff):
            await interaction.response.send_message(
                "Only the ticket owner or authorized department staff can close this ticket.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Are you sure you want to close this ticket? The transcript will be saved and this channel will be deleted.",
            view=confirm_view_factory(),
            ephemeral=True,
        )

    async def close_ticket(self, interaction: discord.Interaction) -> None:
        """Save ticket transcript, mark ticket closed, notify owner, and delete channel."""
        if (
            not isinstance(interaction.channel, discord.TextChannel)
            or interaction.guild is None
            or not isinstance(interaction.user, discord.Member)
        ):
            await send_interaction_response(
                interaction, "This action can only be used inside a server ticket.", ephemeral=True
            )
            return

        channel = interaction.channel
        guild = interaction.guild

        channel_lock = self.state_store.get_ticket_channel_lock(channel.id)
        async with channel_lock:
            record = self.state_store.get_ticket(channel.id)
            if not record or record.get("status") != "open":
                await send_interaction_response(
                    interaction,
                    "Ticket is already closed.",
                    ephemeral=True,
                )
                return

            owner_id = record["owner_id"]
            if not (
                interaction.user.id == owner_id
                or is_ticket_staff_authorized(interaction.user, record, self.settings)
            ):
                await send_interaction_response(
                    interaction,
                    "You cannot close this ticket.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True, thinking=True)

            try:
                transcript_text = await build_transcript(channel)
                transcript_file = discord.File(
                    io.BytesIO(transcript_text.encode("utf-8")),
                    filename=f"{channel.name}-transcript.txt",
                )
                transcript_channel = await resolve_channel(
                    interaction.client,
                    guild,
                    self.settings.channels.transcript,
                )
                if not isinstance(transcript_channel, discord.abc.Messageable):
                    raise TypeError(
                        f"Transcript channel {self.settings.channels.transcript} is not messageable."
                    )

                transcript_embed = discord.Embed(
                    title="📄 Ticket Transcript",
                    description=(
                        f"**Ticket:** `{channel.name}`\n"
                        f"**Ticket ID:** `{channel.id}`\n"
                        f"**Owner:** <@{owner_id}>\n"
                        f"**Type:** {record['ticket_type']}\n"
                        f"**Closed by:** {interaction.user.mention}"
                    ),
                    color=self.settings.color,
                    timestamp=discord.utils.utcnow(),
                )
                await transcript_channel.send(embed=transcript_embed, file=transcript_file)
            except Exception as exc:
                await interaction.followup.send(
                    f"Could not save transcript, so the ticket was not deleted: {exc}",
                    ephemeral=True,
                )
                return

            async with self.state_store.data_lock:
                record["status"] = "closed"
                record["closed_by"] = interaction.user.id
                record["closed_at"] = datetime.now(UTC).isoformat()
                await self.state_store.save()

            owner = await resolve_member(guild, owner_id)
            if owner is not None:
                dm_ok, dm_note = await send_user_dm(
                    owner,
                    embed=discord.Embed(
                        title="🔒 Your BloxBerry Labs ticket was closed",
                        description=(
                            f"Your **{record['ticket_type']}** ticket has been closed.\n"
                            "A transcript has been saved by the support team."
                        ),
                        color=self.settings.color,
                    ),
                    reason="ticket closed",
                )
                if not dm_ok:
                    logger.warning("Ticket-close DM to %s failed: %s", owner_id, dm_note)
            else:
                dm_ok, dm_note = False, "Ticket owner is no longer in the server."

            await interaction.followup.send(
                "Transcript saved. "
                + ("Owner notified by DM. " if dm_ok else f"Owner DM failed: {dm_note} ")
                + "This ticket channel will be deleted in 5 seconds.",
                ephemeral=True,
            )
            try:
                await channel.send(
                    f"🔒 Ticket closed by {interaction.user.mention}. Transcript saved. Deleting this channel in 5 seconds."
                )
            except (discord.HTTPException, discord.RateLimited) as exc:
                log_discord_http_error(logger, f"send close countdown in {channel.id}", exc)

            await asyncio.sleep(5)
            try:
                await channel.delete(reason=f"Ticket closed by {interaction.user}")
            except (discord.HTTPException, discord.RateLimited) as exc:
                log_discord_http_error(logger, f"delete ticket channel {channel.id}", exc)

    async def add_member_to_ticket(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        """Grant a guild member access to the current open ticket channel and notify them."""
        if (
            not isinstance(interaction.channel, discord.TextChannel)
            or interaction.guild is None
            or not isinstance(interaction.user, discord.Member)
        ):
            await send_interaction_response(
                interaction,
                "Use this command inside an open ticket.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        channel_lock = self.state_store.get_ticket_channel_lock(channel.id)
        async with channel_lock:
            record = self.state_store.get_ticket(channel.id)
            if not record or record.get("status") != "open":
                await interaction.response.send_message(
                    "Use this command inside an open ticket.",
                    ephemeral=True,
                )
                return

            authorized = (
                interaction.user.guild_permissions.administrator
                or interaction.user.id == record.get("claimed_by")
                or is_ticket_claim_authorized(
                    interaction.user, str(record["ticket_type"]), self.settings
                )
            )
            if not authorized:
                await interaction.response.send_message(
                    "Only authorized department staff, the claimant, or an administrator can add users.",
                    ephemeral=True,
                )
                return

            if user.bot:
                await interaction.response.send_message(
                    "You cannot add a bot to a ticket.",
                    ephemeral=True,
                )
                return

            try:
                await channel.set_permissions(
                    user,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                    reason=f"Added by {interaction.user}",
                )
            except (discord.HTTPException, discord.RateLimited) as exc:
                log_discord_http_error(logger, f"add member {user.id} to ticket {channel.id}", exc)
                await interaction.response.send_message(
                    f"Could not update channel permissions: {exc}",
                    ephemeral=True,
                )
                return

        audit = discord.Embed(
            title="➕ Member Added to Ticket",
            description=(
                f"**Added member:** {user.mention}\n"
                f"**Added by:** {interaction.user.mention}\n"
                f"**Ticket:** {channel.mention}"
            ),
            color=self.settings.color,
            timestamp=discord.utils.utcnow(),
        )
        await interaction.response.send_message(embed=audit)

        dm_ok, dm_note = await send_user_dm(
            user,
            embed=discord.Embed(
                title="🎟️ You were added to a BloxBerry Labs ticket",
                description=(
                    f"You were added to **{interaction.guild.name}** ticket **#{channel.name}** "
                    f"by **{interaction.user.display_name}**.\n\n"
                    f"[Open ticket](https://discord.com/channels/{interaction.guild.id}/{channel.id})"
                ),
                color=self.settings.color,
            ),
            reason="added to ticket",
        )
        if not dm_ok:
            logger.warning("Ticket-add DM to %s failed: %s", user.id, dm_note)
            await interaction.followup.send(
                f"⚠️ User was added, but their DM failed: {dm_note}",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "📩 The added member was notified by DM.",
                ephemeral=True,
            )
