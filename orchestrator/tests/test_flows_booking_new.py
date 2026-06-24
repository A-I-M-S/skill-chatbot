"""Tests for the book_new flow (issue #5)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from src.flows import booking_new
from src.state import State


# ──────────────────────────────────────────────────────────────────────
# Validation helpers
# ──────────────────────────────────────────────────────────────────────


def test_draft_missing_fields_requires_date_time_pax() -> None:
    assert booking_new._draft_missing_fields({}) == ["date", "time", "pax"]
    assert booking_new._draft_missing_fields({"date": "2026-08-15"}) == ["time", "pax"]
    assert booking_new._draft_missing_fields(
        {"date": "2026-08-15", "time": "10:30", "pax": 30}
    ) == ["contact"]


def test_draft_missing_fields_needs_contact_email_or_phone() -> None:
    d = {"date": "2026-08-15", "time": "10:30", "pax": 30, "contact_name": "Jane"}
    assert booking_new._draft_missing_fields(d) == ["contact"]
    d["contact_email"] = "jane@x.com"
    assert booking_new._draft_missing_fields(d) == []
    d.pop("contact_email")
    d["contact_phone"] = "+6591234567"
    assert booking_new._draft_missing_fields(d) == []


def test_validate_draft_ok() -> None:
    future = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    d = {"date": future, "time": "10:30", "pax": 30, "contact_email": "jane@x.com"}
    assert booking_new._validate_draft(d) is None


def test_validate_draft_past_date_rejected() -> None:
    past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    d = {"date": past, "time": "10:30", "pax": 30}
    err = booking_new._validate_draft(d)
    assert err is not None
    assert "past" in err


def test_validate_draft_too_far_in_future() -> None:
    far = (datetime.now() + timedelta(days=200)).strftime("%Y-%m-%d")
    d = {"date": far, "time": "10:30", "pax": 30}
    err = booking_new._validate_draft(d)
    assert err is not None
    assert "90 days" in err


def test_validate_draft_pax_out_of_range() -> None:
    future = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    for bad in (0, 501, "abc"):
        d = {"date": future, "time": "10:30", "pax": bad}
        err = booking_new._validate_draft(d)
        assert err is not None, f"pax={bad!r} should fail validation"


def test_draft_expired_uses_ttl() -> None:
    fresh = {"updated_at": 0}  # epoch = very stale
    assert booking_new._draft_expired(fresh) is True
    recent = {"updated_at": booking_new.time.time() - 10}
    assert booking_new._draft_expired(recent) is False


# ──────────────────────────────────────────────────────────────────────
# State machine
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def state(tmp_path: Path) -> State:
    s = State(tmp_path / "state.sqlite")
    try:
        yield s
    finally:
        s.close()


def test_fresh_flow_collecting_asks_for_date(state: State) -> None:
    reply = booking_new.handle(
        phone="6591234567",
        user_text="",
        tool_args={},
        state=state,
        language="en",
    )
    assert "date" in reply.lower() or "day" in reply.lower()
    ps = state.get_phone_state("6591234567")
    assert ps is not None
    assert ps["flow"] == "book_new"
    assert ps["draft"] == {}


def test_continues_collection_with_date_only_asks_time(state: State) -> None:
    reply = booking_new.handle(
        phone="6591234567",
        user_text="",
        tool_args={"date": "2026-08-15"},
        state=state,
        language="en",
    )
    assert "time" in reply.lower()
    ps = state.get_phone_state("6591234567")
    assert ps["draft"] == {"date": "2026-08-15"}


def test_continues_collection_with_date_time_pax_asks_contact(state: State) -> None:
    future = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    reply = booking_new.handle(
        phone="6591234567",
        user_text="",
        tool_args={"date": future, "time": "10:30", "pax": 30},
        state=state,
        language="en",
    )
    assert "email" in reply.lower() or "phone" in reply.lower()
    ps = state.get_phone_state("6591234567")
    assert ps["draft"]["date"] == future


def test_all_fields_present_prompts_for_confirmation(state: State) -> None:
    future = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    reply = booking_new.handle(
        phone="6591234567",
        user_text="",
        tool_args={
            "date": future,
            "time": "10:30",
            "pax": 30,
            "contact_name": "Jane Doe",
            "contact_email": "jane@school.cn",
        },
        state=state,
        language="en",
    )
    assert "YES" in reply
    ps = state.get_phone_state("6591234567")
    assert ps["pending_confirm"] is not None
    assert ps["pending_confirm"]["stage"] == "awaiting"


def test_chinese_flow_prompts_for_confirmation_in_zh(state: State) -> None:
    future = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    reply = booking_new.handle(
        phone="6591234567",
        user_text="",
        tool_args={
            "date": future,
            "time": "10:30",
            "pax": 30,
            "contact_name": "张老师",
            "contact_phone": "+6591234567",
        },
        state=state,
        language="zh",
    )
    assert "**YES**" in reply
    assert "回复" in reply


def test_yes_commits_and_clears_state(state: State, monkeypatch: pytest.MonkeyPatch) -> None:
    future = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    # Seed draft + pending_confirm
    state.set_phone_state(
        "6591234567",
        "book_new",
        draft={
            "date": future, "time": "10:30", "pax": 30,
            "contact_name": "Jane", "contact_email": "jane@x.com",
        },
        pending_confirm={"stage": "awaiting"},
        language="en",
    )

    # Stub the upstream commit
    captured: dict[str, Any] = {}

    def _fake_commit(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "error": None,
            "reply": "Booked! 30 pax on Sat 15 Aug 2026, 10:30 SGT at SAAC FARM.",
        }

    monkeypatch.setattr(booking_new, "new_commit", _fake_commit)

    reply = booking_new.handle(
        phone="6591234567",
        user_text="yes",
        tool_args={"date": future},  # router might still pass the date
        state=state,
        language="en",
        confirm_reply="YES",
    )
    assert "Booked" in reply or "pax" in reply
    assert captured["date"] == future
    assert captured["pax"] == 30
    # State cleared
    ps = state.get_phone_state("6591234567")
    assert ps is not None
    assert ps["flow"] == "idle"
    assert ps["draft"] is None


def test_yes_in_chinese_commits(state: State, monkeypatch: pytest.MonkeyPatch) -> None:
    future = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    state.set_phone_state(
        "6591234567",
        "book_new",
        draft={
            "date": future, "time": "10:30", "pax": 30,
            "contact_name": "Jane", "contact_email": "jane@x.com",
        },
        pending_confirm={"stage": "awaiting"},
        language="zh",
    )
    monkeypatch.setattr(
        booking_new,
        "new_commit",
        lambda **kw: {"error": None, "reply": "已预约。"},
    )
    reply = booking_new.handle(
        phone="6591234567",
        user_text="是",
        tool_args={},
        state=state,
        language="zh",
        confirm_reply="是",
    )
    assert "已预约" in reply
    ps = state.get_phone_state("6591234567")
    assert ps["flow"] == "idle"


def test_anything_else_aborts_preserves_draft(state: State) -> None:
    future = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    state.set_phone_state(
        "6591234567",
        "book_new",
        draft={
            "date": future, "time": "10:30", "pax": 30,
            "contact_name": "Jane", "contact_email": "jane@x.com",
        },
        pending_confirm={"stage": "awaiting"},
        language="en",
    )
    reply = booking_new.handle(
        phone="6591234567",
        user_text="actually never mind",
        tool_args={},
        state=state,
        language="en",
        confirm_reply="never mind",
    )
    assert "OK" in reply or "no problem" in reply
    ps = state.get_phone_state("6591234567")
    # Draft preserved so the user can resume
    assert ps["flow"] == "book_new"
    assert ps["draft"]["date"] == future


def test_stale_draft_is_dropped(state: State, monkeypatch: pytest.MonkeyPatch) -> None:
    state.set_phone_state(
        "6591234567",
        "book_new",
        draft={"date": "2026-08-15", "time": "10:30", "pax": 30},
        language="en",
    )
    # Backdate updated_at to make it stale
    import time
    cur = state.get_phone_state("6591234567")
    state._conn.execute(  # type: ignore[attr-defined]
        "UPDATE phone_state SET updated_at = ? WHERE phone = ?",
        (time.time() - booking_new.DRAFT_TTL_SECONDS - 60, "6591234567"),
    )
    reply = booking_new.handle(
        phone="6591234567",
        user_text="",
        tool_args={},
        state=state,
        language="en",
    )
    # After dropping the stale draft, the flow should re-start and ask
    # for the date (not time, because date is the first required field).
    assert "date" in reply.lower() or "day" in reply.lower()


def test_commit_error_falls_back_to_handoff_message(
    state: State, monkeypatch: pytest.MonkeyPatch
) -> None:
    future = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    state.set_phone_state(
        "6591234567",
        "book_new",
        draft={
            "date": future, "time": "10:30", "pax": 30,
            "contact_name": "Jane", "contact_email": "jane@x.com",
        },
        pending_confirm={"stage": "awaiting"},
        language="en",
    )

    def _boom(**kw: Any) -> dict[str, Any]:
        raise booking_new.BookingSubprocessError("calendar API down")

    monkeypatch.setattr(booking_new, "new_commit", _boom)
    monkeypatch.setenv("ADMIN_CONTACT_NUMBER", "+6591234567")
    reply = booking_new.handle(
        phone="6591234567",
        user_text="YES",
        tool_args={},
        state=state,
        language="en",
        confirm_reply="YES",
    )
    assert "team" in reply.lower() or "contact" in reply.lower()
    # State should be cleared so the user can retry
    ps = state.get_phone_state("6591234567")
    assert ps["flow"] == "idle"


def test_invalid_draft_field_is_reasked(state: State) -> None:
    """If a field is invalid (e.g. past date), drop it and re-ask."""
    past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    state.set_phone_state(
        "6591234567",
        "book_new",
        draft={"date": past, "time": "10:30", "pax": 30},
        language="en",
    )
    reply = booking_new.handle(
        phone="6591234567",
        user_text="",
        tool_args={"contact_name": "Jane", "contact_email": "jane@x.com"},
        state=state,
        language="en",
    )
    # The past date was dropped, so the next ask should be for date
    assert "date" in reply.lower() or "day" in reply.lower()
    ps = state.get_phone_state("6591234567")
    assert "date" not in ps["draft"]  # dropped
    assert ps["draft"]["pax"] == 30   # preserved
