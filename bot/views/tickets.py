"""Support ticket panel, ticket control bar, and close confirmation views."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import discord

from bot.config import Settings
from bot.views.common import SafeView

if TYPE_CHECKING:
    from bot.client import BloxBerryBot


class TicketTypeSelect(discord.ui.Select["TicketPanelView"]):
    """Persistent category dropdown used on the support center ticket panel."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or Settings()
        options = [
            discord.SelectOption(
                label=name,
                value=name,
                description=f"Open a {name.lower()} ticket",
            )
            for name in cfg.ticket_types
        ]
        super().__init__(
            placeholder="Choose a ticket category...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="bb_all_ticket_type",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = cast("BloxBerryBot", interaction.client)
        await bot.ticket_service.create_ticket(
            interaction,
            self.values[0],
            TicketControlView,
        )


class TicketPanelView(SafeView):
    """Persistent support ticket panel view."""

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect(settings))


class ClaimButton(discord.ui.Button["TicketControlView"]):
    """Persistent button allowing authorized staff to claim a ticket."""

    def __init__(self) -> None:
        super().__init__(
            label="Claim",
            emoji="🙋",
            style=discord.ButtonStyle.primary,
            custom_id="bb_all_ticket_claim",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = cast("BloxBerryBot", interaction.client)
        await bot.ticket_service.claim_ticket(interaction)


class UnclaimButton(discord.ui.Button["TicketControlView"]):
    """Persistent button allowing the claimant or an admin to unclaim a ticket."""

    def __init__(self) -> None:
        super().__init__(
            label="Unclaim",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id="bb_all_ticket_unclaim",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = cast("BloxBerryBot", interaction.client)
        await bot.ticket_service.unclaim_ticket(interaction)


class CloseButton(discord.ui.Button["TicketControlView"]):
    """Persistent button initiating ticket closure confirmation."""

    def __init__(self) -> None:
        super().__init__(
            label="Close Ticket",
            emoji="🔒",
            style=discord.ButtonStyle.danger,
            custom_id="bb_all_ticket_close",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = cast("BloxBerryBot", interaction.client)
        await bot.ticket_service.request_close_ticket(interaction, CloseConfirmView)


class TicketControlView(SafeView):
    """Persistent control bar posted inside each ticket channel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(ClaimButton())
        self.add_item(UnclaimButton())
        self.add_item(CloseButton())


class CloseConfirmButton(discord.ui.Button["CloseConfirmView"]):
    """Confirmation button that archives and deletes the ticket channel."""

    def __init__(self) -> None:
        super().__init__(
            label="Confirm Close",
            emoji="🗑️",
            style=discord.ButtonStyle.danger,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = cast("BloxBerryBot", interaction.client)
        await bot.ticket_service.close_ticket(interaction)


class CloseCancelButton(discord.ui.Button["CloseConfirmView"]):
    """Button cancelling an ephemeral ticket closure confirmation prompt."""

    def __init__(self) -> None:
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content="Ticket closure cancelled.",
            view=None,
        )


class CloseConfirmView(SafeView):
    """Ephemeral confirmation view before closing a ticket."""

    def __init__(self) -> None:
        super().__init__(timeout=60)
        self.add_item(CloseConfirmButton())
        self.add_item(CloseCancelButton())
