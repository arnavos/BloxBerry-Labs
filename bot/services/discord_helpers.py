"""Shared Discord helper utilities for permissions, cache-first lookups, and DMs."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

import discord

from bot.config import Settings
from bot.utils import log_discord_http_error

logger = logging.getLogger(__name__)


def truncate(text: str | None, limit: int = 1024) -> str:
    """Truncate text to fit Discord embed field limits while preserving fallback dash."""
    value = text or "—"
    return value if len(value) <= limit else value[: limit - 3] + "..."


def member_has_any(member: discord.Member, role_ids: Iterable[int]) -> bool:
    """Return True if the guild member holds at least one role ID in role_ids."""
    member_role_ids = {role.id for role in member.roles}
    return bool(member_role_ids.intersection(role_ids))


def is_recruitment_reviewer(member: discord.Member, settings: Settings) -> bool:
    """Return True if the member is an administrator or an authorized recruitment reviewer."""
    return member.guild_permissions.administrator or member_has_any(
        member, settings.roles.recruitment_reviewers
    )


def is_ticket_claim_authorized(
    member: discord.Member,
    ticket_type: str,
    settings: Settings,
) -> bool:
    """Return True if the member can claim or manage tickets of the given category."""
    cfg = settings.ticket_types.get(ticket_type)
    return bool(
        cfg and (member.guild_permissions.administrator or member_has_any(member, cfg.claim))
    )


def is_ticket_staff_authorized(
    member: discord.Member,
    record: Mapping[str, Any],
    settings: Settings,
) -> bool:
    """Return True if the member is an admin or department staff for the ticket record."""
    return member.guild_permissions.administrator or is_ticket_claim_authorized(
        member, str(record.get("ticket_type", "")), settings
    )


def count_members_with_role(guild: discord.Guild, role_id: int) -> int:
    """Count cached guild members holding the specified role ID."""
    role = guild.get_role(role_id)
    if role is None:
        return 0
    return sum(1 for member in guild.members if role in member.roles)


def count_members_for_roles(
    guild: discord.Guild,
    role_ids: Iterable[int],
) -> dict[int, int]:
    """Count cached guild members for multiple role IDs in a single pass."""
    valid_ids: set[int] = set()
    counts: dict[int, int] = {}
    for rid in role_ids:
        counts[rid] = 0
        if guild.get_role(rid) is not None:
            valid_ids.add(rid)

    if not valid_ids:
        return counts

    for member in guild.members:
        for role in member.roles:
            if role.id in valid_ids:
                counts[role.id] += 1
    return counts


async def resolve_channel(
    client: discord.Client,
    guild: discord.Guild | None,
    channel_id: int,
) -> discord.abc.GuildChannel | discord.Thread | discord.abc.PrivateChannel | None:
    """Resolve a Discord channel from cache first, falling back to REST only on miss."""
    if guild is not None:
        cached = guild.get_channel(channel_id)
        if cached is not None:
            return cached
    cached_global = client.get_channel(channel_id)
    if cached_global is not None:
        return cached_global
    if guild is not None:
        return await guild.fetch_channel(channel_id)
    return await client.fetch_channel(channel_id)


async def resolve_member(
    guild: discord.Guild,
    member_id: int,
) -> discord.Member | None:
    """Resolve a guild member from cache first, falling back to REST on miss."""
    member = guild.get_member(member_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(member_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


async def send_user_dm(
    user: discord.User | discord.Member,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    reason: str = "notification",
) -> tuple[bool, str]:
    """Send a DM and return (success, readable_status); never silently swallow failures."""
    try:
        dm = user.dm_channel or await user.create_dm()
        await dm.send(
            content=content,
            embed=embed if embed is not None else discord.utils.MISSING,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True, "DM sent successfully."
    except discord.Forbidden:
        return False, "DM blocked by the user's privacy settings or server DM settings."
    except discord.NotFound:
        return False, "DM failed: user or DM channel was not found."
    except discord.RateLimited as exc:
        log_discord_http_error(logger, f"send_user_dm({reason})", exc)
        return False, f"DM failed (rate limited for {exc.retry_after:.1f}s)."
    except discord.HTTPException as exc:
        return False, f"DM failed ({exc.status}): {exc.text or str(exc)}"
    except Exception as exc:
        return False, f"DM failed ({type(exc).__name__}): {exc}"


async def send_interaction_response(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
) -> None:
    """Safely send an interaction response or followup depending on response state."""
    kwargs: dict[str, Any] = {"ephemeral": ephemeral}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view

    try:
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except (discord.HTTPException, discord.RateLimited) as exc:
        log_discord_http_error(logger, "interaction response", exc)
