"""Tests for the handoff flow (issue #7)."""

from __future__ import annotations

from typing import Any

import pytest

from src.flows import handoff


# ──────────────────────────────────────────────────────────────────────
# Stubs
# ──────────────────────────────────────────────────────────────────────


def _capture_notify(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def _fake(sender: str, reason: str, summary: str, is_fallback: bool = False, client: Any = None) -> int:
        captured.append(
            {
                "sender": sender,
                "reason": reason,
                "summary": summary,
                "is_fallback": is_fallback,
            }
        )
        return 1  # pretend one admin notified

    monkeypatch.setattr(handoff.notify, "notify_handoff", _fake)
    return captured


# ──────────────────────────────────────────────────────────────────────
# Basic handoff (refund)
# ──────────────────────────────────────────────────────────────────────


def test_handoff_refund_sends_admin_and_returns_team_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_notify(monkeypatch)
    monkeypatch.setenv("ADMIN_CONTACT_NUMBER", "+6591234567")
    reply = handoff.handle(
        sender="6591234567",
        reason="refund",
        summary="I want my money back",
        language="en",
    )
    assert "team" in reply.lower()
    assert "6591234567" in reply
    assert len(captured) == 1
    assert captured[0]["reason"] == "refund"
    assert captured[0]["sender"] == "6591234567"
    assert captured[0]["is_fallback"] is False


# ──────────────────────────────────────────────────────────────────────
# Bilingual
# ──────────────────────────────────────────────────────────────────────


def test_handoff_chinese_uses_chinese_message(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_notify(monkeypatch)
    monkeypatch.setenv("ADMIN_CONTACT_NUMBER", "+6591234567")
    reply = handoff.handle(
        sender="6591234567",
        reason="refund",
        summary="我要退款",
        language="zh",
    )
    assert "团队" in reply or "转给" in reply
    assert "6591234567" in reply
    assert captured[0]["summary"] == "我要退款"


# ──────────────────────────────────────────────────────────────────────
# Abuse special-case
# ──────────────────────────────────────────────────────────────────────


def test_handoff_abuse_returns_short_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_notify(monkeypatch)
    monkeypatch.setenv("ADMIN_CONTACT_NUMBER", "+6591234567")
    reply = handoff.handle(
        sender="6591234567",
        reason="abuse",
        summary="<abusive content>",
        language="en",
    )
    # Short ack, doesn't tell the abuser about the admin DM
    assert "team has been notified" in reply.lower()
    assert "team will reach out" not in reply.lower()  # that's the non-abuse path
    # Admin IS still notified so operators can decide to block
    assert len(captured) == 1
    assert captured[0]["reason"] == "abuse"


def test_handoff_abuse_chinese(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_notify(monkeypatch)
    monkeypatch.setenv("ADMIN_CONTACT_NUMBER", "+6591234567")
    reply = handoff.handle(
        sender="6591234567",
        reason="abuse",
        summary="<abuse>",
        language="zh",
    )
    assert "无法处理" in reply or "已通知" in reply


# ──────────────────────────────────────────────────────────────────────
# Fallback tag
# ──────────────────────────────────────────────────────────────────────


def test_handoff_fallback_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_notify(monkeypatch)
    monkeypatch.setenv("ADMIN_CONTACT_NUMBER", "+6591234567")
    handoff.handle(
        sender="6591234567",
        reason="refund",
        summary="x",
        language="en",
        is_fallback=True,
    )
    assert captured[0]["is_fallback"] is True


# ──────────────────────────────────────────────────────────────────────
# Defensive coercion
# ──────────────────────────────────────────────────────────────────────


def test_handoff_invalid_reason_coerced_to_other(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_notify(monkeypatch)
    monkeypatch.setenv("ADMIN_CONTACT_NUMBER", "+6591234567")
    handoff.handle(
        sender="6591234567",
        reason="<not a valid reason>",
        summary="x",
        language="en",
    )
    assert captured[0]["reason"] == "other"


# ──────────────────────────────────────────────────────────────────────
# WA_NOTIFY empty — flow still works, returns customer reply
# ──────────────────────────────────────────────────────────────────────


def test_handoff_works_when_wa_notify_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no admins are configured, the customer still gets a reply
    and notify.notify_handoff is still called (it logs the no-op)."""

    def _fake(sender: str, reason: str, summary: str, is_fallback: bool = False, client: Any = None) -> int:
        return 0  # no admins

    monkeypatch.setattr(handoff.notify, "notify_handoff", _fake)
    monkeypatch.setenv("ADMIN_CONTACT_NUMBER", "+6591234567")
    reply = handoff.handle(
        sender="6591234567", reason="refund", summary="x", language="en"
    )
    assert "team" in reply.lower()


# ──────────────────────────────────────────────────────────────────────
# Default language fallback
# ──────────────────────────────────────────────────────────────────────


def test_handoff_default_language_is_en(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_notify(monkeypatch)
    monkeypatch.setenv("ADMIN_CONTACT_NUMBER", "+6591234567")
    reply = handoff.handle(
        sender="6591234567",
        reason="complaint",
        summary="x",
        language="",
    )
    assert "team" in reply.lower() or "notified" in reply.lower()


# ──────────────────────────────────────────────────────────────────────
# Reasons
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reason",
    ["refund", "complaint", "custom_pricing", "abuse", "other"],
)
def test_handoff_all_reasons_resolve(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    _capture_notify(monkeypatch)
    monkeypatch.setenv("ADMIN_CONTACT_NUMBER", "+6591234567")
    reply = handoff.handle(
        sender="6591234567", reason=reason, summary="x", language="en"
    )
    assert isinstance(reply, str) and reply