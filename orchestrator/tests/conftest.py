"""Pytest fixtures for orchestrator v0 tests.

Shared helpers:
- ``tmp_inbox`` — a fresh ``inbox.ndjson`` per test.
- ``tmp_state`` — a fresh SQLite state per test.
- ``mock_bridge_send`` — ``respx``-mocked ``POST /send`` endpoint.
- ``fake_rag_ask`` — monkeypatched ``rag_qdrant.ask`` (we mock via the
  ``src.rag`` module so we don't need rag-qdrant installed).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import respx
from httpx import Response

from src import rag as rag_mod
from src.settings import Settings


@pytest.fixture
def tmp_inbox(tmp_path: Path) -> Path:
    p = tmp_path / "inbox.ndjson"
    p.touch()
    return p


@pytest.fixture
def tmp_state_db(tmp_path: Path) -> Path:
    return tmp_path / "state.sqlite"


@pytest.fixture
def settings(tmp_inbox: Path, tmp_state_db: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("INBOX_PATH", str(tmp_inbox))
    monkeypatch.setenv("ORCHESTRATOR_DB", str(tmp_state_db))
    monkeypatch.setenv("WA_BRIDGE_URL", "http://bridge.test:7788")
    monkeypatch.setenv("WA_BRIDGE_TOKEN", "test-token")
    monkeypatch.setenv("ORCHESTRATOR_PORT", "0")  # ephemeral
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    return Settings.from_env()


@pytest.fixture
def bridge_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def fake_rag_ask(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str]]]:
    """Replace ``src.rag.ask`` so we don't need rag-qdrant installed.

    Yields a list of ``(question, echoed_answer)`` pairs so tests can assert
    the orchestrator forwarded the right question.
    """
    calls: list[tuple[str, str]] = []

    def _fake(question: str) -> str:
        answer = f"echo: {question}"
        calls.append((question, answer))
        return answer

    monkeypatch.setattr(rag_mod, "ask", _fake)
    yield calls


def bridge_send_response() -> Response:
    return Response(200, json={"ok": True, "queued": True})
