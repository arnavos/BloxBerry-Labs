"""Structured logging and Discord HTTP diagnostic helpers."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable

import discord


class TokenRedactingFilter(logging.Filter):
    """Mask configured secret values from log messages and tracebacks."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(s for s in secrets if s)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        rendered = record.getMessage()
        masked = rendered
        for secret in self._secrets:
            if secret in masked:
                masked = masked.replace(secret, "[REDACTED]")
        if masked != rendered:
            record.msg = masked
            record.args = ()
        return True


def configure_logging(
    level_name: str = "INFO",
    *,
    secrets: Iterable[str] = (),
) -> logging.Logger:
    """Configure root logging for both Render stdout logs and discord.py internals."""
    level = getattr(logging, level_name.upper(), None)
    if not isinstance(level, int):
        level = logging.INFO

    root = logging.getLogger()
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    secret_filter = TokenRedactingFilter(secrets)
    handler.addFilter(secret_filter)

    # Replace existing handlers only when configuring the bot process so repeated
    # startup retries do not duplicate log lines.
    root.handlers.clear()
    root.addHandler(handler)

    # Ensure discord.py HTTP rate-limit warnings and gateway events are visible.
    logging.getLogger("discord").setLevel(level)
    logging.getLogger("discord.http").setLevel(level)
    logging.getLogger("discord.gateway").setLevel(level)

    return logging.getLogger("bloxberry")


def log_discord_http_error(
    logger: logging.Logger,
    context: str,
    exc: discord.HTTPException | discord.RateLimited,
) -> None:
    """Log Discord rate-limit and HTTP failure metadata with actionable context."""
    if isinstance(exc, discord.RateLimited):
        logger.warning(
            "[DISCORD RATELIMIT] %s | retry_after=%.2fs | message=%s",
            context,
            exc.retry_after,
            exc,
        )
        return

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) if response else {}
    if exc.status == 429:
        logger.warning(
            "[DISCORD 429] %s | retry_after=%s | global=%s | scope=%s | "
            "bucket=%s | remaining=%s | reset_after=%s | message=%s",
            context,
            headers.get("Retry-After", "unknown"),
            headers.get("X-RateLimit-Global", headers.get("X-Rate-Limit-Global", "unknown")),
            headers.get("X-RateLimit-Scope", "unknown"),
            headers.get("X-RateLimit-Bucket", "unknown"),
            headers.get("X-RateLimit-Remaining", "unknown"),
            headers.get("X-RateLimit-Reset-After", "unknown"),
            exc,
        )
    else:
        logger.error(
            "[DISCORD HTTP] %s | status=%s | code=%s | %s",
            context,
            exc.status,
            exc.code,
            exc,
        )
