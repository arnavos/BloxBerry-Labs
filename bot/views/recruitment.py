"""Recruitment panels, dropdowns, modals, and application review views."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import discord

from bot.config import Settings
from bot.services.discord_helpers import count_members_for_roles
from bot.services.recruitment_service import ApplicationAnswers
from bot.views.common import SafeModal, SafeView

if TYPE_CHECKING:
    from bot.client import BloxBerryBot


class RecruitmentModal(SafeModal):
    """Application form modal for main positions and skill-based roles."""

    def __init__(self, app_type: str, selected_names: list[str]) -> None:
        super().__init__(
            title="Main Role Application" if app_type == "main" else "Skill Role Application",
            timeout=300,
        )
        self.app_type = app_type
        self.selected_names = selected_names

        self.experience: discord.ui.TextInput[RecruitmentModal] = discord.ui.TextInput(
            label="Previous Experience",
            placeholder="Tell us about your relevant experience...",
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.skills: discord.ui.TextInput[RecruitmentModal] = discord.ui.TextInput(
            label="Your Skills",
            placeholder="What skills make you suitable for these roles?",
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.portfolio: discord.ui.TextInput[RecruitmentModal] = discord.ui.TextInput(
            label="Portfolio / Previous Work",
            placeholder="Share links or write None.",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=False,
        )
        self.availability: discord.ui.TextInput[RecruitmentModal] = discord.ui.TextInput(
            label="Availability",
            placeholder="How many hours can you contribute weekly?",
            max_length=300,
        )
        self.reason: discord.ui.TextInput[RecruitmentModal] = discord.ui.TextInput(
            label="Why BloxBerry Labs?",
            placeholder="Why do you want to join our team?",
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        for item in (
            self.experience,
            self.skills,
            self.portfolio,
            self.availability,
            self.reason,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = cast("BloxBerryBot", interaction.client)
        answers = ApplicationAnswers(
            experience=self.experience.value,
            skills=self.skills.value,
            portfolio=self.portfolio.value,
            availability=self.availability.value,
            reason=self.reason.value,
        )
        await bot.recruitment_service.submit_application(
            interaction=interaction,
            app_type=self.app_type,
            selected_names=self.selected_names,
            answers=answers,
            review_view_factory=ApplicationReviewView,
        )


class MainRoleSelect(discord.ui.Select["MainRoleView"]):
    """Dropdown of open main recruitment positions with remaining seat counts."""

    def __init__(self, guild: discord.Guild, settings: Settings) -> None:
        counts = count_members_for_roles(guild, settings.roles.main_roles.values())
        options: list[discord.SelectOption] = []
        for name, rid in settings.roles.main_roles.items():
            count = counts.get(rid, 0)
            limit = settings.roles.seat_limits[name]
            if count < limit:
                options.append(
                    discord.SelectOption(
                        label=name,
                        value=name,
                        description=f"{limit - count} seat(s) remaining",
                    )
                )
        if not options:
            options = [discord.SelectOption(label="All positions are full", value="none")]
        super().__init__(
            placeholder="Select your main position...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        name = self.values[0]
        if name == "none":
            await interaction.response.send_message(
                "All main positions are full.",
                ephemeral=True,
            )
            return
        bot = cast("BloxBerryBot", interaction.client)
        if bot.has_pending_application(interaction.user.id):
            await interaction.response.send_message(
                "You already have a pending application.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(RecruitmentModal("main", [name]))


class MainRoleView(SafeView):
    """Ephemeral view wrapping the MainRoleSelect dropdown."""

    def __init__(self, guild: discord.Guild, settings: Settings) -> None:
        super().__init__(timeout=180)
        self.add_item(MainRoleSelect(guild, settings))


class MainApplyButton(discord.ui.Button["MainPanelView"]):
    """Persistent button initiating a main recruitment application."""

    def __init__(self) -> None:
        super().__init__(
            label="Apply for a Main Role",
            emoji="🍓",
            style=discord.ButtonStyle.primary,
            custom_id="bb_all_main_apply",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = cast("BloxBerryBot", interaction.client)
        if bot.has_pending_application(interaction.user.id):
            await interaction.response.send_message(
                "You already have a pending application.",
                ephemeral=True,
            )
            return
        if interaction.guild is None:
            await interaction.response.send_message(
                "This button can only be used inside the server.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Choose the main position you want to apply for:",
            view=MainRoleView(interaction.guild, bot.settings),
            ephemeral=True,
        )


class MainPanelView(SafeView):
    """Persistent main recruitment panel view."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(MainApplyButton())


class SkillRoleSelect(discord.ui.Select["SkillPanelView"]):
    """Persistent multi-select dropdown for skill-based roles."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or Settings()
        options = [
            discord.SelectOption(
                label=name,
                value=name,
                description=f"Apply for {name}",
            )
            for name in cfg.roles.skill_roles
        ]
        super().__init__(
            placeholder="Select one or multiple skill roles...",
            min_values=1,
            max_values=len(options),
            options=options,
            custom_id="bb_all_skill_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = cast("BloxBerryBot", interaction.client)
        if bot.has_pending_application(interaction.user.id):
            await interaction.response.send_message(
                "You already have a pending application. Wait for management to review it.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Selected roles:\n"
            + "\n".join(f"• **{n}**" for n in self.values)
            + "\n\nClick below to open the application form.",
            view=SkillContinueView(list(self.values)),
            ephemeral=True,
        )


class SkillContinueButton(discord.ui.Button["SkillContinueView"]):
    """Button opening the recruitment modal for previously selected skill roles."""

    def __init__(self, selected_names: list[str]) -> None:
        super().__init__(
            label="Continue to Application Form",
            emoji="📝",
            style=discord.ButtonStyle.success,
        )
        self.selected_names = selected_names

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = cast("BloxBerryBot", interaction.client)
        if bot.has_pending_application(interaction.user.id):
            await interaction.response.send_message(
                "You already have a pending application.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(RecruitmentModal("skill", self.selected_names))


class SkillContinueView(SafeView):
    """Ephemeral view allowing an applicant to proceed to the skill application modal."""

    def __init__(self, selected_names: list[str]) -> None:
        super().__init__(timeout=180)
        self.add_item(SkillContinueButton(selected_names))


class SkillPanelView(SafeView):
    """Persistent skill-based recruitment panel view."""

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(timeout=None)
        self.add_item(SkillRoleSelect(settings))


class AcceptApplicationButton(discord.ui.Button["ApplicationReviewView"]):
    """Persistent reviewer button to accept an application."""

    def __init__(self) -> None:
        super().__init__(
            label="Accept",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id="bb_all_application_accept",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = cast("BloxBerryBot", interaction.client)
        await bot.recruitment_service.finalize_application(
            interaction,
            "accept",
            ApplicationReviewedView,
        )


class DenyApplicationButton(discord.ui.Button["ApplicationReviewView"]):
    """Persistent reviewer button to deny an application."""

    def __init__(self) -> None:
        super().__init__(
            label="Deny",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id="bb_all_application_deny",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = cast("BloxBerryBot", interaction.client)
        await bot.recruitment_service.finalize_application(
            interaction,
            "deny",
            ApplicationReviewedView,
        )


class ApplicationReviewView(SafeView):
    """Persistent review action view attached to pending application embeds."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(AcceptApplicationButton())
        self.add_item(DenyApplicationButton())


class ApplicationReviewedView(SafeView):
    """Disabled view replacing review actions once an application is finalized."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Application Reviewed",
                emoji="🔒",
                style=discord.ButtonStyle.secondary,
                disabled=True,
            )
        )
