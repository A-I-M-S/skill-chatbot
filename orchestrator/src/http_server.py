"""HTTP server for the orchestrator.

Stdlib-only — no starlette/uvicorn. Runs in a daemon thread so it doesn't
block the tail loop. Exposes:

- ``GET /health`` — always 200 with ``{"ok": true, "db": "ok",
  "last_processed_message_id": "..." | null}`` (or 503 if the SQLite
  health check fails).
- ``/admin/*`` — admin sub-app (issue #30). All routes go through
  :func:`src.admin.dispatch` for auth + routing. The ``Settings`` object
  passed to :func:`start_server` supplies ``admin_http_token`` /
  ``admin_telegram_ids`` / ``booking_rules_path``.

The admin sub-app is a stdlib router (not a starlette ``Mount``) — it
shares the same daemon thread and port as ``/health`` so we don't have
to coordinate two servers in ``main.py``. The customer-facing
``/health`` route keeps its 404-on-unknown behaviour for paths it
doesn't own (everything else is the admin sub-app's responsibility).
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .admin import AdminSettings, is_admin_path
from .admin import dispatch as admin_dispatch
from .settings import Settings
from .state import State

logger = logging.getLogger(__name__)


def make_handler(state: State, admin: AdminSettings | None = None) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to the given :class:`State`.

    ``admin`` defaults to an empty :class:`AdminSettings` so tests that
    only exercise ``/health`` don't have to wire anything up.
    """
    admin_cfg = admin or AdminSettings(
        admin_http_token="", admin_telegram_ids=(), booking_rules_path=None
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("http: " + format, *args)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PATCH(self) -> None:
            self._dispatch("PATCH")

        def _dispatch(self, method: str) -> None:
            parsed = urlsplit(self.path)
            path = parsed.path
            query = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}

            if is_admin_path(path):
                body = self._read_body()
                status, payload = admin_dispatch(
                    method,
                    path,
                    body=body,
                    query=query,
                    headers={k: v for k, v in self.headers.items()},
                    admin=admin_cfg,
                )
                self._json(status, payload)
                return

            if method == "GET" and path == "/health":
                try:
                    last = state.last_processed_message_id()
                    db_status = state.health()
                except Exception as exc:
                    logger.exception("health check failed")
                    self._json(503, {"ok": False, "db": f"error: {exc}"})
                    return
                self._json(
                    200,
                    {"ok": True, "db": db_status, "last_processed_message_id": last},
                )
                return

            self._json(404, {"ok": False, "error": "not_found"})

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return b""
            try:
                return self.rfile.read(length)
            except Exception:
                logger.exception("admin: failed to read request body")
                return b""

        def _json(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def start_server(
    state: State,
    host: str,
    port: int,
    *,
    admin: AdminSettings | None = None,
    settings: Settings | None = None,
) -> ThreadingHTTPServer:
    """Start the HTTP server in a daemon thread and return the server.

    ``settings`` (optional) is used to pull admin token + telegram ids +
    booking-rules path. If you only have a :class:`State` and no
    ``Settings``, pass ``admin`` directly.
    """
    if admin is None:
        admin = (
            AdminSettings.from_settings(settings)
            if settings is not None
            else AdminSettings(admin_http_token="", admin_telegram_ids=(), booking_rules_path=None)
        )
    handler_cls = make_handler(state, admin=admin)
    server = ThreadingHTTPServer((host, port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, name="orchestrator-http", daemon=True)
    thread.start()
    logger.info(
        "orchestrator http listening on %s:%d (admin_token_set=%s admin_ids=%s)",
        host,
        port,
        bool(admin.admin_http_token),
        len(admin.admin_telegram_ids),
    )
    return server


def stop_server(server: ThreadingHTTPServer) -> None:
    """Shutdown the HTTP server (called from ``main.py`` on signal)."""
    server.shutdown()
    server.server_close()


__all__ = ["make_handler", "start_server", "stop_server"]
