"""Panel embedding, startup reconciliation, and debounced refresh service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

import discord

from bot.config import Settings
from bot.database import StateStore
from bot.services.discord_helpers import count_members_for_roles, resolve_channel
from bot.utils import log_discord_http_error

logger = logging.getLogger(__name__)


def seat_description(guild: discord.Guild, settings: Settings) -> str:
    """Format the live seat availability block for main recruitment positions."""
    counts = count_members_for_roles(guild, settings.roles.main_roles.values())
    lines: list[str] = []
    for name, role_id in settings.roles.main_roles.items():
        count = counts.get(role_id, 0)
        limit = settings.roles.seat_limits[name]
        if count >= limit:
            lines.append(f"~~{name} — {count}/{limit}~~ ❌ **FULL**")
        else:
            lines.append(f"**{name}** — `{count}/{limit}` seats filled 🟢")
    return "\n".join(lines)


def build_main_recruitment_embed(
    guild: discord.Guild,
    settings: Settings,
) -> discord.Embed:
    """Build the Main Recruitment panel embed."""
    embed = discord.Embed(
        title="🍓 WANT TO JOIN BLOXBERRY LABS?",
        description=(
            "# Become Part of Our Team\n"
            "We're looking for talented, creative and motivated people to help us build something amazing.\n\n"
            "**WHY JOIN US?**\n"
            "• Work with a creative team.\n• Collaborate on Roblox projects.\n"
            "• Improve your skills through real projects.\n• Bring your ideas to life.\n\n"
            "---\n\n# AVAILABLE MAIN POSITIONS\n"
            f"{seat_description(guild, settings)}\n\n---\n\n"
            "**HOW TO APPLY**\n"
            "1. Click **Apply for a Main Role**.\n"
            "2. Select your desired position.\n"
            "3. Complete the application form.\n"
            "4. Management will review your application.\n\n"
            "Want skill-based roles instead? Use the separate Skill Roles panel.\n\n"
            "*Your skills matter. Your contribution makes the difference.*\n"
            "**BloxBerry Labs** 🍓"
        ),
        color=settings.color,
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=settings.main_panel_marker)
    return embed


def build_skill_recruitment_embed(
    guild: discord.Guild,
    settings: Settings,
) -> discord.Embed:
    """Build the Skill-Based Recruitment panel embed."""
    role_lines = "\n".join(f"• <@&{rid}>" for rid in settings.roles.skill_roles.values())
    embed = discord.Embed(
        title="🍓 BLOXBERRY LABS | SKILL-BASED RECRUITMENT",
        description=(
            "# Join Through Your Skills!\n"
            "Not interested in one of the main positions? Apply for skill-based roles instead.\n\n"
            "**AVAILABLE SKILL ROLES**\n"
            f"{role_lines}\n\n---\n\n"
            "**HOW TO APPLY**\n"
            "1. Select one or multiple roles from the dropdown.\n"
            "2. Click **Continue to Application Form**.\n"
            "3. Complete the application form.\n"
            "4. Management will review all selected roles.\n\n"
            "**IMPORTANT**\n"
            "• You can select multiple skill roles in one application.\n"
            "• Every selected role will appear as a Discord role mention in your application.\n"
            "• You do not need to apply for a main position.\n"
            "• Selection does not guarantee acceptance.\n\n"
            "*Show us what you can bring to the team.*\n**BloxBerry Labs** 🍓"
        ),
        color=settings.color,
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=settings.skill_panel_marker)
    return embed


def build_ticket_panel_embed(settings: Settings) -> discord.Embed:
    """Build the Support Center ticket panel embed."""
    embed = discord.Embed(
        title="🎟️ BLOXBERRY LABS | SUPPORT CENTER",
        description=(
            "Need help or want to contact a department? Open a private ticket below.\n\n"
            "**Available departments**\n"
            "🎨 Creative Department Request\n"
            "📢 Marketing Request\n"
            "🛠️ Technical Support\n"
            "🤝 Partnerships\n"
            "💬 General Inquiry\n"
            "🚨 Member Report\n\n"
            "Choose the category that best matches your request. Please do not open multiple tickets for the same issue."
        ),
        color=settings.color,
    )
    embed.set_footer(text=settings.ticket_panel_marker)
    return embed


def _extract_component_signature(components: Any) -> list[tuple[Any, ...]]:
    signature: list[tuple[Any, ...]] = []
    for row in components or ():
        children = getattr(row, "children", [row])
        for item in children:
            options = getattr(item, "options", None)
            option_values = (
                tuple(getattr(opt, "value", None) for opt in options) if options is not None else ()
            )
            signature.append(
                (
                    getattr(item, "custom_id", None),
                    getattr(item, "label", None),
                    getattr(item, "placeholder", None),
                    option_values,
                )
            )
    return signature


def _extract_view_signature(view: discord.ui.View) -> list[tuple[Any, ...]]:
    signature: list[tuple[Any, ...]] = []
    for item in view.children:
        options = getattr(item, "options", None)
        option_values = (
            tuple(getattr(opt, "value", None) for opt in options) if options is not None else ()
        )
        signature.append(
            (
                getattr(item, "custom_id", None),
                getattr(item, "label", None),
                getattr(item, "placeholder", None),
                option_values,
            )
        )
    return signature


def panel_message_matches(
    message: discord.Message,
    embed: discord.Embed,
    view: discord.ui.View,
) -> bool:
    """Return True if an existing Discord panel message already matches target embed & view."""
    embeds = getattr(message, "embeds", None) or []
    if len(embeds) != 1:
        return False

    current = embeds[0]
    current_color = current.color.value if current.color else None
    target_color = embed.color.value if embed.color else None
    current_footer = current.footer.text if current.footer else None
    target_footer = embed.footer.text if embed.footer else None
    current_thumb = current.thumbnail.url if current.thumbnail else None
    target_thumb = embed.thumbnail.url if embed.thumbnail else None

    if (
        (current.title or "") != (embed.title or "")
        or (current.description or "") != (embed.description or "")
        or current_color != target_color
        or current_footer != target_footer
        or (current_thumb or "") != (target_thumb or "")
    ):
        return False

    return _extract_component_signature(
        getattr(message, "components", None)
    ) == _extract_view_signature(view)


class PanelService:
    """Manages persistent panel setup, caching, and debounced live seat updates."""

    def __init__(
        self,
        bot: discord.Client,
        settings: Settings,
        state_store: StateStore,
    ) -> None:
        self.bot = bot
        self.settings = settings
        self.state_store = state_store
        self.main_panel_message: discord.Message | None = None
        self.skill_panel_message: discord.Message | None = None
        self.ticket_panel_message: discord.Message | None = None
        self.panels_ready: bool = False
        self._setup_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None
        self._refresh_pending: bool = False

    async def _find_existing_panel_message(
        self,
        channel: discord.TextChannel,
        panel_key: str,
        marker: str,
    ) -> discord.Message | None:
        bot_user = self.bot.user
        if bot_user is None:
            return None

        known_id = self.state_store.get_panel_message_id(panel_key)
        if known_id is not None:
            try:
                msg = await channel.fetch_message(known_id)
                if (
                    msg.author.id == bot_user.id
                    and msg.embeds
                    and (msg.embeds[0].footer.text or "") == marker
                ):
                    return msg
            except (discord.NotFound, discord.Forbidden):
                pass
            except (discord.HTTPException, discord.RateLimited) as exc:
                log_discord_http_error(logger, f"fetch_message({panel_key})", exc)

        async for msg in channel.history(limit=100):
            if (
                msg.author.id == bot_user.id
                and msg.embeds
                and (msg.embeds[0].footer.text or "") == marker
            ):
                return msg
        return None

    async def ensure_panel(
        self,
        *,
        panel_key: str,
        channel_id: int,
        marker: str,
        embed_builder: Callable[[discord.Guild], discord.Embed],
        view: discord.ui.View,
        attr_name: str,
    ) -> discord.Message:
        """Locate or create a panel message, editing only when content/components differ."""
        channel = await resolve_channel(self.bot, None, channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"Channel {channel_id} is not a text channel.")

        embed = embed_builder(channel.guild)
        found = await self._find_existing_panel_message(channel, panel_key, marker)

        if found is not None:
            if panel_message_matches(found, embed, view):
                logger.info("Panel %s in channel %s already up to date.", panel_key, channel_id)
                target_message = found
            else:
                target_message = await found.edit(embed=embed, view=view)
                logger.info("Updated panel %s in channel %s.", panel_key, channel_id)
        else:
            target_message = await channel.send(embed=embed, view=view)
            logger.info("Posted new panel %s in channel %s.", panel_key, channel_id)

        setattr(self, attr_name, target_message)
        if self.state_store.get_panel_message_id(panel_key) != target_message.id:
            async with self.state_store.data_lock:
                self.state_store.set_panel_message_id(panel_key, target_message.id)
                await self.state_store.save()

        return target_message

    async def ensure_all_panels(
        self,
        *,
        main_view_factory: Callable[[], discord.ui.View],
        skill_view_factory: Callable[[], discord.ui.View],
        ticket_view_factory: Callable[[], discord.ui.View],
    ) -> bool:
        """Ensure all recruitment and ticket panels exist; returns True on complete success."""
        async with self._setup_lock:
            if self.panels_ready:
                return True
            try:
                await self.ensure_panel(
                    panel_key="main",
                    channel_id=self.settings.channels.main_recruitment,
                    marker=self.settings.main_panel_marker,
                    embed_builder=lambda g: build_main_recruitment_embed(g, self.settings),
                    view=main_view_factory(),
                    attr_name="main_panel_message",
                )
                await self.ensure_panel(
                    panel_key="skill",
                    channel_id=self.settings.channels.skill_recruitment,
                    marker=self.settings.skill_panel_marker,
                    embed_builder=lambda g: build_skill_recruitment_embed(g, self.settings),
                    view=skill_view_factory(),
                    attr_name="skill_panel_message",
                )
                await self.ensure_panel(
                    panel_key="ticket",
                    channel_id=self.settings.channels.ticket_panel,
                    marker=self.settings.ticket_panel_marker,
                    embed_builder=lambda _g: build_ticket_panel_embed(self.settings),
                    view=ticket_view_factory(),
                    attr_name="ticket_panel_message",
                )
            except (discord.HTTPException, discord.RateLimited) as exc:
                log_discord_http_error(logger, "startup panel setup", exc)
                return False
            except Exception as exc:
                logger.warning("Panel setup warning: %s: %s", type(exc).__name__, exc)
                return False

            self.panels_ready = True
            return True

    def schedule_main_panel_refresh(
        self,
        main_view_factory: Callable[[], discord.ui.View],
    ) -> None:
        """Coalesce bursts of role/member events into a single debounced panel edit."""
        self._refresh_pending = True
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(
                self._run_debounced_main_panel_refresh(main_view_factory),
                name="bloxberry-main-panel-refresh",
            )
            self._refresh_task.add_done_callback(self._log_refresh_task_error)

    @staticmethod
    def _log_refresh_task_error(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "Main panel refresh background task failed: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=exc,
            )

    async def _run_debounced_main_panel_refresh(
        self,
        main_view_factory: Callable[[], discord.ui.View],
    ) -> None:
        while self._refresh_pending:
            await asyncio.sleep(self.settings.panel_refresh_debounce_seconds)
            self._refresh_pending = False
            await self.refresh_main_panel(main_view_factory)

    async def refresh_main_panel(
        self,
        main_view_factory: Callable[[], discord.ui.View],
    ) -> None:
        """Edit the cached main recruitment panel message if seat availability changed."""
        if self.main_panel_message is None:
            return
        guild = self.main_panel_message.guild
        if guild is None:
            return
        embed = build_main_recruitment_embed(guild, self.settings)
        view = main_view_factory()
        if panel_message_matches(self.main_panel_message, embed, view):
            logger.debug("Skipped main recruitment panel edit; embed already matches.")
            return
        try:
            self.main_panel_message = await self.main_panel_message.edit(
                embed=embed,
                view=view,
            )
        except discord.NotFound:
            self.main_panel_message = None
            self.panels_ready = False
        except (discord.HTTPException, discord.RateLimited) as exc:
            log_discord_http_error(logger, "main recruitment panel refresh", exc)

    async def shutdown(self) -> None:
        """Cancel and await any pending debounced refresh task during shutdown."""
        self._refresh_pending = False
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._refresh_task
        self._refresh_task = None
