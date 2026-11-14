"""Member lifecycle event listeners (welcome flow, auto-role, live seat updates)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from bot.services.discord_helpers import resolve_channel, resolve_member
from bot.utils import log_discord_http_error
from bot.views import MainPanelView

if TYPE_CHECKING:
    from bot.client import BloxBerryBot

logger = logging.getLogger(__name__)


class MemberEventsCog(commands.Cog):
    """Handles member joins, role updates, and server departures."""

    def __init__(self, bot: BloxBerryBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Assign the automatic member role and post a welcome message with member number."""
        logger.info(
            "[WELCOME] member_join received: %s (%s) in %s (%s)",
            member,
            member.id,
            member.guild.name,
            member.guild.id,
        )

        settings = self.bot.settings
        role = member.guild.get_role(settings.roles.auto_member)
        if role is None:
            logger.warning(
                "[WELCOME] Member role %s was not found.",
                settings.roles.auto_member,
            )
        else:
            try:
                await member.add_roles(role, reason="Automatic member role")
                logger.info("[WELCOME] Assigned member role to %s.", member.id)
            except discord.Forbidden as exc:
                logger.warning(
                    "[WELCOME] Could not assign member role; check Manage Roles and role hierarchy: %s",
                    exc,
                )
            except (discord.HTTPException, discord.RateLimited) as exc:
                log_discord_http_error(logger, "welcome member-role assignment", exc)

        try:
            channel = await resolve_channel(
                self.bot,
                member.guild,
                settings.channels.welcome,
            )
            if not isinstance(channel, discord.TextChannel):
                raise TypeError(
                    f"Welcome channel {settings.channels.welcome} is not a normal text channel."
                )

            bot_member = member.guild.me
            if bot_member is None and self.bot.user is not None:
                bot_member = await resolve_member(member.guild, self.bot.user.id)
            if bot_member is None:
                raise RuntimeError("Could not resolve bot member in guild.")

            perms = channel.permissions_for(bot_member)
            if not perms.view_channel or not perms.send_messages:
                raise RuntimeError(
                    f"Bot lacks channel permissions: view_channel={perms.view_channel}, "
                    f"send_messages={perms.send_messages}"
                )

            join_number: int | str = member.guild.member_count or "—"
            joined_at = member.joined_at or discord.utils.utcnow()
            unix_joined = int(joined_at.timestamp())

            embed = discord.Embed(
                title="🍓 A new berry just joined!",
                description=(
                    f"Welcome to **BloxBerry Labs**, {member.mention}!\n"
                    "We're happy to have you here. Make yourself at home and enjoy the community."
                ),
                color=settings.color,
                timestamp=joined_at,
            )
            embed.add_field(
                name="Member",
                value=f"{member.mention}\n`{member.name}`",
                inline=True,
            )
            embed.add_field(
                name="You are member",
                value=f"**#{join_number}**",
                inline=True,
            )
            embed.add_field(
                name="Joined",
                value=f"<t:{unix_joined}:F>\n<t:{unix_joined}:R>",
                inline=False,
            )
            if member.guild.icon:
                embed.set_author(
                    name=member.guild.name,
                    icon_url=member.guild.icon.url,
                )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="BloxBerry Labs • Community")

            if perms.embed_links:
                await channel.send(
                    content=member.mention,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False),
                )
            else:
                await channel.send(
                    content=(
                        "🍓 **A new berry just joined BloxBerry Labs!**\n"
                        f"Welcome, {member.mention}!\n"
                        f"**Member number:** #{join_number}\n"
                        f"**Joined:** <t:{unix_joined}:F> (<t:{unix_joined}:R>)"
                    ),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False),
                )

            logger.info(
                "[WELCOME] Posted welcome for %s in #%s; member_number=%s; joined_at=%s",
                member.id,
                channel.name,
                join_number,
                joined_at.isoformat(),
            )
        except discord.Forbidden as exc:
            logger.warning(
                "[WELCOME] Discord denied access to channel %s. "
                "Check View Channel, Send Messages and Embed Links permissions: %s",
                settings.channels.welcome,
                exc,
            )
        except (discord.HTTPException, discord.RateLimited) as exc:
            log_discord_http_error(logger, "welcome message/channel operation", exc)
        except Exception as exc:
            logger.warning("[WELCOME] Failed: %s: %s", type(exc).__name__, exc)

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ) -> None:
        """Refresh the main recruitment panel when main-role memberships change."""
        before_role_ids = {role.id for role in before.roles}
        after_role_ids = {role.id for role in after.roles}
        changed_role_ids = before_role_ids.symmetric_difference(after_role_ids)

        if changed_role_ids.intersection(self.bot.settings.roles.main_roles.values()):
            self.bot.panel_service.schedule_main_panel_refresh(MainPanelView)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Refresh the main recruitment panel if a departing member held a main role."""
        member_role_ids = {role.id for role in member.roles}
        if member_role_ids.intersection(self.bot.settings.roles.main_roles.values()):
            self.bot.panel_service.schedule_main_panel_refresh(MainPanelView)


async def setup(bot: BloxBerryBot) -> None:
    """Discord.py extension entrypoint."""
    await bot.add_cog(MemberEventsCog(bot))
