"""Auth tests for the admin sub-app (issue #30 — defense-in-depth).

Covers the two header gates:

1. ``X-Admin-Token`` must equal ``ADMIN_HTTP_TOKEN`` — missing or wrong
   token is a 401.
2. ``X-Admin-Telegram-Id`` must be in ``ADMIN_TELEGRAM_IDS`` — missing
   or wrong id is a 403.

We exercise these through the dispatcher (no socket) so the assertions
are decoupled from ``BaseHTTPRequestHandler`` quirks.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.admin import AuthError, verify_request

from .conftest import AdminSettings, auth_headers, call


def test_verify_request_returns_tid_when_token_and_id_ok(admin_cfg: AdminSettings) -> None:
    assert verify_request(auth_headers(), admin_cfg) == 111


def test_verify_request_rejects_wrong_token(admin_cfg: AdminSettings) -> None:
    with pytest.raises(AuthError) as exc_info:
        verify_request(
            {"X-Admin-Token": "wrong", "X-Admin-Telegram-Id": "111"},
            admin_cfg,
        )
    assert exc_info.value.status == 401
    assert exc_info.value.code == "bad_token"


def test_verify_request_rejects_missing_token(admin_cfg: AdminSettings) -> None:
    with pytest.raises(AuthError) as exc_info:
        verify_request({"X-Admin-Telegram-Id": "111"}, admin_cfg)
    assert exc_info.value.status == 401
    assert exc_info.value.code == "missing_token"


def test_verify_request_rejects_non_admin_tid(admin_cfg: AdminSettings) -> None:
    with pytest.raises(AuthError) as exc_info:
        verify_request(
            {"X-Admin-Token": "s3cret-token", "X-Admin-Telegram-Id": "999"},
            admin_cfg,
        )
    assert exc_info.value.status == 403
    assert exc_info.value.code == "not_admin"


def test_verify_request_rejects_missing_tid(admin_cfg: AdminSettings) -> None:
    with pytest.raises(AuthError) as exc_info:
        verify_request({"X-Admin-Token": "s3cret-token"}, admin_cfg)
    assert exc_info.value.status == 403
    assert exc_info.value.code == "missing_telegram_id"


def test_verify_request_rejects_non_integer_tid(admin_cfg: AdminSettings) -> None:
    with pytest.raises(AuthError) as exc_info:
        verify_request(
            {"X-Admin-Token": "s3cret-token", "X-Admin-Telegram-Id": "not-an-int"},
            admin_cfg,
        )
    assert exc_info.value.status == 403
    assert exc_info.value.code == "bad_telegram_id"


def test_verify_request_rejects_when_server_token_unset() -> None:
    cfg = AdminSettings(admin_http_token="", admin_telegram_ids=(111,), booking_rules_path=None)
    with pytest.raises(AuthError) as exc_info:
        verify_request(auth_headers(), cfg)
    assert exc_info.value.status == 401
    assert exc_info.value.code == "admin_disabled"


def test_verify_request_rejects_when_server_admins_unset() -> None:
    cfg = AdminSettings(
        admin_http_token="s3cret-token", admin_telegram_ids=(), booking_rules_path=None
    )
    with pytest.raises(AuthError) as exc_info:
        verify_request(auth_headers(), cfg)
    assert exc_info.value.status == 403
    assert exc_info.value.code == "no_admins"


# ---------------------------------------------------------------------------
# Dispatch-level: every route returns 401/403 when headers missing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/admin/ingest", {"source_type": "path", "target": "/tmp/x"}),
        ("POST", "/admin/grant", {"source": "faq", "telegram_id": 1}),
        ("POST", "/admin/revoke", {"source": "faq", "telegram_id": 1}),
        ("GET", "/admin/show", None),
        ("GET", "/admin/bookings?date=2026-06-25", None),
        ("PATCH", "/admin/config", {"key": "slot_duration_minutes", "value": 60}),
    ],
)
def test_routes_require_token(
    admin_cfg: AdminSettings,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    status, payload = call(method, path, body=body, admin=admin_cfg)
    assert status == 401
    assert payload["ok"] is False
    assert payload["error"] == "missing_token"


def test_routes_require_tid_when_token_present(admin_cfg: AdminSettings) -> None:
    status, payload = call(
        "GET",
        "/admin/show",
        headers={"X-Admin-Token": "s3cret-token"},
        admin=admin_cfg,
    )
    assert status == 403
    assert payload["error"] == "missing_telegram_id"


def test_routes_require_admin_tid(admin_cfg: AdminSettings) -> None:
    status, payload = call(
        "GET",
        "/admin/show",
        headers={"X-Admin-Token": "s3cret-token", "X-Admin-Telegram-Id": "999"},
        admin=admin_cfg,
    )
    assert status == 403
    assert payload["error"] == "not_admin"


def test_unknown_route_404s_under_auth(admin_cfg: AdminSettings) -> None:
    status, payload = call(
        "POST",
        "/admin/nope",
        body={"x": 1},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 404
    assert payload["error"] == "not_found"


def test_path_just_under_admin_404s(admin_cfg: AdminSettings) -> None:
    """``/admin`` (no trailing route) is not in the table — should 404."""
    status, payload = call(
        "GET",
        "/admin",
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 404
    assert payload["error"] == "not_found"
