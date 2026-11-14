"""Async-safe JSON state store with atomic persistence and scoped locks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _default_state() -> dict[str, Any]:
    return {
        "applications": {},
        "tickets": {},
        "meta": {
            "panel_messages": {},
            "command_sync_hash": None,
        },
    }


class StateStore:
    """Manages persisted application/ticket state and instance-scoped async locks."""

    def __init__(self, data_file: Path | str, main_role_names: Iterable[str]) -> None:
        self.data_file = Path(data_file)
        self.state: dict[str, Any] = self._load_sync()
        self.data_lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()
        self.seat_locks: dict[str, asyncio.Lock] = {
            name: asyncio.Lock() for name in main_role_names
        }
        self._applicant_locks: dict[int, asyncio.Lock] = {}
        self._ticket_create_locks: dict[int, asyncio.Lock] = {}
        self._ticket_channel_locks: dict[int, asyncio.Lock] = {}
        self._purge_channel_locks: dict[int, asyncio.Lock] = {}
        self.commands_synced_in_process: bool = False

    def _load_sync(self) -> dict[str, Any]:
        if not self.data_file.exists():
            return _default_state()
        try:
            with self.data_file.open("r", encoding="utf-8") as handle:
                obj = json.load(handle)
            if not isinstance(obj, dict):
                raise TypeError("Invalid state file root; expected JSON object")
            if not isinstance(obj.get("applications"), dict):
                obj["applications"] = {}
            if not isinstance(obj.get("tickets"), dict):
                obj["tickets"] = {}
            meta = obj.get("meta")
            if not isinstance(meta, dict):
                meta = {}
                obj["meta"] = meta
            if not isinstance(meta.get("panel_messages"), dict):
                meta["panel_messages"] = {}
            meta.setdefault("command_sync_hash", None)
            return obj
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning(
                "Could not load state file %s (%s); starting with empty state.",
                self.data_file,
                exc,
            )
            return _default_state()

    def _write_atomic_sync(self, payload: str) -> None:
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = Path(f"{self.data_file}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_path, self.data_file)

    def save_sync(self) -> None:
        """Synchronously persist the current state atomically."""
        payload = json.dumps(self.state, indent=2)
        self._write_atomic_sync(payload)

    async def save(self) -> None:
        """Asynchronously persist the current state without blocking the event loop."""
        async with self._persist_lock:
            payload = json.dumps(self.state, indent=2)
            await asyncio.to_thread(self._write_atomic_sync, payload)

    @staticmethod
    def _get_scoped_lock(
        lock_map: dict[int, asyncio.Lock],
        key: int,
    ) -> asyncio.Lock:
        if len(lock_map) > 512:
            stale = [k for k, lock in lock_map.items() if not lock.locked() and k != key]
            for k in stale:
                lock_map.pop(k, None)
        lock = lock_map.get(key)
        if lock is None:
            lock = asyncio.Lock()
            lock_map[key] = lock
        return lock

    def get_applicant_lock(self, user_id: int) -> asyncio.Lock:
        """Return a per-user lock serializing recruitment submit/review operations."""
        return self._get_scoped_lock(self._applicant_locks, user_id)

    def get_ticket_create_lock(self, user_id: int) -> asyncio.Lock:
        """Return a per-user lock preventing concurrent duplicate ticket creation."""
        return self._get_scoped_lock(self._ticket_create_locks, user_id)

    def get_ticket_channel_lock(self, channel_id: int) -> asyncio.Lock:
        """Return a per-channel lock serializing claim/unclaim/close/add operations."""
        return self._get_scoped_lock(self._ticket_channel_locks, channel_id)

    def get_purge_channel_lock(self, channel_id: int) -> asyncio.Lock:
        """Return a per-channel lock serializing purge runs in the same channel."""
        return self._get_scoped_lock(self._purge_channel_locks, channel_id)

    def has_pending_application(self, user_id: int) -> bool:
        """Return True if the user currently has a pending recruitment application."""
        record = self.state["applications"].get(str(user_id))
        return bool(record and record.get("status") == "pending")

    def get_application(self, user_id: int) -> dict[str, Any] | None:
        """Return the stored application record for a user, if present."""
        return self.state["applications"].get(str(user_id))

    def set_application(self, user_id: int, record: dict[str, Any]) -> None:
        """Store or overwrite an application record."""
        self.state["applications"][str(user_id)] = record

    def get_ticket(self, channel_id: int) -> dict[str, Any] | None:
        """Return the stored ticket record for a channel ID, if present."""
        return self.state["tickets"].get(str(channel_id))

    def set_ticket(self, channel_id: int, record: dict[str, Any]) -> None:
        """Store or overwrite a ticket record."""
        self.state["tickets"][str(channel_id)] = record

    def iter_open_tickets_for_user(
        self,
        user_id: int,
        ticket_type: str,
    ) -> list[tuple[int, dict[str, Any]]]:
        """Return all open ticket (channel_id, record) pairs for a user and category."""
        matches: list[tuple[int, dict[str, Any]]] = []
        for channel_id_str, record in self.state["tickets"].items():
            if (
                record.get("owner_id") == user_id
                and record.get("ticket_type") == ticket_type
                and record.get("status") == "open"
            ):
                try:
                    matches.append((int(channel_id_str), record))
                except ValueError:
                    continue
        return matches

    def get_panel_message_id(self, panel_key: str) -> int | None:
        """Return the cached panel message ID for a panel key, if recorded."""
        raw = self.state.get("meta", {}).get("panel_messages", {}).get(panel_key)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def set_panel_message_id(self, panel_key: str, message_id: int) -> None:
        """Persist the Discord message ID for a managed panel."""
        meta = self.state.setdefault("meta", {})
        panels = meta.setdefault("panel_messages", {})
        panels[panel_key] = int(message_id)

    def get_command_sync_hash(self) -> str | None:
        """Return the SHA-256 hash of the last synced slash-command schema."""
        value = self.state.get("meta", {}).get("command_sync_hash")
        return value if isinstance(value, str) and value else None

    def set_command_sync_hash(self, digest: str) -> None:
        """Persist the SHA-256 hash of the last synced slash-command schema."""
        meta = self.state.setdefault("meta", {})
        meta["command_sync_hash"] = digest
