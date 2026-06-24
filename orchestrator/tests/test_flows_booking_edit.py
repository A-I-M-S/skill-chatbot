"""Tests for the book_edit + book_cancel flows (issue #6)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from src.booking_subprocess import BookingSubprocessError
from src.flows import booking_edit
from src.state import State


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def state(tmp_path: Path) -> State:
    s = State(tmp_path / "state.sqlite")
    try:
        yield s
    finally:
        s.close()


def _ev(eid: str = "EVT-1", start: str | None = None, pax: int = 30, body: str = "", phone: str = "+6591234567") -> dict[str, Any]:
    return {
        "id": eid,
        "event_id": eid,
        "start": start or (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%dT10:30:00"),
        "pax": pax,
        "subject": f"Farm tour {pax} pax",
        "body": body or f"Booked via BlueAcres / SAAC FARM bot\nContact: Jane Doe <jane@x.com> {phone}",
        "attendees": [{"email": "jane@x.com", "name": "Jane Doe", "phone": phone, "type": "required"}],
    }


@pytest.fixture
def one_event(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    ev = _ev()
    monkeypatch.setattr(booking_edit, "list_events", lambda *a, **k: [ev])
    return [ev]


@pytest.fixture
def two_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    ev1 = _ev(eid="EVT-1")
    ev2 = _ev(eid="EVT-2", start=(datetime.now() + timedelta(days=21)).strftime("%Y-%m-%dT14:30:00"))
    monkeypatch.setattr(booking_edit, "list_events", lambda *a, **k: [ev1, ev2])
    return [ev1, ev2]


@pytest.fixture
def no_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(booking_edit, "list_events", lambda *a, **k: [])


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def test_event_matches_phone_digits_only() -> None:
    ev = _ev(body="Contact: Jane Doe <jane@x.com> +6591234567")
    assert booking_edit._event_matches_phone(ev, "+6591234567") is True
    assert booking_edit._event_matches_phone(ev, "6591234567") is True
    assert booking_edit._event_matches_phone(ev, "6599999999") is False
    # Attendees phone
    ev2 = _ev(phone="+6591234567")
    assert booking_edit._event_matches_phone(ev2, "+6591234567") is True


def test_event_matches_phone_empty_returns_false() -> None:
    assert booking_edit._event_matches_phone(_ev(), "") is False
    assert booking_edit._event_matches_phone(_ev(), None or "") is False


def test_event_id_returns_either_field() -> None:
    assert booking_edit._event_id({"id": "A"}) == "A"
    assert booking_edit._event_id({"event_id": "B"}) == "B"
    assert booking_edit._event_id({"subject": "C"}) is None


def test_summarize_event_short() -> None:
    s = booking_edit._summarize_event(_ev(), "en")
    assert "30 pax" in s
    s_zh = booking_edit._summarize_event(_ev(), "zh")
    assert "30 位" in s_zh


# ──────────────────────────────────────────────────────────────────────
# book_edit — one event
# ──────────────────────────────────────────────────────────────────────


def test_edit_no_events_offers_handoff(state: State, no_events: None) -> None:
    reply = booking_edit.handle_edit(
        phone="6591234567", user_text="", tool_args={}, state=state, language="en"
    )
    assert "no upcoming" in reply.lower() or "team" in reply.lower()


def test_edit_one_event_with_change_prompts_confirm(
    state: State, one_event: list[dict[str, Any]]
) -> None:
    reply = booking_edit.handle_edit(
        phone="6591234567",
        user_text="",
        tool_args={"pax": 35},
        state=state,
        language="en",
    )
    assert "YES" in reply
    ps = state.get_phone_state("6591234567")
    assert ps["flow"] == "book_edit"
    assert ps["pending_confirm"]["stage"] == "awaiting"
    assert ps["draft"]["picked_event_id"] == "EVT-1"
    assert ps["draft"]["pax"] == 35


def test_edit_one_event_no_change_asks_field(
    state: State, one_event: list[dict[str, Any]]
) -> None:
    reply = booking_edit.handle_edit(
        phone="6591234567",
        user_text="",
        tool_args={},
        state=state,
        language="en",
    )
    assert "date" in reply.lower() or "time" in reply.lower() or "pax" in reply.lower()
    ps = state.get_phone_state("6591234567")
    # Not in awaiting_confirm yet — still collecting which field to change
    assert ps["pending_confirm"] is None


def test_edit_yes_commits(state: State, monkeypatch: pytest.MonkeyPatch, one_event: list[dict[str, Any]]) -> None:
    state.set_phone_state(
        "6591234567", "book_edit",
        draft={"picked_event_id": "EVT-1", "pax": 35, "summary": "old"},
        pending_confirm={"stage": "awaiting", "kind": "edit"},
        language="en",
    )
    captured: dict[str, Any] = {}
    monkeypatch.setattr(booking_edit, "edit", lambda eid, **kw: (captured.update(eid=eid, **kw), {"error": None, "reply": "Updated."})[1])
    reply = booking_edit.handle_edit(
        phone="6591234567", user_text="YES", tool_args={}, state=state, language="en", confirm_reply="YES"
    )
    assert "Updated" in reply or "Updated." in reply
    assert captured["eid"] == "EVT-1"
    assert captured["pax"] == 35
    assert state.get_phone_state("6591234567")["flow"] == "idle"


def test_edit_no_aborts(state: State, one_event: list[dict[str, Any]]) -> None:
    state.set_phone_state(
        "6591234567", "book_edit",
        draft={"picked_event_id": "EVT-1", "pax": 35},
        pending_confirm={"stage": "awaiting", "kind": "edit"},
        language="en",
    )
    reply = booking_edit.handle_edit(
        phone="6591234567", user_text="nope", tool_args={}, state=state, language="en", confirm_reply="nope"
    )
    assert "OK" in reply or "no problem" in reply
    ps = state.get_phone_state("6591234567")
    assert ps["flow"] == "book_edit"  # draft preserved


def test_edit_commit_error_falls_back_to_handoff(
    state: State, monkeypatch: pytest.MonkeyPatch, one_event: list[dict[str, Any]]
) -> None:
    state.set_phone_state(
        "6591234567", "book_edit",
        draft={"picked_event_id": "EVT-1", "pax": 35},
        pending_confirm={"stage": "awaiting", "kind": "edit"},
        language="en",
    )
    monkeypatch.setattr(booking_edit, "edit", lambda *a, **k: (_ for _ in ()).throw(BookingSubprocessError("boom")))
    monkeypatch.setenv("ADMIN_CONTACT_NUMBER", "+6591234567")
    reply = booking_edit.handle_edit(
        phone="6591234567", user_text="YES", tool_args={}, state=state, language="en", confirm_reply="YES"
    )
    assert "team" in reply.lower() or "contact" in reply.lower()


# ──────────────────────────────────────────────────────────────────────
# book_edit — multiple events
# ──────────────────────────────────────────────────────────────────────


def test_edit_multiple_events_shows_numbered_list(
    state: State, two_events: list[dict[str, Any]]
) -> None:
    reply = booking_edit.handle_edit(
        phone="6591234567", user_text="", tool_args={}, state=state, language="en"
    )
    assert "1." in reply and "2." in reply
    ps = state.get_phone_state("6591234567")
    assert ps["pending_confirm"]["stage"] == "awaiting_pick"


def test_edit_pick_event_by_number(
    state: State, two_events: list[dict[str, Any]]
) -> None:
    # Trigger the multi-event prompt
    booking_edit.handle_edit(
        phone="6591234567", user_text="", tool_args={}, state=state, language="en"
    )
    # Reply with "2" — pick the second event
    reply = booking_edit.handle_edit(
        phone="6591234567", user_text="2", tool_args={}, state=state, language="en", confirm_reply="2"
    )
    ps = state.get_phone_state("6591234567")
    assert ps["draft"]["picked_event_id"] == "EVT-2"
    assert "date" in reply.lower() or "time" in reply.lower() or "pax" in reply.lower()


def test_edit_pick_event_invalid_number_re_prompts(
    state: State, two_events: list[dict[str, Any]]
) -> None:
    booking_edit.handle_edit(
        phone="6591234567", user_text="", tool_args={}, state=state, language="en"
    )
    reply = booking_edit.handle_edit(
        phone="6591234567", user_text="99", tool_args={}, state=state, language="en", confirm_reply="99"
    )
    assert "1." in reply and "2." in reply


# ──────────────────────────────────────────────────────────────────────
# book_cancel
# ──────────────────────────────────────────────────────────────────────


def test_cancel_no_events_offer_handoff(state: State, no_events: None) -> None:
    reply = booking_edit.handle_cancel(
        phone="6591234567", user_text="", tool_args={}, state=state, language="en"
    )
    assert "no upcoming" in reply.lower() or "team" in reply.lower()


def test_cancel_one_event_prompts_confirm(
    state: State, one_event: list[dict[str, Any]]
) -> None:
    reply = booking_edit.handle_cancel(
        phone="6591234567", user_text="", tool_args={}, state=state, language="en"
    )
    assert "irreversible" in reply.lower() or "YES" in reply
    ps = state.get_phone_state("6591234567")
    assert ps["flow"] == "book_cancel"
    assert ps["pending_confirm"]["stage"] == "awaiting"


def test_cancel_yes_commits(state: State, monkeypatch: pytest.MonkeyPatch, one_event: list[dict[str, Any]]) -> None:
    state.set_phone_state(
        "6591234567", "book_cancel",
        draft={"picked_event_id": "EVT-1"},
        pending_confirm={"stage": "awaiting"},
        language="en",
    )
    captured: list[str] = []
    monkeypatch.setattr(booking_edit, "cancel", lambda eid: (captured.append(eid), {"error": None, "reply": "Cancelled."})[1])
    reply = booking_edit.handle_cancel(
        phone="6591234567", user_text="YES", tool_args={}, state=state, language="en", confirm_reply="YES"
    )
    assert "Cancelled" in reply
    assert captured == ["EVT-1"]
    assert state.get_phone_state("6591234567")["flow"] == "idle"


def test_cancel_chinese_yes_commits(
    state: State, monkeypatch: pytest.MonkeyPatch, one_event: list[dict[str, Any]]
) -> None:
    state.set_phone_state(
        "6591234567", "book_cancel",
        draft={"picked_event_id": "EVT-1"},
        pending_confirm={"stage": "awaiting"},
        language="zh",
    )
    monkeypatch.setattr(booking_edit, "cancel", lambda eid: {"error": None, "reply": "已取消。"})
    reply = booking_edit.handle_cancel(
        phone="6591234567", user_text="是", tool_args={}, state=state, language="zh", confirm_reply="是"
    )
    assert "已取消" in reply


def test_cancel_commit_error_falls_back_to_handoff(
    state: State, monkeypatch: pytest.MonkeyPatch, one_event: list[dict[str, Any]]
) -> None:
    state.set_phone_state(
        "6591234567", "book_cancel",
        draft={"picked_event_id": "EVT-1"},
        pending_confirm={"stage": "awaiting"},
        language="en",
    )
    monkeypatch.setattr(booking_edit, "cancel", lambda eid: (_ for _ in ()).throw(BookingSubprocessError("nope")))
    monkeypatch.setenv("ADMIN_CONTACT_NUMBER", "+6591234567")
    reply = booking_edit.handle_cancel(
        phone="6591234567", user_text="YES", tool_args={}, state=state, language="en", confirm_reply="YES"
    )
    assert "team" in reply.lower() or "contact" in reply.lower()


def test_cancel_multiple_events_shows_numbered_list(
    state: State, two_events: list[dict[str, Any]]
) -> None:
    reply = booking_edit.handle_cancel(
        phone="6591234567", user_text="", tool_args={}, state=state, language="en"
    )
    assert "1." in reply and "2." in reply
    ps = state.get_phone_state("6591234567")
    assert ps["pending_confirm"]["stage"] == "awaiting_pick"


def test_cancel_pick_by_number(
    state: State, two_events: list[dict[str, Any]]
) -> None:
    booking_edit.handle_cancel(
        phone="6591234567", user_text="", tool_args={}, state=state, language="en"
    )
    reply = booking_edit.handle_cancel(
        phone="6591234567", user_text="1", tool_args={}, state=state, language="en", confirm_reply="1"
    )
    ps = state.get_phone_state("6591234567")
    assert ps["draft"]["picked_event_id"] == "EVT-1"
    assert "irreversible" in reply.lower() or "YES" in reply


def test_list_events_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        booking_edit,
        "list_events",
        lambda *a, **k: (_ for _ in ()).throw(BookingSubprocessError("calendar 503")),
    )
    state = State("/tmp/s.sqlite")
    try:
        result = booking_edit._lookup_user_events("+6591234567", 90, state)
        assert result == []
    finally:
        state.close()


def test_event_id_falls_back_through_keys() -> None:
    assert booking_edit._event_id({"id": "X"}) == "X"
    assert booking_edit._event_id({"event_id": "Y"}) == "Y"
    assert booking_edit._event_id({}) is None


def test_digits_only_strips_non_digits() -> None:
    assert booking_edit._digits_only("+65 9123-4567") == "6591234567"
    assert booking_edit._digits_only("") == ""
    assert booking_edit._digits_only(None or "") == ""