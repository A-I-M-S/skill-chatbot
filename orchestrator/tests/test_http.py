"""Tests for the orchestrator HTTP server (``/health``)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src import http_server as http_srv
from src.state import open_state


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_health_shape(tmp_state_db: Path) -> None:
    port = _free_port()
    with open_state(tmp_state_db) as state:
        server = http_srv.start_server(state, host="127.0.0.1", port=port)
        try:
            import urllib.request

            for _ in range(20):
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=1.0
                    ) as resp:
                        body = json.loads(resp.read())
                        assert resp.status == 200
                        assert body == {"ok": True, "db": "ok", "last_processed_message_id": None}
                        break
                except (ConnectionRefusedError, OSError):
                    time.sleep(0.05)
            else:
                pytest.fail("orchestrator http server did not start")
        finally:
            http_srv.stop_server(server)


def test_health_reports_last_processed(tmp_state_db: Path) -> None:
    port = _free_port()
    with open_state(tmp_state_db) as state:
        state.mark_processed("m42")
        server = http_srv.start_server(state, host="127.0.0.1", port=port)
        try:
            import urllib.request

            for _ in range(20):
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=1.0
                    ) as resp:
                        body = json.loads(resp.read())
                        assert body["last_processed_message_id"] == "m42"
                        break
                except (ConnectionRefusedError, OSError):
                    time.sleep(0.05)
            else:
                pytest.fail("orchestrator http server did not start")
        finally:
            http_srv.stop_server(server)


def test_health_404_on_unknown_path(tmp_state_db: Path) -> None:
    port = _free_port()
    with open_state(tmp_state_db) as state:
        server = http_srv.start_server(state, host="127.0.0.1", port=port)
        try:
            import urllib.request

            for _ in range(20):
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/nope", timeout=1.0
                    ) as resp:
                        assert resp.status == 404
                        break
                except urllib.error.HTTPError as e:
                    assert e.code == 404
                    break
                except (ConnectionRefusedError, OSError):
                    time.sleep(0.05)
            else:
                pytest.fail("orchestrator http server did not start")
        finally:
            http_srv.stop_server(server)
