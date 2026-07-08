"""Tests for the RAG shim's reasoning-strip (src/rag.py).

Reasoning models (MiniMax-M3) emit a <think>…</think> chain-of-thought in the
answer content; it must never reach a customer.
"""
from __future__ import annotations

import pytest

from src import rag


def test_strip_reasoning_removes_think_block():
    raw = "<think>\nlet me check the context...\n</think>\n\n09:00 SGT, daily."
    assert rag.strip_reasoning(raw) == "09:00 SGT, daily."


def test_strip_reasoning_removes_multiple_and_inline_blocks():
    raw = "<think>a</think>Deposit is SGD 50.<think>b</think>"
    assert rag.strip_reasoning(raw) == "Deposit is SGD 50."


def test_strip_reasoning_handles_unterminated_think():
    # Truncated output: opening tag with no close — drop from the tag on.
    raw = "Here is the answer.<think>reasoning that got cut off"
    assert rag.strip_reasoning(raw) == "Here is the answer."


def test_strip_reasoning_passthrough_when_no_tags():
    assert rag.strip_reasoning("Just a plain answer.") == "Just a plain answer."


def test_ask_strips_reasoning(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "rag_qdrant",
        type("M", (), {"ask": staticmethod(lambda q: {"answer": "<think>x</think>Open at 9."})}),
    )
    assert rag.ask("when do you open?") == "Open at 9."


def test_ask_rejects_bad_shape(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "rag_qdrant",
        type("M", (), {"ask": staticmethod(lambda q: {"no_answer": True})}),
    )
    with pytest.raises(RuntimeError):
        rag.ask("q")
