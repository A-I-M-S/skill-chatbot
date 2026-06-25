"""End-to-end route tests through the real ``ThreadingHTTPServer``.

The dispatcher tests under ``test_auth.py`` / ``test_acl.py`` /
``test_yaml_patch.py`` cover the business logic; this file makes sure
the wire-up in ``src.http_server`` (path routing, method dispatch,
body reading) actually works against a real socket.

We also exercise the bookings route via the subprocess fallback path so
the test doesn't need ``composio_outlook`` installed in the orchestrator
venv.
"""

from __future__ import annotations

import sys

import pytest

from src.admin import AdminSettings

from .conftest import auth_headers, http_get, http_post

# ---------------------------------------------------------------------------
# /admin/bookings
# ---------------------------------------------------------------------------


def test_bookings_requires_date(admin_server: str, admin_cfg: AdminSettings) -> None:
    status, payload = http_get(f"{admin_server}/admin/bookings", auth_headers())
    assert status == 400
    assert "date" in payload["message"]


def test_bookings_rejects_bad_date(admin_server: str, admin_cfg: AdminSettings) -> None:
    status, payload = http_get(f"{admin_server}/admin/bookings?date=yesterday", auth_headers())
    assert status == 400
    assert "date" in payload["message"]


def test_bookings_via_subprocess_when_composio_missing(
    admin_server: str, admin_cfg: AdminSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``composio_outlook`` isn't importable the handler falls back to
    ``booking_subprocess.list_events``. We patch that to a fake to keep
    the test offline.

    """
    from src.admin import handlers

    fake_events = [
        {
            "id": "evt-1",
            "subject": "Farm tour — School — 30 pax",
            "start": {"dateTime": "2026-06-25T09:00:00", "timeZone": "Asia/Singapore"},
            "end": {"dateTime": "2026-06-25T10:00:00", "timeZone": "Asia/Singapore"},
            "location": {"displayName": "SAAC FARM"},
        },
    ]

    def _fake_list_events(frm: str, to: str) -> list[dict[str, object]]:
        assert "2026-06-25" in frm
        return fake_events

    # Block composio import to exercise the subprocess fallback
    monkeypatch.setitem(sys.modules, "composio_outlook", None)

    from src import booking_subprocess

    monkeypatch.setattr(booking_subprocess, "list_events", _fake_list_events)
    # Ensure the handler imports the same booking_subprocess module instance
    monkeypatch.setattr(handlers, "_bookings_via_subprocess", handlers._bookings_via_subprocess)
    # Make sure ``composio_outlook`` import raises ImportError so we
    # hit the fallback path.
    import builtins

    orig_import = builtins.__import__

    def _patched_import(name: str, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if name == "composio_outlook" or name.startswith("composio_outlook."):
            raise ImportError("blocked for test")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _patched_import)

    status, payload = http_get(f"{admin_server}/admin/bookings?date=2026-06-25", auth_headers())
    assert status == 200, payload
    assert payload["date"] == "2026-06-25"
    assert payload["tz"] == "Asia/Singapore"
    assert len(payload["events"]) == 1
    evt = payload["events"][0]
    assert evt["id"] == "evt-1"
    assert evt["subject"] == "Farm tour — School — 30 pax"
    assert evt["start"] == "2026-06-25T09:00:00"
    assert evt["location"] == "SAAC FARM"


# ---------------------------------------------------------------------------
# /admin/* end-to-end through the real socket (PATCH is the meatiest path)
# ---------------------------------------------------------------------------


def test_routes_404_for_unknown_admin_path(admin_server: str) -> None:
    status, payload = http_post(
        f"{admin_server}/admin/nope",
        auth_headers(),
        body={"x": 1},
    )
    assert status == 404
    assert payload["error"] == "not_found"


def test_health_still_works_alongside_admin(admin_server: str) -> None:
    """The admin sub-app must NOT break the customer-facing ``/health`` route."""
    status, payload = http_get(f"{admin_server}/health", {})
    assert status == 200
    assert payload["ok"] is True
    assert "db" in payload


def test_admin_path_auth_missing_returns_401(admin_server: str) -> None:
    status, payload = http_get(f"{admin_server}/admin/show", {})
    assert status == 401
    assert payload["error"] == "missing_token"


def test_admin_path_auth_wrong_token_returns_401(admin_server: str) -> None:
    status, payload = http_get(
        f"{admin_server}/admin/show",
        {"X-Admin-Token": "nope", "X-Admin-Telegram-Id": "111"},
    )
    assert status == 401
    assert payload["error"] == "bad_token"


def test_admin_path_auth_non_admin_returns_403(admin_server: str) -> None:
    status, payload = http_get(
        f"{admin_server}/admin/show",
        {"X-Admin-Token": "s3cret-token", "X-Admin-Telegram-Id": "999"},
    )
    assert status == 403
    assert payload["error"] == "not_admin"
