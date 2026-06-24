"""Tests for the SQLite state handle (WAL, flock, processed_messages)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from src.state import State, StateLockedError, open_state


def test_state_creates_schema(tmp_state_db: Path) -> None:
    with open_state(tmp_state_db):
        with sqlite3.connect(str(tmp_state_db)) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "processed_messages" in tables


def test_state_enables_wal(tmp_state_db: Path) -> None:
    with open_state(tmp_state_db):
        with sqlite3.connect(str(tmp_state_db)) as c:
            mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


def test_state_marks_and_queries_processed(tmp_state_db: Path) -> None:
    with open_state(tmp_state_db) as s:
        assert s.is_processed("m1") is False
        s.mark_processed("m1")
        assert s.is_processed("m1") is True
        assert s.last_processed_message_id() == "m1"


def test_state_mark_processed_is_idempotent(tmp_state_db: Path) -> None:
    with open_state(tmp_state_db) as s:
        s.mark_processed("m1")
        s.mark_processed("m1")
        assert s.last_processed_message_id() == "m1"


def test_state_last_processed_returns_most_recent(tmp_state_db: Path) -> None:
    with open_state(tmp_state_db) as s:
        s.mark_processed("m1")
        time.sleep(0.01)
        s.mark_processed("m2")
        assert s.last_processed_message_id() == "m2"


def test_state_flock_blocks_second_instance(tmp_state_db: Path) -> None:
    with open_state(tmp_state_db), pytest.raises(StateLockedError):
        State(tmp_state_db)


def test_state_close_releases_lock(tmp_state_db: Path) -> None:
    with State(tmp_state_db):
        pass
    with State(tmp_state_db):
        pass
