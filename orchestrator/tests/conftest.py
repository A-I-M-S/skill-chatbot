"""Pytest fixtures for orchestrator v0 tests.

Shared helpers:
- ``tmp_inbox`` — a fresh ``inbox.ndjson`` per test.
- ``tmp_state`` — a fresh SQLite state per test.
- ``mock_bridge_send`` — ``respx``-mocked ``POST /send`` endpoint.
- ``fake_rag_ask`` — monkeypatched ``rag_qdrant.ask`` (we mock via the
  ``src.rag`` module so we don't need rag-qdrant installed).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import respx
from httpx import Response

from src import rag as rag_mod
from src import router as router_mod
from src import main as main_mod
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


@pytest.fixture
def fake_router(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, object]]]:
    """Replace ``src.router.route_message`` so we don't need an LLM.

    The stub records every call (caller can assert routing decisions) and
    returns a canned ``RouterDecision(tool='faq', arguments={'question': text})``.
    Set ``FAKE_ROUTER_TOOL=handoff`` (etc.) via monkeypatch to override the
    returned tool.
    """
    calls: list[dict[str, object]] = []

    def _fake(**kwargs: object) -> router_mod.RouterDecision:
        from src.enums import HandoffReason

        calls.append(kwargs)
        tool = os.environ.get("FAKE_ROUTER_TOOL", "faq")
        text = str(kwargs.get("user_text", ""))
        if tool == "faq":
            return router_mod.RouterDecision(
                tool="faq", arguments={"question": text}, language=str(kwargs.get("language", "en"))
            )
        if tool == "handoff":
            return router_mod.RouterDecision(
                tool="handoff",
                arguments={"reason": HandoffReason.OTHER.value, "summary": text},
                language=str(kwargs.get("language", "en")),
            )
        return router_mod.RouterDecision(
            tool=tool, arguments={}, language=str(kwargs.get("language", "en"))
        )

    monkeypatch.setattr(router_mod, "route_message", _fake)
    monkeypatch.setattr(main_mod, "route_message", _fake)
    yield calls


@pytest.fixture(autouse=True)
def _stub_router_for_tests_without_real_llm(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Auto-stub the router unless the test asks for a real one.

    Tests that want to drive the router themselves should set
    ``@pytest.mark.real_router`` and provide their own stub via the
    ``fake_router`` fixture (or inject a custom ``client``).
    """
    if "real_router" in request.keywords:
        yield
        return
    # Also skip if the test already requested a fake_router manually.
    if "fake_router" in request.fixturenames:
        yield
        return
    calls: list[dict[str, object]] = []

    def _fake(**kwargs: object) -> router_mod.RouterDecision:
        from src.enums import HandoffReason

        calls.append(kwargs)
        tool = os.environ.get("FAKE_ROUTER_TOOL", "faq")
        text = str(kwargs.get("user_text", ""))
        if tool == "faq":
            return router_mod.RouterDecision(
                tool="faq",
                arguments={"question": text},
                language=str(kwargs.get("language", "en")),
            )
        if tool == "handoff":
            return router_mod.RouterDecision(
                tool="handoff",
                arguments={"reason": HandoffReason.OTHER.value, "summary": text},
                language=str(kwargs.get("language", "en")),
            )
        return router_mod.RouterDecision(
            tool=tool, arguments={}, language=str(kwargs.get("language", "en"))
        )

    monkeypatch.setattr(router_mod, "route_message", _fake)
    monkeypatch.setattr(main_mod, "route_message", _fake)
    yield


def bridge_send_response() -> Response:
    return Response(200, json={"ok": True, "queued": True})
