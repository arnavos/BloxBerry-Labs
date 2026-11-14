"""Moderation service for channel message purging."""

from __future__ import annotations

import contextlib
import logging
from datetime import timedelta

import discord

from bot.config import Settings
from bot.database import StateStore
from bot.utils import log_discord_http_error

logger = logging.getLogger(__name__)


class ModerationService:
    """Executes authorized channel moderation operations with concurrency safety."""

    def __init__(self, settings: Settings, state_store: StateStore) -> None:
        self.settings = settings
        self.state_store = state_store

    async def purge_channel_messages(self, message: discord.Message, amount: int) -> None:
        """Delete `amount` messages immediately before `message`, then delete `message`."""
        if (
            not message.guild
            or not isinstance(message.author, discord.Member)
            or not any(r.id == self.settings.roles.purge_auth for r in message.author.roles)
        ):
            await message.reply("You are not authorized to use purge.", delete_after=5)
            return

        if (
            message.guild.me is None
            or not message.channel.permissions_for(message.guild.me).manage_messages
        ):
            await message.reply(
                "I need the **Manage Messages** permission in this channel.",
                delete_after=7,
            )
            return

        if not isinstance(
            message.channel,
            (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel),
        ):
            return

        channel_lock = self.state_store.get_purge_channel_lock(message.channel.id)
        async with channel_lock:
            targets: list[discord.Message] = []
            try:
                async for msg in message.channel.history(
                    limit=amount,
                    before=message,
                    oldest_first=False,
                ):
                    targets.append(msg)
                    if len(targets) >= amount:
                        break
            except discord.Forbidden:
                await message.reply(
                    "I need **View Channel** and **Read Message History** permissions.",
                    delete_after=7,
                )
                return
            except (discord.HTTPException, discord.RateLimited) as exc:
                log_discord_http_error(logger, f"purge history in {message.channel.id}", exc)
                await message.reply(
                    f"Couldn't read channel history: {exc}",
                    delete_after=7,
                )
                return

            deleted_count = 0
            cutoff = discord.utils.utcnow() - timedelta(days=14)
            recent = [m for m in targets if m.created_at > cutoff]
            old = [m for m in targets if m.created_at <= cutoff]

            try:
                for i in range(0, len(recent), 100):
                    batch = recent[i : i + 100]
                    if len(batch) == 1:
                        with contextlib.suppress(discord.NotFound):
                            await batch[0].delete()
                            deleted_count += 1
                    elif batch:
                        await message.channel.delete_messages(
                            batch,
                            reason=f"Purge requested by {message.author}",
                        )
                        deleted_count += len(batch)

                for msg in old:
                    with contextlib.suppress(discord.NotFound):
                        await msg.delete()
                        deleted_count += 1

                with contextlib.suppress(discord.NotFound):
                    await message.delete()

                logger.info(
                    "Purge completed in #%s (%s); deleted %d requested messages plus the command.",
                    getattr(message.channel, "name", message.channel.id),
                    message.channel.id,
                    deleted_count,
                )
            except discord.Forbidden as exc:
                logger.warning("Purge permission error in channel %s: %s", message.channel.id, exc)
                with contextlib.suppress(discord.HTTPException, discord.RateLimited):
                    await message.channel.send(
                        "I couldn't finish the purge. Check my **Manage Messages** permission.",
                        delete_after=7,
                    )
            except (discord.HTTPException, discord.RateLimited) as exc:
                log_discord_http_error(logger, f"purge delete in {message.channel.id}", exc)
                with contextlib.suppress(discord.HTTPException, discord.RateLimited):
                    await message.channel.send(
                        f"Purge stopped because of a Discord error: {exc}",
                        delete_after=7,
                    )
