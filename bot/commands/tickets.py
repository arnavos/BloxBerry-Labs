"""Slash command cog for ticket management (`/ticket add`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot.client import BloxBerryBot


@app_commands.guild_only()
class TicketCommandsCog(
    commands.GroupCog,
    group_name="ticket",
    group_description="Ticket management commands",
):
    """Guild-only `/ticket` application command group."""

    def __init__(self, bot: BloxBerryBot) -> None:
        self.bot = bot
        super().__init__()

    @app_commands.command(name="add", description="Add a member to the current ticket")
    @app_commands.describe(user="Member to add to this ticket")
    async def add(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        """Add a member to the current open ticket channel."""
        await self.bot.ticket_service.add_member_to_ticket(interaction, user)


async def setup(bot: BloxBerryBot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(TicketCommandsCog(bot))
