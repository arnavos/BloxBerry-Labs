"""Core Discord client subclass orchestrating cogs, persistent views, sync, and services."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.database import StateStore
from bot.services import (
    ModerationService,
    PanelService,
    RecruitmentService,
    TicketService,
    send_interaction_response,
)
from bot.utils import log_discord_http_error
from bot.views import (
    ApplicationReviewView,
    MainPanelView,
    SkillPanelView,
    TicketControlView,
    TicketPanelView,
)

logger = logging.getLogger(__name__)

EXTENSIONS: tuple[str, ...] = (
    "bot.commands.tickets",
    "bot.events.member_events",
    "bot.events.message_events",
)


def build_bot_intents() -> discord.Intents:
    """Create the exact Discord gateway intents required by the bot."""
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    return intents


def _empty_prefix(_bot: commands.Bot, _message: discord.Message) -> list[str]:
    """Disable commands.Bot default prefix parser; text commands use MessageEventsCog."""
    return []


class BloxBerryBot(commands.Bot):
    """Modular Discord bot client with rate-limit-aware startup and clean shutdown."""

    def __init__(
        self,
        *,
        settings: Settings,
        state_store: StateStore | None = None,
        intents: discord.Intents | None = None,
        **kwargs: Any,
    ) -> None:
        resolved_intents = intents if intents is not None else build_bot_intents()
        super().__init__(
            command_prefix=_empty_prefix,
            help_command=None,
            intents=resolved_intents,
            chunk_guilds_at_startup=False,
            max_ratelimit_timeout=settings.max_ratelimit_timeout,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="BloxBerry Labs",
            ),
            status=discord.Status.online,
            **kwargs,
        )
        self.settings = settings
        self.state_store = state_store or StateStore(
            settings.data_file,
            settings.roles.main_roles.keys(),
        )
        self.panel_service = PanelService(self, settings, self.state_store)
        self.recruitment_service = RecruitmentService(settings, self.state_store)
        self.ticket_service = TicketService(settings, self.state_store)
        self.moderation_service = ModerationService(settings, self.state_store)

    @property
    def state(self) -> dict[str, Any]:
        """Expose underlying state mapping for compatibility and diagnostics."""
        return self.state_store.state

    def has_pending_application(self, user_id: int) -> bool:
        """Return True if the user has a pending recruitment application."""
        return self.state_store.has_pending_application(user_id)

    def compute_command_tree_digest(self, *, guild: discord.abc.Snowflake | None = None) -> str:
        """Return a deterministic SHA-256 hash of the registered application commands."""
        payload = [command.to_dict(self.tree) for command in self.tree.get_commands(guild=guild)]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def sync_commands_if_needed(self) -> int | None:
        """Sync slash commands according to SYNC_COMMANDS_MODE without spamming PUT /commands."""
        mode = self.settings.sync_commands_mode
        if mode == "never":
            logger.info("Slash command sync skipped (SYNC_COMMANDS_MODE=never).")
            return None

        target_guild: discord.Object | None = None
        if self.settings.discord_guild_id is not None:
            target_guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=target_guild)

        digest = self.compute_command_tree_digest(guild=target_guild)
        if self.state_store.commands_synced_in_process:
            logger.info("Slash command sync skipped (already synced in this process).")
            return None

        if mode == "auto" and self.state_store.get_command_sync_hash() == digest:
            self.state_store.commands_synced_in_process = True
            logger.info(
                "Slash command sync skipped (command definitions unchanged: %s).",
                digest[:12],
            )
            return None

        try:
            synced = await self.tree.sync(guild=target_guild)
        except (discord.HTTPException, discord.RateLimited) as exc:
            log_discord_http_error(logger, "slash-command sync", exc)
            return None
        except Exception as exc:
            logger.error("Slash command sync failed: %s: %s", type(exc).__name__, exc)
            return None

        self.state_store.commands_synced_in_process = True
        async with self.state_store.data_lock:
            self.state_store.set_command_sync_hash(digest)
            await self.state_store.save()

        scope_label = f"guild {target_guild.id}" if target_guild is not None else "global"
        logger.info("Synced %d slash commands (%s).", len(synced), scope_label)
        return len(synced)

    async def setup_hook(self) -> None:
        """Register persistent views, load cogs/extensions, and conditionally sync commands."""
        self.add_view(MainPanelView())
        self.add_view(SkillPanelView(self.settings))
        self.add_view(TicketPanelView(self.settings))
        self.add_view(TicketControlView())
        self.add_view(ApplicationReviewView())

        for extension in EXTENSIONS:
            if extension not in self.extensions:
                await self.load_extension(extension)

        self.tree.error(self.on_app_command_error)
        await self.sync_commands_if_needed()

    async def on_ready(self) -> None:
        """Log readiness details and reconcile managed recruitment/ticket panels."""
        logger.info("Logged in as %s.", self.user)
        logger.info('[PRESENCE] Configured activity "Watching BloxBerry Labs".')
        logger.info(
            "[WELCOME] members_intent=%s; message_content_intent=%s; welcome_channel_id=%s",
            self.intents.members,
            self.intents.message_content,
            self.settings.channels.welcome,
        )

        if self.panel_service.panels_ready:
            return

        # Existing welcome embed is deliberately NOT posted or edited.
        ok = await self.panel_service.ensure_all_panels(
            main_view_factory=MainPanelView,
            skill_view_factory=lambda: SkillPanelView(self.settings),
            ticket_view_factory=lambda: TicketPanelView(self.settings),
        )
        if ok:
            logger.info("BloxBerry Labs all-in-one bot is ready.")
        else:
            logger.warning(
                "Bot connected, but panel setup encountered an error and will retry on next ready event."
            )

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Handle uncaught slash-command errors cleanly without leaking tracebacks."""
        original = getattr(error, "original", error)
        if isinstance(original, (discord.HTTPException, discord.RateLimited)):
            log_discord_http_error(logger, f"app_command:{interaction.command}", original)
        elif isinstance(error, app_commands.CheckFailure):
            await send_interaction_response(
                interaction,
                "You do not meet the requirements to run this command here.",
                ephemeral=True,
            )
            return
        else:
            logger.error(
                "Unhandled slash-command error: %s: %s",
                type(original).__name__,
                original,
                exc_info=original,
            )

        await send_interaction_response(
            interaction,
            "Could not complete that command right now. Please try again shortly.",
            ephemeral=True,
        )

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
        """Log unhandled gateway event exceptions through structured logging."""
        logger.exception("Unhandled exception in event %s", event_method)

    async def close(self) -> None:
        """Cancel managed background tasks before closing WebSocket and HTTP sessions."""
        await self.panel_service.shutdown()
        await super().close()
