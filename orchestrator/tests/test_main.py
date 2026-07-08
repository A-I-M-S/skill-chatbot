"""End-to-end smoke: write to inbox -> /send is POSTed with the rag answer."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import httpx
import respx

from src import main as main_mod
from src.state import open_state


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_e2e_message_flows_to_bridge_send(
    tmp_inbox: Path,
    tmp_state_db: Path,
    bridge_mock: respx.MockRouter,
    fake_rag_ask: list[tuple[str, str]],
) -> None:
    """The 'is the loop closed' milestone: drop a line in the inbox, watch the
    bridge's ``/send`` endpoint get hit with the RAG answer."""
    port = _free_port()
    bridge_url = "http://bridge.test:7788"
    bridge_mock.post(f"{bridge_url}/send").mock(return_value=httpx.Response(200, json={"ok": True}))

    settings = main_mod.Settings.from_mapping(
        {
            "inbox_path": tmp_inbox,
            "orchestrator_db": tmp_state_db,
            "orchestrator_port": port,
            "wa_bridge_url": bridge_url,
            "wa_bridge_token": "secret",
            "log_level": "DEBUG",
        }
    )

    stop = threading.Event()
    thread = threading.Thread(
        target=lambda: main_mod.run_forever_until_stopped(settings, stop),
        daemon=True,
    )
    thread.start()

    try:
        with tmp_inbox.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "message_id": "msg-1",
                        "from": "6512345678",
                        "text": "what time is the tour?",
                        "image": None,
                        "timestamp": "2026-06-24T10:00:00Z",
                    }
                )
                + "\n"
            )

        for _ in range(80):
            if bridge_mock.calls:
                break
            time.sleep(0.05)
        assert len(bridge_mock.calls) == 1
        request = bridge_mock.calls[0].request
        assert request.headers.get("authorization") == "Bearer secret"
        body = json.loads(request.content)
        assert body == {"to": "6512345678", "text": "echo: what time is the tour?"}
        assert fake_rag_ask == [("what time is the tour?", "echo: what time is the tour?")]
    finally:
        stop.set()
        thread.join(timeout=5.0)

    with open_state(tmp_state_db) as state:
        assert state.last_processed_message_id() == "msg-1"


def test_e2e_dedupes_duplicate_message(
    tmp_inbox: Path,
    tmp_state_db: Path,
    bridge_mock: respx.MockRouter,
    fake_rag_ask: list[tuple[str, str]],
) -> None:
    port = _free_port()
    bridge_url = "http://bridge.test:7788"
    bridge_mock.post(f"{bridge_url}/send").mock(return_value=httpx.Response(200, json={"ok": True}))

    settings = main_mod.Settings.from_mapping(
        {
            "inbox_path": tmp_inbox,
            "orchestrator_db": tmp_state_db,
            "orchestrator_port": port,
            "wa_bridge_url": bridge_url,
            "wa_bridge_token": "t",
            "log_level": "DEBUG",
        }
    )

    with open_state(tmp_state_db) as pre:
        pre.mark_processed("msg-dup")

    stop = threading.Event()
    thread = threading.Thread(
        target=lambda: main_mod.run_forever_until_stopped(settings, stop),
        daemon=True,
    )
    thread.start()

    try:
        with tmp_inbox.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "message_id": "msg-dup",
                        "from": "6512345678",
                        "text": "should be skipped",
                        "image": None,
                        "timestamp": "t",
                    }
                )
                + "\n"
            )

        time.sleep(1.0)
        assert bridge_mock.calls == []
        assert fake_rag_ask == []
    finally:
        stop.set()
        thread.join(timeout=5.0)

    with open_state(tmp_state_db) as pre:
        assert pre.is_processed("msg-dup") is True


def test_handle_message_short_circuits_when_processed(
    tmp_state_db: Path, fake_rag_ask: list
) -> None:
    """Direct unit test for :func:`handle_message`'s dedupe branch."""
    from src.settings import Settings

    with open_state(tmp_state_db) as state:
        state.mark_processed("already-done")
        settings = Settings.from_mapping(
            {"wa_bridge_url": "http://bridge.test:7788", "wa_bridge_token": "x"}
        )
        client = httpx.Client()
        try:
            main_mod.handle_message(
                state,
                client,
                settings,
                message_id="already-done",
                sender="1",
                text="ignored",
            )
            assert fake_rag_ask == []
        finally:
            client.close()
