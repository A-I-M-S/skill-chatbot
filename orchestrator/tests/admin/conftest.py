"""Pytest fixtures for admin sub-app tests.

The admin sub-app dispatches through :func:`src.admin.dispatch` and the
upstream ``BaseHTTPRequestHandler`` in ``src.http_server``. These fixtures
give the tests a configured :class:`AdminSettings` plus helpers to:

- send a fake HTTP request without binding a socket (calls ``dispatch``
  directly), and
- run a real ``ThreadingHTTPServer`` on an ephemeral port for the
  integration tests under ``test_routes.py``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from src.admin import AdminSettings
from src.admin import dispatch as admin_dispatch

# ---------------------------------------------------------------------------
# AdminSettings
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_cfg(tmp_path: Path) -> AdminSettings:
    """Real ``AdminSettings`` for tests that exercise the dispatcher.

    Named ``admin_cfg`` (not ``admin_settings``) to avoid clashing with
    the root ``tests/conftest.py`` fixture of the same name.
    """
    return AdminSettings(
        admin_http_token="s3cret-token",
        admin_telegram_ids=(111, 222),
        booking_rules_path=str(tmp_path / "booking_rules.yaml"),
    )


@pytest.fixture
def empty_admin_settings() -> AdminSettings:
    """``AdminSettings`` with no token / no telegram ids — auth should reject everything."""
    return AdminSettings(admin_http_token="", admin_telegram_ids=(), booking_rules_path=None)


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------


def call(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    admin: AdminSettings,
) -> tuple[int, dict[str, Any]]:
    """Invoke :func:`src.admin.dispatch` in-process and decode the JSON reply.

    The real HTTP server parses the query string off the URL itself; this
    helper does the same so test calls with ``?source=faq`` match the
    production wire behaviour.
    """
    from urllib.parse import parse_qs, urlsplit

    headers = dict(headers or {})
    body_bytes = json.dumps(body).encode("utf-8") if body is not None else b""
    parsed_path = urlsplit(path)
    base_path = parsed_path.path
    parsed_qs = {k: v[0] for k, v in parse_qs(parsed_path.query, keep_blank_values=True).items()}
    if query:
        parsed_qs.update(query)
    status, payload = admin_dispatch(
        method,
        base_path,
        body=body_bytes,
        query=parsed_qs,
        headers=headers,
        admin=admin,
    )
    return status, payload


def auth_headers(token: str = "s3cret-token", tid: int = 111) -> dict[str, str]:
    return {"X-Admin-Token": token, "X-Admin-Telegram-Id": str(tid)}


# ---------------------------------------------------------------------------
# Real HTTP server (used by test_routes for an end-to-end smoke)
# ---------------------------------------------------------------------------


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def admin_server(tmp_state_db: Path, admin_cfg: AdminSettings):
    """Spin up the orchestrator HTTP server with admin routes on an ephemeral port."""
    from src import http_server
    from src.state import open_state

    port = free_port()
    with open_state(tmp_state_db) as state:
        server = http_server.start_server(state, host="127.0.0.1", port=port, admin=admin_cfg)
        # Wait for the thread to start accepting.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                import urllib.request

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(0.02)
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            http_server.stop_server(server)


def http_get(url: str, headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    import urllib.request

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def http_post(
    url: str, headers: dict[str, str], body: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    import urllib.request

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={**headers, "Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def http_patch(
    url: str, headers: dict[str, str], body: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    import urllib.request

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={**headers, "Content-Type": "application/json"}, method="PATCH"
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())
