"""Integration tests for admin-bot CLI scripts.

Spawns each ``bin/<cmd>`` as a subprocess (mirrors how OpenClaw will
invoke them) and points them at a tiny in-process HTTP server that
mocks the orchestrator admin API.

Run with::

    cd <repo> && python -m pytest admin-bot/tests -q
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin"
ADMIN_ID = 920567169
OTHER_ID = 12345


# ---------------------------------------------------------------------------
# In-process fake orchestrator
# ---------------------------------------------------------------------------


class _FakeOrchestrator(BaseHTTPRequestHandler):
    """Records the last request and returns a canned response."""

    last_request: dict | None = None
    response_status = 200
    response_body: dict | str = {"ok": True}

    def do_GET(self):  # noqa: N802
        self._capture()
        self._respond()

    def do_POST(self):  # noqa: N802
        self._capture()
        self._respond()

    def do_PATCH(self):  # noqa: N802
        self._capture()
        self._respond()

    def _capture(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = raw.decode("utf-8", errors="replace")
        _FakeOrchestrator.last_request = {
            "method": self.command,
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
        }

    def _respond(self):
        body = _FakeOrchestrator.response_body
        if isinstance(body, dict):
            raw = json.dumps(body).encode("utf-8")
            ctype = "application/json"
        else:
            raw = str(body).encode("utf-8")
            ctype = "text/plain; charset=utf-8"
        self.send_response(_FakeOrchestrator.response_status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format, *args):  # silence stderr
        pass


@contextmanager
def fake_orchestrator(status: int = 200, body: dict | str = {"ok": True}):
    """Spin up a fake orchestrator on a free port; restore state on exit."""
    _FakeOrchestrator.last_request = None
    _FakeOrchestrator.response_status = status
    _FakeOrchestrator.response_body = body
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOrchestrator)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/admin"
    finally:
        server.shutdown()
        server.server_close()


def _write_env(tmp_path: Path, base_url: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(
        f"ADMIN_API_BASE={base_url}\n"
        f"ADMIN_HTTP_TOKEN=secret-xyz\n"
        f"ADMIN_TELEGRAM_IDS={ADMIN_ID}\n",
        encoding="utf-8",
    )
    return env


def _run(cmd: str, *args: str, env_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(BIN / cmd), *args],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(BIN.parent),  # so 'bin' is importable for _lib.py
        },
        cwd=str(env_path.parent),  # so _lib.load_env() finds .env in CWD
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ingest_happy_path(tmp_path):
    with fake_orchestrator(200, {"ok": True, "chunks": 7, "acl": {"public": False, "telegram_ids": [ADMIN_ID]}}) as base:
        env = _write_env(tmp_path, base)
        (tmp_path / "rules.md").write_text("# rules\n\n- rule one\n- rule two\n", encoding="utf-8")
        result = _run("ingest", "--sender-id", str(ADMIN_ID), str(tmp_path / "rules.md"), env_path=env)
    assert result.returncode == 0, result.stderr
    assert "Ingested *7* chunk(s)" in result.stdout
    req = _FakeOrchestrator.last_request
    assert req["method"] == "POST"
    assert req["path"] == "/admin/ingest"
    assert req["headers"]["x-admin-token"] == "secret-xyz"
    assert req["headers"]["x-admin-telegram-id"] == str(ADMIN_ID)
    assert req["body"]["source_type"] == "md"
    assert req["body"]["target"].endswith("rules.md")


def test_ingest_refuses_non_admin(tmp_path):
    with fake_orchestrator() as base:
        env = _write_env(tmp_path, base)
        (tmp_path / "x.md").write_text("# x\n", encoding="utf-8")
        result = _run("ingest", "--sender-id", str(OTHER_ID), str(tmp_path / "x.md"), env_path=env)
    assert result.returncode == 2
    assert "Refused" in result.stderr
    assert _FakeOrchestrator.last_request is None


def test_grant_happy_path(tmp_path):
    with fake_orchestrator(200, {"ok": True}) as base:
        env = _write_env(tmp_path, base)
        result = _run("grant", "--sender-id", str(ADMIN_ID), "rules", "42", env_path=env)
    assert result.returncode == 0, result.stderr
    assert "Granted `42` access to `rules`" in result.stdout
    assert _FakeOrchestrator.last_request["body"] == {"source": "rules", "telegram_id": 42}


def test_grant_refuses_non_admin(tmp_path):
    with fake_orchestrator() as base:
        env = _write_env(tmp_path, base)
        result = _run("grant", "--sender-id", str(OTHER_ID), "rules", "42", env_path=env)
    assert result.returncode == 2
    assert _FakeOrchestrator.last_request is None


def test_grant_rejects_username(tmp_path):
    with fake_orchestrator() as base:
        env = _write_env(tmp_path, base)
        result = _run("grant", "--sender-id", str(ADMIN_ID), "rules", "@someone", env_path=env)
    assert result.returncode == 1
    assert "not yet implemented" in result.stderr
    assert _FakeOrchestrator.last_request is None


def test_grant_rejects_non_integer(tmp_path):
    with fake_orchestrator() as base:
        env = _write_env(tmp_path, base)
        result = _run("grant", "--sender-id", str(ADMIN_ID), "rules", "abc", env_path=env)
    assert result.returncode == 1
    assert "must be an integer" in result.stderr
    assert _FakeOrchestrator.last_request is None


def test_revoke_happy_path(tmp_path):
    with fake_orchestrator(200, {"ok": True}) as base:
        env = _write_env(tmp_path, base)
        result = _run("revoke", "--sender-id", str(ADMIN_ID), "rules", "42", env_path=env)
    assert result.returncode == 0, result.stderr
    assert "Revoked" in result.stdout
    assert _FakeOrchestrator.last_request["body"] == {"source": "rules", "telegram_id": 42}


def test_show_lists_acl(tmp_path):
    with fake_orchestrator(200, {
        "ok": True,
        "rows": [
            {"source": "rules", "allowed_telegram_ids": [str(ADMIN_ID)], "public": False},
            {"source": "faq", "allowed_telegram_ids": [], "public": True},
        ],
    }) as base:
        env = _write_env(tmp_path, base)
        result = _run("show", "--sender-id", str(ADMIN_ID), "access", env_path=env)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "`rules`" in out
    assert "`faq`" in out
    assert "public" in out
    assert _FakeOrchestrator.last_request["method"] == "GET"
    assert _FakeOrchestrator.last_request["path"] == "/admin/show"


def test_show_with_source_filter(tmp_path):
    with fake_orchestrator(200, {"ok": True, "rows": []}) as base:
        env = _write_env(tmp_path, base)
        result = _run("show", "--sender-id", str(ADMIN_ID), "access", "rules", env_path=env)
    assert result.returncode == 0
    assert _FakeOrchestrator.last_request["path"] == "/admin/show?source=rules"


def test_bookings_with_date(tmp_path):
    with fake_orchestrator(200, {
        "ok": True,
        "events": [
            {
                "id": "A1",
                "subject": "Farm tour",
                "start": "2026-06-25T10:00:00",
                "end": "2026-06-25T12:00:00",
                "location": "Main gate",
            },
        ],
    }) as base:
        env = _write_env(tmp_path, base)
        result = _run("bookings", "--sender-id", str(ADMIN_ID), "2026-06-25", env_path=env)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "Bookings on 2026-06-25" in out
    assert "Farm tour" in out
    assert "A1" in out
    assert "Main gate" in out
    assert _FakeOrchestrator.last_request["path"].startswith("/admin/bookings?")
    assert "date=2026-06-25" in _FakeOrchestrator.last_request["path"]
    assert "tz=Asia%2FSingapore" in _FakeOrchestrator.last_request["path"]


def test_bookings_without_date_prompts(tmp_path):
    with fake_orchestrator() as base:
        env = _write_env(tmp_path, base)
        result = _run("bookings", "--sender-id", str(ADMIN_ID), env_path=env)
    assert result.returncode == 0
    assert "Which date" in result.stdout
    assert _FakeOrchestrator.last_request is None  # prompt, no API call


def test_bookings_rejects_bad_date(tmp_path):
    with fake_orchestrator() as base:
        env = _write_env(tmp_path, base)
        result = _run("bookings", "--sender-id", str(ADMIN_ID), "tomorrow", env_path=env)
    assert result.returncode == 1
    assert "Bad date" in result.stderr
    assert _FakeOrchestrator.last_request is None


def test_config_int_value(tmp_path):
    with fake_orchestrator(200, {"ok": True}) as base:
        env = _write_env(tmp_path, base)
        result = _run("config", "--sender-id", str(ADMIN_ID), "slot_duration_minutes", "90", env_path=env)
    assert result.returncode == 0, result.stderr
    assert _FakeOrchestrator.last_request["method"] == "PATCH"
    assert _FakeOrchestrator.last_request["body"] == {"key": "slot_duration_minutes", "value": 90}


def test_config_list_value(tmp_path):
    with fake_orchestrator(200, {"ok": True}) as base:
        env = _write_env(tmp_path, base)
        result = _run(
            "config", "--sender-id", str(ADMIN_ID),
            "blackout_dates", '["2026-12-25","2026-12-26"]',
            env_path=env,
        )
    assert result.returncode == 0, result.stderr
    assert _FakeOrchestrator.last_request["body"]["value"] == ["2026-12-25", "2026-12-26"]


def test_config_string_value_fallback(tmp_path):
    with fake_orchestrator(200, {"ok": True}) as base:
        env = _write_env(tmp_path, base)
        result = _run("config", "--sender-id", str(ADMIN_ID), "some_str_key", "hello world", env_path=env)
    assert result.returncode == 0
    assert _FakeOrchestrator.last_request["body"]["value"] == "hello world"


def test_config_rejects_unknown_key(tmp_path):
    with fake_orchestrator(400, {"error": "unknown_key", "allowed_keys": ["slot_duration_minutes"]}) as base:
        env = _write_env(tmp_path, base)
        result = _run("config", "--sender-id", str(ADMIN_ID), "location", "Mars", env_path=env)
    assert result.returncode == 1
    assert "unknown_key" in result.stderr
    assert "slot_duration_minutes" in result.stderr


def test_auth_failure_propagates(tmp_path):
    with fake_orchestrator(401, {"error": "bad_token"}) as base:
        env = _write_env(tmp_path, base)
        result = _run("show", "--sender-id", str(ADMIN_ID), "access", env_path=env)
    assert result.returncode == 1
    assert "401" in result.stderr
    assert "bad_token" in result.stderr


def test_missing_sender_id(tmp_path):
    with fake_orchestrator() as base:
        env = _write_env(tmp_path, base)
        result = _run("show", "access", env_path=env)
    assert result.returncode == 2
    assert "--sender-id" in result.stderr


def test_empty_admin_list_refuses_everyone(tmp_path):
    with fake_orchestrator() as base:
        env = tmp_path / ".env"
        env.write_text(
            f"ADMIN_API_BASE={base}\nADMIN_HTTP_TOKEN=x\nADMIN_TELEGRAM_IDS=\n",
            encoding="utf-8",
        )
        result = _run("show", "--sender-id", str(ADMIN_ID), "access", env_path=env)
    assert result.returncode == 1
    assert "ADMIN_TELEGRAM_IDS" in result.stderr


def test_no_env_at_all(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .env in CWD
    with fake_orchestrator() as base:
        env = tmp_path / ".env"  # never written
        # pass a benign PATH-only env
        result = subprocess.run(
            ["python3", str(BIN / "show"), "--sender-id", str(ADMIN_ID), "access"],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(BIN.parent)},
            cwd=str(tmp_path),
            timeout=10,
        )
    assert result.returncode == 1
    assert "ADMIN_API_BASE" in result.stderr
