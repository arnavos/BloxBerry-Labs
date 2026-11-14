"""Message event listeners for text commands (`purge N`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from bot.client import BloxBerryBot


class MessageEventsCog(commands.Cog):
    """Dispatches message-triggered commands while preserving existing syntax."""

    def __init__(self, bot: BloxBerryBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Handle `purge <N>` commands in guild channels."""
        if message.author.bot or not message.guild:
            return

        parts = message.content.strip().split()
        if parts and parts[0].casefold() == "purge":
            if len(parts) != 2 or not parts[1].isdigit():
                await message.reply(
                    "Use `purge 10` to delete the 10 messages before this command.",
                    delete_after=7,
                )
                return

            amount = int(parts[1])
            if amount < 1 or amount > 1000:
                await message.reply(
                    "Choose a number from **1 to 1000**.",
                    delete_after=7,
                )
                return

            await self.bot.moderation_service.purge_channel_messages(message, amount)


async def setup(bot: BloxBerryBot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(MessageEventsCog(bot))
