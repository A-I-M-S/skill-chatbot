"""Smoke test fixtures and assertions (issue #14)."""

from __future__ import annotations

import json
from pathlib import Path

from src import settings as settings_mod


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "smoke_inbox.ndjson"


def test_fixture_exists() -> None:
    assert FIXTURE_PATH.exists(), f"smoke fixture missing at {FIXTURE_PATH}"


def test_fixture_lines_are_valid_ndjson() -> None:
    lines = FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 10, f"smoke fixture should have >=10 lines, got {len(lines)}"
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        obj = json.loads(line)
        for field in ("message_id", "from", "text", "image", "timestamp"):
            assert field in obj, f"line {i}: missing field {field!r}"


def test_fixture_covers_en_and_zh() -> None:
    """At least one EN line and one 中文 line."""
    text_blob = FIXTURE_PATH.read_text(encoding="utf-8")
    assert "几点开门" in text_blob, "no Chinese sample in fixture"
    assert "What time do you open" in text_blob, "no English sample in fixture"


def test_fixture_covers_all_flows() -> None:
    """faq (smoke-1), book_new multi-turn (smoke-2..7), handoff
    (smoke-8, smoke-15), zh happy path (smoke-9..14)."""
    text_blob = FIXTURE_PATH.read_text(encoding="utf-8")
    assert "refund" in text_blob or "退款" in text_blob, "no handoff sample"
    assert "book" in text_blob.lower() or "预约" in text_blob, "no booking sample"


def test_settings_from_env_defaults() -> None:
    """Settings can be built with no env (defaults); sanity check."""
    import os
    for k in list(os.environ):
        if k.startswith(("WA_BRIDGE_", "WA_NOTIFY", "ADMIN_", "QDRANT_", "INFERENCE_", "ORCHESTRATOR_", "INBOX_", "BOOKING_")):
            del os.environ[k]
    s = settings_mod.Settings.from_mapping({})
    assert s.wa_bridge_url
    assert s.inbox_path
    assert s.orchestrator_port