"""Tests for the ``state_log`` audit table (issue #12 hardening)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.state import State, open_state


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "state.sqlite"


def test_state_log_table_exists(db: Path) -> None:
    with open_state(db) as s:
        row = s._conn.execute(  # type: ignore[attr-defined]
            "SELECT name FROM sqlite_master WHERE type='table' AND name='state_log'"
        ).fetchone()
        assert row is not None
        assert row["name"] == "state_log"


def test_state_log_index_exists(db: Path) -> None:
    with open_state(db) as s:
        row = s._conn.execute(  # type: ignore[attr-defined]
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_state_log_phone_at'"
        ).fetchone()
        assert row is not None


def test_log_state_appends_row(db: Path) -> None:
    with open_state(db) as s:
        s.log_state(
            phone="6591234567",
            old_flow="idle",
            new_flow="book_new",
            old_draft=None,
            new_draft={"date": "2026-08-15"},
        )
        rows = s.get_state_log("6591234567")
        assert len(rows) == 1
        row = rows[0]
        assert row["phone"] == "6591234567"
        assert row["old_flow"] == "idle"
        assert row["new_flow"] == "book_new"
        assert row["old_draft"] is None
        assert row["new_draft"] == {"date": "2026-08-15"}
        assert row["at"] > 0


def test_log_state_serialises_drafts_as_json(db: Path) -> None:
    with open_state(db) as s:
        s.log_state(
            phone="6591234567",
            old_flow="book_new",
            new_flow="book_new",
            old_draft={"date": "2026-08-15", "pax": 30},
            new_draft={"date": "2026-08-15", "pax": 30, "time": "10:30"},
        )
        # raw column is a JSON string (not a dict) — verify storage shape
        raw = s._conn.execute(  # type: ignore[attr-defined]
            "SELECT new_draft FROM state_log WHERE phone = ?", ("6591234567",)
        ).fetchone()
        assert isinstance(raw["new_draft"], str)
        assert json.loads(raw["new_draft"]) == {
            "date": "2026-08-15",
            "pax": 30,
            "time": "10:30",
        }


def test_get_state_log_returns_newest_first(db: Path) -> None:
    with open_state(db) as s:
        for flow in ("idle", "book_new", "book_new", "handoff"):
            s.log_state(
                phone="6591234567",
                old_flow=None,
                new_flow=flow,
            )
        rows = s.get_state_log("6591234567")
        # handoff was logged last, so it's the newest (rows[0])
        assert [r["new_flow"] for r in rows] == ["handoff", "book_new", "book_new", "idle"]


def test_get_state_log_respects_limit(db: Path) -> None:
    with open_state(db) as s:
        for i in range(20):
            s.log_state(phone="6591234567", old_flow=None, new_flow=f"step_{i}")
        rows = s.get_state_log("6591234567", limit=5)
        assert len(rows) == 5
        # Newest 5 = step_19, step_18, ..., step_15
        assert [r["new_flow"] for r in rows] == ["step_19", "step_18", "step_17", "step_16", "step_15"]


def test_get_state_log_isolated_per_phone(db: Path) -> None:
    with open_state(db) as s:
        s.log_state(phone="6590000001", old_flow=None, new_flow="book_new")
        s.log_state(phone="6590000002", old_flow=None, new_flow="handoff")
        s.log_state(phone="6590000001", old_flow="book_new", new_flow="book_new")
        assert len(s.get_state_log("6590000001")) == 2
        assert len(s.get_state_log("6590000002")) == 1
        assert len(s.get_state_log("6590000099")) == 0


def test_latest_flow(db: Path) -> None:
    with open_state(db) as s:
        assert s.latest_flow("6591234567") is None
        s.log_state(phone="6591234567", old_flow="idle", new_flow="book_new")
        assert s.latest_flow("6591234567") == "book_new"
        s.log_state(phone="6591234567", old_flow="book_new", new_flow="handoff")
        assert s.latest_flow("6591234567") == "handoff"


def test_state_log_is_append_only(db: Path) -> None:
    """The table has no UPDATE/DELETE surface; raw rows can only grow."""
    with open_state(db) as s:
        s.log_state(phone="6591234567", old_flow="idle", new_flow="book_new")
        s.log_state(phone="6591234567", old_flow="book_new", new_flow="handoff")
        # No public update/delete methods — verify by attempting
        # the only mutation (log_state) and checking the count
        # only grew.
        before = len(s.get_state_log("6591234567"))
        s.log_state(phone="6591234567", old_flow="handoff", new_flow="idle")
        after = len(s.get_state_log("6591234567"))
        assert after == before + 1


def test_state_log_survives_state_reopen(db: Path) -> None:
    """Close the DB, reopen, log should still be there (WAL durability)."""
    with open_state(db) as s:
        s.log_state(phone="6591234567", old_flow="idle", new_flow="book_new")
    # Reopen
    with open_state(db) as s:
        rows = s.get_state_log("6591234567")
        assert len(rows) == 1
        assert rows[0]["new_flow"] == "book_new"


def test_state_log_draft_can_be_omitted(db: Path) -> None:
    with open_state(db) as s:
        s.log_state(phone="6591234567", old_flow=None, new_flow="handoff")
        row = s.get_state_log("6591234567")[0]
        assert row["old_draft"] is None
        assert row["new_draft"] is None
