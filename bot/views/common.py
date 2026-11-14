"""Shared UI view/modal base classes and interaction error handling."""

from __future__ import annotations

import logging
from typing import Any

import discord

from bot.services.discord_helpers import send_interaction_response
from bot.utils import log_discord_http_error

logger = logging.getLogger(__name__)


async def handle_ui_error(
    interaction: discord.Interaction,
    error: Exception,
    source_name: str,
) -> None:
    """Log UI interaction errors consistently and inform the user ephemerally."""
    if isinstance(error, (discord.HTTPException, discord.RateLimited)):
        log_discord_http_error(logger, source_name, error)
    else:
        logger.error(
            "Unhandled error in %s: %s: %s",
            source_name,
            type(error).__name__,
            error,
            exc_info=error,
        )

    await send_interaction_response(
        interaction,
        "Something went wrong while processing that action. Please try again in a moment.",
        ephemeral=True,
    )


class SafeView(discord.ui.View):
    """discord.ui.View with consistent error logging and user-facing fallback."""

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
        /,
    ) -> None:
        await handle_ui_error(interaction, error, f"{type(self).__name__}.{type(item).__name__}")


class SafeModal(discord.ui.Modal):
    """discord.ui.Modal with consistent error logging and user-facing fallback."""

    async def on_error(  # type: ignore[override]
        self,
        interaction: discord.Interaction,
        error: Exception,
        /,
    ) -> None:
        await handle_ui_error(interaction, error, type(self).__name__)
