"""HTTP server for the orchestrator (v0: ``/health`` only).

Stdlib-only — no starlette/uvicorn. Runs in a daemon thread so it doesn't
block the tail loop. ``/health`` always returns 200 with::

    {"ok": true, "db": "ok", "last_processed_message_id": "..." | null}
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .state import State

logger = logging.getLogger(__name__)


def make_handler(state: State) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to the given :class:`State`."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("http: " + format, *args)

        def do_GET(self) -> None:
            if self.path != "/health":
                self._json(404, {"ok": False, "error": "not_found"})
                return
            try:
                last = state.last_processed_message_id()
                db_status = state.health()
            except Exception as exc:
                logger.exception("health check failed")
                self._json(503, {"ok": False, "db": f"error: {exc}"})
                return
            self._json(200, {"ok": True, "db": db_status, "last_processed_message_id": last})

        def _json(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def start_server(state: State, host: str, port: int) -> ThreadingHTTPServer:
    """Start the HTTP server in a daemon thread and return the server."""
    handler_cls = make_handler(state)
    server = ThreadingHTTPServer((host, port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, name="orchestrator-http", daemon=True)
    thread.start()
    logger.info("orchestrator http listening on %s:%d", host, port)
    return server


def stop_server(server: ThreadingHTTPServer) -> None:
    """Shutdown the HTTP server (called from ``main.py`` on signal)."""
    server.shutdown()
    server.server_close()


__all__ = ["make_handler", "start_server", "stop_server"]
