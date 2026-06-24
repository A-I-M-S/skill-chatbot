"""SQLite state for the orchestrator.

Single-writer SQLite store for v0. Plan risk #6 says: enable WAL via
``PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL`` at boot and forbid
two orchestrator processes against the same DB. We enforce that with an
``fcntl.flock(LOCK_EX | LOCK_NB)`` on a sidecar ``.lock`` file — a second boot
fails fast with :class:`StateLockedError`.

Public surface:

- :class:`State` — opens the DB, holds the flock, exposes typed helpers.
- :func:`open_state` — context manager used by ``main.py``.
- :class:`StateLockedError` — raised when another orchestrator holds the lock.

Schema (v0):

- ``processed_messages(message_id TEXT PRIMARY KEY, processed_at REAL NOT NULL)``

That table is the dedupe boundary for v0 (full idempotency lands in issue #12).
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import logging
import os
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id    TEXT PRIMARY KEY,
    processed_at  REAL NOT NULL
);
"""

CREATE_WAL_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
)


class StateLockedError(RuntimeError):
    """Raised when another orchestrator process already holds the DB flock."""


class State:
    """Thin SQLite handle for the orchestrator's v0 state.

    Holds an ``fcntl.flock`` on ``<db>.lock`` for the lifetime of the object —
    so two ``State`` instances on the same DB file can't co-exist.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._db_path.with_suffix(self._db_path.suffix + ".lock")
        self._lock_path.touch(exist_ok=True)
        self._lock_fd: int | None = None
        self._conn: sqlite3.Connection | None = None
        self._open()

    def _open(self) -> None:
        lock_fd = os.open(str(self._lock_path), os.O_WRONLY | os.O_CREAT, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(lock_fd)
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise StateLockedError(
                    f"another orchestrator process holds {self._lock_path}"
                ) from exc
            raise
        self._lock_fd = lock_fd

        conn = sqlite3.connect(str(self._db_path), isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        for pragma in CREATE_WAL_PRAGMAS:
            conn.execute(pragma)
        conn.executescript(SCHEMA)
        self._conn = conn
        logger.info("state db opened: %s (wal=%s)", self._db_path, self._wal_mode())

    def _wal_mode(self) -> str:
        assert self._conn is not None
        return str(self._conn.execute("PRAGMA journal_mode").fetchone()[0])

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = None

    def __enter__(self) -> State:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def is_processed(self, message_id: str) -> bool:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    def mark_processed(self, message_id: str) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT OR IGNORE INTO processed_messages(message_id, processed_at) VALUES (?, ?)",
            (message_id, time.time()),
        )

    def last_processed_message_id(self) -> str | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT message_id FROM processed_messages ORDER BY processed_at DESC, message_id DESC LIMIT 1"
        ).fetchone()
        return str(row["message_id"]) if row is not None else None

    def health(self) -> str:
        assert self._conn is not None
        self._conn.execute("SELECT 1").fetchone()
        return "ok"


@contextlib.contextmanager
def open_state(db_path: Path) -> Iterator[State]:
    state = State(db_path)
    try:
        yield state
    finally:
        state.close()


__all__ = ["SCHEMA", "State", "StateLockedError", "open_state"]
