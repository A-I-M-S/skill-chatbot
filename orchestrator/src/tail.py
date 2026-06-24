"""NDJSON tailer for the wa-bridge inbox.

Contract: each line in ``INBOX_PATH`` is a JSON object with at least::

    {"message_id": "...", "from": "<digits-only-phone>", "text": "...",
     "image": null | {...}, "timestamp": "..."}

(See ``main.py`` for the ``v1`` contract note.) Robust to:

- partial last line (``pygtail`` keeps reading once a newline arrives);
- lines that don't parse (logged at WARN, skipped — plan risk #10);
- log rotation / inode changes (``pygtail`` handles via its offset file).

We persist the byte offset via ``pygtail``'s ``offset_file`` (next to the state DB
so a ``make clean`` picks it up). Per-message dedupe is the SQLite table in
``state.py``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pygtail import Pygtail

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("message_id", "from", "text")


class InboxMessage(dict):
    """Typed view over a parsed NDJSON line. Subclass of ``dict`` for
    JSON-serialisation compatibility with the bridge ``/send`` contract.
    """

    @property
    def message_id(self) -> str:
        return str(self["message_id"])

    @property
    def sender(self) -> str:
        return str(self["from"])

    @property
    def text(self) -> str:
        return str(self.get("text") or "")

    @property
    def image(self) -> dict[str, str] | None:
        """Image metadata attached to the message (issue #10).

        Returns ``None`` if the inbound message had no image (text-only or
        an oversize image dropped by the bridge). The shape is::

            {"path": str, "sha256": str, "filename": str}
        """
        raw = self.get("image")
        if not isinstance(raw, dict):
            return None
        try:
            path = str(raw["path"])
            sha256 = str(raw["sha256"])
            filename = str(raw["filename"])
        except KeyError:
            return None
        if not path or not sha256:
            return None
        return {"path": path, "sha256": sha256, "filename": filename}

    @property
    def has_image(self) -> bool:
        return self.image is not None


class Tailer:
    """Tails the wa-bridge inbox NDJSON, yielding parsed lines.

    The tailer's own byte offset is stored in ``offset_file`` (defaults to
    ``<state_db>.offset``). The dedupe key (``message_id``) is enforced by the
    caller via :class:`src.state.State`.
    """

    def __init__(self, inbox_path: Path, offset_file: Path, poll_interval: float = 0.5) -> None:
        self._inbox_path = Path(inbox_path)
        self._offset_file = Path(offset_file)
        self._offset_file.parent.mkdir(parents=True, exist_ok=True)
        self._poll_interval = poll_interval
        self._pygtail = Pygtail(
            str(self._inbox_path),
            offset_file=str(self._offset_file),
            paranoid=False,
            copytruncate=False,
            read_from_end=False,
            every_n=10,
            save_on_end=True,
        )

    @property
    def offset_file(self) -> Path:
        return self._offset_file

    def __iter__(self) -> Iterator[InboxMessage]:
        return self.iter_lines()

    def iter_lines(self) -> Iterator[InboxMessage]:
        """Yield parsed, valid messages until the inbox is exhausted.

        Lines that don't parse are logged at WARN and skipped. The tailer
        stops at EOF; the caller is expected to call ``iter_lines`` in a loop
        (with a small sleep) to keep polling.
        """
        try:
            for raw in self._pygtail:
                msg = self._safe_parse(raw)
                if msg is not None:
                    yield msg
        except StopIteration:
            return
        finally:
            self._pygtail.update_offset_file()

    def update_offset(self) -> None:
        """Force-flush the offset file (used in tests / on shutdown)."""
        self._pygtail.update_offset_file()

    @staticmethod
    def _safe_parse(raw: str) -> InboxMessage | None:
        line = raw.rstrip("\n")
        if not line.strip():
            return None
        try:
            data: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.warning("skipping unparseable ndjson line: %s (err=%s)", line[:120], exc)
            return None
        if not isinstance(data, dict):
            logger.warning("skipping ndjson line: not a JSON object (got %s)", type(data).__name__)
            return None
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            logger.warning(
                "skipping ndjson line: missing fields %s (got keys %s)", missing, list(data)
            )
            return None
        return InboxMessage(data)


__all__ = ["REQUIRED_FIELDS", "InboxMessage", "Tailer"]
