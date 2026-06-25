"""Admin HTTP sub-app for the orchestrator.

Mounted at ``/admin`` by ``src.http_server``. Reuses the existing Qdrant
ingest (via ``rag_qdrant``) and Composio Outlook wrappers so we don't
fork the upstream skill code paths.

Public API:

- :func:`verify_request` — token + telegram-id auth gate (returns 401/403/ok).
- :func:`dispatch` — single entrypoint: ``(method, path, body, query, headers)``
  → ``(status_code, response_body)``. ``http_server`` routes any path under
  ``/admin`` here.
- :func:`AdminSettings` — typed bundle of ``ADMIN_HTTP_TOKEN``,
  ``ADMIN_TELEGRAM_IDS``, ``BOOKING_RULES_PATH`` so callers can construct
  it from the orchestrator's :class:`Settings` without re-reading env.

The actual handler bodies live in :mod:`src.admin.handlers` (kept thin so
they're easy to unit-test) and the YAML patch in :mod:`src.admin.yaml_patch`.
This module is just the wiring (auth + dispatch table + JSON helpers).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .handlers import (
    handle_bookings,
    handle_config_patch,
    handle_grant,
    handle_ingest,
    handle_revoke,
    handle_show,
)
from .yaml_patch import PatchError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdminSettings:
    """Bundle of admin-only config. Built from the main :class:`Settings`."""

    admin_http_token: str
    admin_telegram_ids: tuple[int, ...]
    booking_rules_path: str | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> AdminSettings:
        return cls(
            admin_http_token=str(getattr(settings, "admin_http_token", "") or ""),
            admin_telegram_ids=tuple(getattr(settings, "admin_telegram_ids", ()) or ()),
            booking_rules_path=(
                str(settings.booking_rules_path)
                if getattr(settings, "booking_rules_path", None)
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def verify_request(headers: dict[str, str], admin: AdminSettings) -> int:
    """Return the caller's admin telegram id when auth passes; raise :class:`AuthError` otherwise.

    Headers expected:

    - ``X-Admin-Token`` — must equal ``ADMIN_HTTP_TOKEN`` (defense-in-depth).
    - ``X-Admin-Telegram-Id`` — int; must be in ``ADMIN_TELEGRAM_IDS``.

    The same env is checked locally by the ``admin-bot/`` TG skill (issue #31).
    """
    token = headers.get("X-Admin-Token", "")
    if not admin.admin_http_token:
        raise AuthError(401, "admin_disabled", "ADMIN_HTTP_TOKEN not configured on server")
    if not token:
        raise AuthError(401, "missing_token", "missing X-Admin-Token header")
    if token != admin.admin_http_token:
        raise AuthError(401, "bad_token", "X-Admin-Token does not match ADMIN_HTTP_TOKEN")
    raw_id = headers.get("X-Admin-Telegram-Id", "")
    if not raw_id:
        raise AuthError(403, "missing_telegram_id", "missing X-Admin-Telegram-Id header")
    try:
        tid = int(raw_id)
    except ValueError as exc:
        raise AuthError(
            403, "bad_telegram_id", f"X-Admin-Telegram-Id must be an int: {exc}"
        ) from exc
    if not admin.admin_telegram_ids:
        raise AuthError(403, "no_admins", "ADMIN_TELEGRAM_IDS not configured on server")
    if tid not in admin.admin_telegram_ids:
        raise AuthError(403, "not_admin", f"telegram id {tid} is not in ADMIN_TELEGRAM_IDS")
    return tid


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


Handler = Callable[..., tuple[int, dict[str, Any]]]


# path → (method, handler) registry
_ROUTES: dict[tuple[str, str], Handler] = {
    ("POST", "/admin/ingest"): handle_ingest,
    ("POST", "/admin/grant"): handle_grant,
    ("POST", "/admin/revoke"): handle_revoke,
    ("GET", "/admin/show"): handle_show,
    ("GET", "/admin/bookings"): handle_bookings,
    ("PATCH", "/admin/config"): handle_config_patch,
}


def is_admin_path(path: str) -> bool:
    """True if this request should be routed to the admin sub-app."""
    return path == "/admin" or path.startswith("/admin/")


def dispatch(
    method: str,
    path: str,
    *,
    body: bytes,
    query: dict[str, str],
    headers: dict[str, str],
    admin: AdminSettings,
) -> tuple[int, dict[str, Any]]:
    """Single entrypoint used by ``src.http_server``.

    Returns ``(status_code, body_dict)`` so the existing ``_json`` helper can
    serialise it. 401 / 403 short-circuit before route lookup; other routes
    raise :class:`ValueError` (→ 400) on bad input, ``PatchError`` (→ 400)
    on disallowed YAML keys, or whatever the underlying call raises (→ 500
    via the http_server catch-all).
    """
    try:
        verify_request(headers, admin)
    except AuthError as exc:
        return exc.status, {"ok": False, "error": exc.code, "message": exc.message}

    normalised = path.split("?", 1)[0].rstrip("/") or "/"
    handler = _ROUTES.get((method.upper(), normalised))
    if handler is None:
        return 404, {"ok": False, "error": "not_found", "path": path}

    parsed_body: Any
    if body:
        try:
            parsed_body = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return 400, {"ok": False, "error": "bad_json", "message": str(exc)}
    else:
        parsed_body = None

    try:
        if method.upper() == "GET":
            status, payload = handler(query=query, admin=admin)  # type: ignore[arg-type]
        else:
            status, payload = handler(parsed_body, admin=admin)  # type: ignore[arg-type]
    except PatchError as exc:
        return 400, {"ok": False, "error": exc.code, "message": exc.message}
    except ValueError as exc:
        return 400, {"ok": False, "error": "bad_request", "message": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("admin dispatch error path=%s method=%s", path, method)
        return 500, {"ok": False, "error": "internal", "message": str(exc)}
    return status, payload


__all__ = [
    "AdminSettings",
    "AuthError",
    "dispatch",
    "is_admin_path",
    "verify_request",
]
