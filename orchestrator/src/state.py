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

Schema:

- ``processed_messages(message_id TEXT PRIMARY KEY, processed_at REAL NOT NULL)``
- ``last_image(sender TEXT PRIMARY KEY, message_id TEXT NOT NULL, path TEXT NOT NULL,
  sha256 TEXT NOT NULL, filename TEXT NOT NULL, saved_at REAL NOT NULL)``
- ``state_log(id INTEGER PRIMARY KEY, phone TEXT, old_flow TEXT, new_flow TEXT,
  old_draft TEXT, new_draft TEXT, at REAL)`` — insert-only audit of every
  state transition (issue #12)

``processed_messages`` is the dedupe boundary. ``last_image`` records the most
recent inbound image per phone, used by #10's caption routing. ``state_log`` is
the append-only audit of every flow transition (added in #12).
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
CREATE TABLE IF NOT EXISTS last_image (
    sender      TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL,
    path        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    filename    TEXT NOT NULL,
    saved_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS state_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phone       TEXT NOT NULL,
    old_flow    TEXT,
    new_flow    TEXT NOT NULL,
    old_draft   TEXT,
    new_draft   TEXT,
    at          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_state_log_phone_at ON state_log(phone, at);
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

    def set_last_image(
        self,
        sender: str,
        message_id: str,
        path: str,
        sha256: str,
        filename: str,
    ) -> None:
        """Record the most recent inbound image for ``sender``.

        Single row per phone (upsert). Used by :mod:`src.image_handler` so a
        follow-up caption can be routed against the right photo. Added in #10.
        """
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO last_image (sender, message_id, path, sha256, filename, saved_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(sender) DO UPDATE SET
                message_id = excluded.message_id,
                path       = excluded.path,
                sha256     = excluded.sha256,
                filename   = excluded.filename,
                saved_at   = excluded.saved_at
            """,
            (sender, message_id, path, sha256, filename, time.time()),
        )

    def get_last_image(self, sender: str) -> dict[str, str] | None:
        """Return the last image metadata for ``sender`` or ``None``."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT message_id, path, sha256, filename, saved_at FROM last_image WHERE sender = ?",
            (sender,),
        ).fetchone()
        if row is None:
            return None
        return {
            "message_id": str(row["message_id"]),
            "path": str(row["path"]),
            "sha256": str(row["sha256"]),
            "filename": str(row["filename"]),
            "saved_at": str(row["saved_at"]),
        }




    # ── state_log (insert-only audit) ───────────────────────────────────
    # Every state transition writes one row. Used by ops to reconstruct
    # the conversation lifecycle for any phone. Append-only — never UPDATE
    # or DELETE. The (phone, at) index supports "show me everything for
    # +65xxx" queries.

    def log_state(
        self,
        phone: str,
        old_flow: str | None,
        new_flow: str,
        old_draft: dict[str, Any] | None = None,
        new_draft: dict[str, Any] | None = None,
    ) -> None:
        """Append a state transition to ``state_log``.

        ``old_flow`` and ``new_flow`` are the ``Flow`` enum string values
        (e.g. ``"idle"``, ``"book_new"``, ``"handoff"``). ``old_draft`` /
        ``new_draft`` are the partial-field collections the flow is
        building up; serialised to JSON for storage. Use ``None`` when
        the draft is empty (typical for non-booking flows).
        """
        import json

        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO state_log (phone, old_flow, new_flow, old_draft, new_draft, at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                phone,
                old_flow,
                new_flow,
                json.dumps(old_draft) if old_draft is not None else None,
                json.dumps(new_draft) if new_draft is not None else None,
                time.time(),
            ),
        )

    def get_state_log(self, phone: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent state transitions for ``phone``.

        Newest first; ``limit`` caps the number of rows. Used by the
        runbook's "show me the last 10 transitions for +65xxx" command
        and by tests.
        """
        import json

        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT id, phone, old_flow, new_flow, old_draft, new_draft, at "
            "FROM state_log WHERE phone = ? ORDER BY at DESC, id DESC LIMIT ?",
            (phone, limit),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            old_draft = row["old_draft"]
            new_draft = row["new_draft"]
            out.append(
                {
                    "id": int(row["id"]),
                    "phone": str(row["phone"]),
                    "old_flow": row["old_flow"],
                    "new_flow": str(row["new_flow"]),
                    "old_draft": json.loads(old_draft) if old_draft is not None else None,
                    "new_draft": json.loads(new_draft) if new_draft is not None else None,
                    "at": float(row["at"]),
                }
            )
        return out

    def latest_flow(self, phone: str) -> str | None:
        """Return the most-recent ``new_flow`` for ``phone`` (or ``None``)."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT new_flow FROM state_log WHERE phone = ? "
            "ORDER BY at DESC, id DESC LIMIT 1",
            (phone,),
        ).fetchone()
        return str(row["new_flow"]) if row is not None else None



@contextlib.contextmanager
def open_state(db_path: Path) -> Iterator[State]:
    state = State(db_path)
    try:
        yield state
    finally:
        state.close()


__all__ = ["SCHEMA", "State", "StateLockedError", "open_state"]
