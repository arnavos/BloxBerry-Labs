"""Tests for async StateStore persistence, legacy compatibility, and locks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from bot.config import MAIN_ROLES
from bot.database import StateStore


def test_loads_legacy_state_file_and_corrupt_recovery(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(
        json.dumps({"applications": {"1": {"status": "pending"}}, "tickets": {}}),
        encoding="utf-8",
    )
    store = StateStore(legacy_path, MAIN_ROLES.keys())
    assert store.has_pending_application(1) is True
    assert store.state["meta"]["panel_messages"] == {}

    corrupt_path = tmp_path / "corrupt.json"
    corrupt_path.write_text("{broken json", encoding="utf-8")
    recovered = StateStore(corrupt_path, MAIN_ROLES.keys())
    assert recovered.state["applications"] == {}
    assert recovered.state["tickets"] == {}


async def test_async_save_creates_parent_dirs_and_leaves_no_tmp(tmp_path: Path) -> None:
    nested_file = tmp_path / "nested" / "disk" / "bloxberry_data.json"
    store = StateStore(nested_file, MAIN_ROLES.keys())
    store.set_application(42, {"status": "pending"})
    await store.save()

    assert nested_file.exists()
    assert not Path(f"{nested_file}.tmp").exists()

    reloaded = StateStore(nested_file, MAIN_ROLES.keys())
    assert reloaded.has_pending_application(42) is True


async def test_concurrent_saves_and_open_ticket_iteration(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json", MAIN_ROLES.keys())
    store.set_ticket(100, {"owner_id": 7, "ticket_type": "Technical Support", "status": "open"})
    store.set_ticket(101, {"owner_id": 7, "ticket_type": "Technical Support", "status": "closed"})
    store.set_ticket(102, {"owner_id": 7, "ticket_type": "Technical Support", "status": "open"})

    matches = store.iter_open_tickets_for_user(7, "Technical Support")
    assert [cid for cid, _ in matches] == [100, 102]

    await asyncio.gather(*(store.save() for _ in range(10)))
    loaded = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert set(loaded["tickets"].keys()) == {"100", "101", "102"}
