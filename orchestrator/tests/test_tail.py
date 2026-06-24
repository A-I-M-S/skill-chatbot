"""Tests for the NDJSON tailer (advance + skip + robust parsing)."""

from __future__ import annotations

import json
from pathlib import Path

from src.tail import Tailer


def _append(path: Path, line: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def test_tailer_advances_on_new_line(tmp_inbox: Path, tmp_state_db: Path) -> None:
    _append(tmp_inbox, {"message_id": "m1", "from": "6512345678", "text": "hi", "timestamp": "t1"})
    tailer = Tailer(tmp_inbox, tmp_state_db.with_suffix(".offset"))
    msgs = list(tailer.iter_lines())
    assert [m.message_id for m in msgs] == ["m1"]
    assert msgs[0].sender == "6512345678"
    assert msgs[0].text == "hi"
    tailer.update_offset()


def test_tailer_skips_unparseable_line(tmp_inbox: Path, tmp_state_db: Path) -> None:
    with tmp_inbox.open("a", encoding="utf-8") as fh:
        fh.write("not-json-at-all\n")
        fh.write(json.dumps({"message_id": "m1", "from": "6512345678", "text": "hi"}) + "\n")
        fh.write("{bad-json\n")
    tailer = Tailer(tmp_inbox, tmp_state_db.with_suffix(".offset"))
    msgs = list(tailer.iter_lines())
    assert [m.message_id for m in msgs] == ["m1"]


def test_tailer_skips_lines_missing_required_fields(tmp_inbox: Path, tmp_state_db: Path) -> None:
    _append(tmp_inbox, {"from": "6512345678", "text": "no id"})
    _append(tmp_inbox, {"message_id": "m1", "from": "6512345678", "text": "ok"})
    tailer = Tailer(tmp_inbox, tmp_state_db.with_suffix(".offset"))
    msgs = list(tailer.iter_lines())
    assert [m.message_id for m in msgs] == ["m1"]


def test_tailer_offset_advances_after_consume(tmp_inbox: Path, tmp_state_db: Path) -> None:
    _append(tmp_inbox, {"message_id": "m1", "from": "1", "text": "x"})
    tailer = Tailer(tmp_inbox, tmp_state_db.with_suffix(".offset"))
    assert list(tailer.iter_lines())  # consumes the line
    tailer.update_offset()
    offset_path = tmp_state_db.with_suffix(".offset")
    assert offset_path.exists()
    assert offset_path.read_text().strip() != ""


def test_tailer_resumes_after_restart(tmp_inbox: Path, tmp_state_db: Path) -> None:
    """Restart scenario: lines written before first run should not be
    re-delivered once the offset file is in place."""
    _append(tmp_inbox, {"message_id": "m1", "from": "1", "text": "first"})
    offset_path = tmp_state_db.with_suffix(".offset")
    tailer = Tailer(tmp_inbox, offset_path)
    assert [m.message_id for m in tailer.iter_lines()] == ["m1"]
    tailer.update_offset()

    _append(tmp_inbox, {"message_id": "m2", "from": "1", "text": "second"})

    tailer2 = Tailer(tmp_inbox, offset_path)
    assert [m.message_id for m in tailer2.iter_lines()] == ["m2"]


def test_tailer_ignores_blank_lines(tmp_inbox: Path, tmp_state_db: Path) -> None:
    with tmp_inbox.open("a", encoding="utf-8") as fh:
        fh.write("\n")
        fh.write("   \n")
        fh.write(json.dumps({"message_id": "m1", "from": "1", "text": "x"}) + "\n")
    tailer = Tailer(tmp_inbox, tmp_state_db.with_suffix(".offset"))
    assert [m.message_id for m in tailer.iter_lines()] == ["m1"]
