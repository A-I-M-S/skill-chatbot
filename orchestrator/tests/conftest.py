"""Pytest fixtures for orchestrator v0 tests.

Shared helpers:
- ``tmp_inbox`` — a fresh ``inbox.ndjson`` per test.
- ``tmp_state`` — a fresh SQLite state per test.
- ``mock_bridge_send`` — ``respx``-mocked ``POST /send`` endpoint.
- ``fake_rag_ask`` — monkeypatched ``rag_qdrant.ask`` (we mock via the
  ``src.rag`` module so we don't need rag-qdrant installed).
- ``fake_router`` — manual fixture; replaces ``src.router.route_message``
  with a stub that returns a canned ``RouterDecision`` (default
  ``tool='faq'``; override with ``FAKE_ROUTER_TOOL`` env var).
- ``_stub_router_for_tests_without_real_llm`` — autouse fixture that
  applies the same stub unless the test asks for the manual fixture or
  marks ``@pytest.mark.real_router``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import respx
from httpx import Response

from src import rag as rag_mod
from src import router as router_mod
from src import main as main_mod
from src.settings import Settings


# ──────────────────────────────────────────────────────────────────────
# tmp paths
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_inbox(tmp_path: Path) -> Path:
    p = tmp_path / "inbox.ndjson"
    p.touch()
    return p


@pytest.fixture
def tmp_state_db(tmp_path: Path) -> Path:
    return tmp_path / "state.sqlite"


# ──────────────────────────────────────────────────────────────────────
# settings + bridge mock
# ──────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────
# RAG stub
# ──────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────
# Router stub — single source of truth for the stub factory.
# Both ``fake_router`` (manual) and ``_stub_router_for_tests_without_real_llm``
# (autouse) call this so there's a single line of ``monkeypatch.setattr`` per
# binding — easy to edit, no duplicate text.
# ──────────────────────────────────────────────────────────────────────


def _build_router_stub() -> Callable[..., router_mod.RouterDecision]:
    """Return a fresh ``route_message`` stub function.

    The stub reads ``FAKE_ROUTER_TOOL`` from env at call time (so tests can
    change it per-call via monkeypatch) and returns a ``RouterDecision``
    shaped to match.
    """
    from src.enums import HandoffReason

    def _fake(**kwargs: object) -> router_mod.RouterDecision:
        tool = os.environ.get("FAKE_ROUTER_TOOL", "faq")
        text = str(kwargs.get("user_text", ""))
        language = str(kwargs.get("language", "en"))
        if tool == "faq":
            return router_mod.RouterDecision(
                tool="faq", arguments={"question": text}, language=language
            )
        if tool == "handoff":
            return router_mod.RouterDecision(
                tool="handoff",
                arguments={"reason": HandoffReason.OTHER.value, "summary": text},
                language=language,
            )
        return router_mod.RouterDecision(
            tool=tool, arguments={}, language=language
        )

    return _fake


def _install_router_stub(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Install the router stub on both ``src.router`` and ``src.main``.

    ``src.main`` does ``from .router import route_message``, which creates
    a separate binding — patching ``router_mod`` alone leaves ``main_mod``
    pointing at the real implementation. Patching both is the only way
    to short-circuit the call in tests.

    Returns the calls list (for the manual ``fake_router`` fixture to
    yield; the autouse fixture discards it).
    """
    calls: list[dict[str, object]] = []
    stub = _build_router_stub()
    original = router_mod.route_message

    def _recording(**kwargs: object) -> router_mod.RouterDecision:
        calls.append(kwargs)
        return stub(**kwargs)

    monkeypatch.setattr(router_mod, "route_message", _recording)
    monkeypatch.setattr(main_mod, "route_message", _recording)
    return calls


@pytest.fixture
def fake_router(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, object]]]:
    """Replace ``src.router.route_message`` so we don't need an LLM.

    The stub records every call (caller can assert routing decisions) and
    returns a canned ``RouterDecision(tool='faq', arguments={'question': text})``.
    Set ``FAKE_ROUTER_TOOL=handoff`` (etc.) via monkeypatch to override the
    returned tool.
    """
    calls = _install_router_stub(monkeypatch)
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
    if "fake_router" in request.fixturenames:
        # The test opted in to the manual fixture — don't double-stub.
        yield
        return
    _install_router_stub(monkeypatch)
    yield


# ──────────────────────────────────────────────────────────────────────
# misc
# ──────────────────────────────────────────────────────────────────────


def bridge_send_response() -> Response:
    return Response(200, json={"ok": True, "queued": True})