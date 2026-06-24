"""Tests for the admin-notify helpers (issue #8).

Covers:
- notify_handoff with multiple admins, partial failures, empty WA_NOTIFY
- notify_new_booking (EN + 中文) — message shape, sender/event_id/summary
- Bilingual labelling
- Failure swallowing (admin DM failure does not raise)
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from src import notify


@pytest.fixture
def bridge_mock() -> respx.MockRouter:
    with respx.mock(assert_all_called=False) as router:
        yield router


# ──────────────────────────────────────────────────────────────────────
# WA_NOTIFY parsing
# ──────────────────────────────────────────────────────────────────────


def test_split_admins_handles_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_NOTIFY", "")
    assert notify._split_admins() == []


def test_split_admins_strips_and_normalises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_NOTIFY", " 6591234567 , 6592345678, +6593456789, junk")
    admins = notify._split_admins()
    assert "+6591234567" in admins
    assert "+6592345678" in admins
    assert "+6593456789" in admins
    assert len(admins) == 3


# ──────────────────────────────────────────────────────────────────────
# notify_handoff
# ──────────────────────────────────────────────────────────────────────


def test_notify_handoff_zero_admins(bridge_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_NOTIFY", "")
    sent = notify.notify_handoff(sender="6591234567", reason="refund", summary="I want a refund")
    assert sent == 0
    assert not bridge_mock.calls


def test_notify_handoff_sends_to_each_admin(
    bridge_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WA_NOTIFY", "6591234567,6592345678")
    route = bridge_mock.post("http://127.0.0.1:7788/send").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    sent = notify.notify_handoff(sender="6591234567", reason="refund", summary="refund pls")
    assert sent == 2
    assert route.call_count == 2
    # Bodies should mention sender, reason, summary
    bodies = [json.loads(c.request.content)["text"] for c in route.calls] if False else [
        c.request.content.decode("utf-8") for c in route.calls
    ]
    import json as _json
    decoded = [_json.loads(b) for b in bodies]
    for d in decoded:
        assert "6591234567" in d["text"]
        assert "refund" in d["text"]


def test_notify_handoff_partial_failure(
    bridge_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WA_NOTIFY", "6591234567,6592345678")
    route = bridge_mock.post("http://127.0.0.1:7788/send").mock(
        side_effect=[
            httpx.Response(200, json={"ok": True}),
            httpx.ConnectError("network"),
        ]
    )
    sent = notify.notify_handoff(sender="6591234567", reason="refund", summary="x")
    assert sent == 1
    assert route.call_count == 2


def test_notify_handoff_fallback_tag(
    bridge_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WA_NOTIFY", "6591234567")
    bridge_mock.post("http://127.0.0.1:7788/send").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    notify.notify_handoff(sender="6591234567", reason="other", summary="x", is_fallback=True)
    sent = bridge_mock.calls[0].request.content.decode("utf-8")
    import json as _json
    body = _json.loads(sent)["text"]
    assert "[FALLBACK]" in body


# ──────────────────────────────────────────────────────────────────────
# notify_new_booking
# ──────────────────────────────────────────────────────────────────────


def test_notify_new_booking_en_shape(
    bridge_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WA_NOTIFY", "6591234567")
    route = bridge_mock.post("http://127.0.0.1:7788/send").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    sent = notify.notify_new_booking(
        sender="6591234567",
        event_id="EVT-ABC-123",
        summary={
            "date": "2026-08-15",
            "time": "10:30",
            "pax": 30,
            "contact_name": "Jane Doe",
            "contact_email": "jane@school.cn",
            "contact_phone": "+6591234567",
            "org": "Acme Primary",
        },
        language="en",
    )
    assert sent == 1
    body = route.calls[0].request.content.decode("utf-8")
    import json as _json
    parsed = _json.loads(body)["text"]
    assert "🆕" in parsed
    assert "Jane Doe" in parsed
    assert "jane@school.cn" in parsed
    assert "6591234567" in parsed
    assert "Acme Primary" in parsed
    assert "EVT-ABC-123" in parsed
    assert "2026-08-15" in parsed
    assert "30" in parsed


def test_notify_new_booking_zh_shape(
    bridge_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WA_NOTIFY", "6591234567")
    route = bridge_mock.post("http://127.0.0.1:7788/send").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    notify.notify_new_booking(
        sender="6591234567",
        event_id="EVT-XYZ",
        summary={
            "date": "2026-08-15",
            "time": "10:30",
            "pax": 30,
            "contact_name": "张老师",
        },
        language="zh",
    )
    parsed = __import__("json").loads(route.calls[0].request.content.decode("utf-8"))["text"]
    assert "新预约" in parsed
    assert "编号" in parsed
    assert "时间" in parsed
    assert "人数" in parsed
    assert "联系人" in parsed
    assert "张老师" in parsed


def test_notify_new_booking_failure_swallowed(
    bridge_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WA_NOTIFY", "6591234567")
    bridge_mock.post("http://127.0.0.1:7788/send").mock(
        return_value=httpx.Response(500, json={"error": "internal"})
    )
    sent = notify.notify_new_booking(
        sender="6591234567", event_id="EVT-1", summary={"date": "2026-08-15"}, language="en"
    )
    assert sent == 0  # 500 → not counted as success


def test_notify_new_booking_empty_wa_notify(
    bridge_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WA_NOTIFY", "")
    sent = notify.notify_new_booking(
        sender="6591234567", event_id="EVT-1", summary={"date": "2026-08-15"}, language="en"
    )
    assert sent == 0
    assert not bridge_mock.calls