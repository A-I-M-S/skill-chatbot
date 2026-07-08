"""Thin wrapper over ``rag_qdrant.ask``.

Kept deliberately small so tests can monkeypatch :func:`ask` without mocking
the entire RAG stack. Real Qdrant / inference is out of scope for v0 tests
(per the brief).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Reasoning models (e.g. MiniMax-M3) emit a <think>…</think> chain-of-thought
# in the message content. It must never reach a customer, so strip it before
# returning the answer.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Remove <think>…</think> blocks (and a lone dangling <think>) from LLM text."""
    cleaned = _THINK_RE.sub("", text)
    # Defensive: an unterminated <think> (truncated output) — drop from the tag on.
    if "<think>" in cleaned.lower():
        cleaned = re.split(r"<think>", cleaned, flags=re.IGNORECASE)[0]
    return cleaned.strip()


def ask(question: str) -> str:
    """Call ``rag_qdrant.ask(question)`` and return its ``answer`` string.

    Matches the upstream contract:

        from rag_qdrant import ask as _ask
        result = _ask(question)
        return result["answer"]

    The answer is passed through :func:`strip_reasoning` so a reasoning model's
    <think> block never reaches the customer.
    """
    from rag_qdrant import ask as _rag_ask

    result: Any = _rag_ask(question)
    if not isinstance(result, dict) or "answer" not in result:
        raise RuntimeError(f"rag_qdrant.ask returned unexpected shape: {result!r}")
    return strip_reasoning(str(result["answer"]))


def ask_with_photo(question: str, photo_path: str | None = None) -> str:
    """Photo-aware question (issue #10).

    For v1 we don't have a multimodal model, so this just prepends the photo
    path context (mirroring ``rag_qdrant``'s photo support section) and falls
    through to :func:`ask`. The path is best-effort: the bridge has already
    saved the file under ``<RAG_PHOTOS_DIR>/inbound/<sha>.<ext>``, so a
    follow-up question can be answered from the rag-photos corpus via
    :func:`ask`.

    Tests monkeypatch :func:`ask` so this function is fully covered without
    touching Qdrant. The signature is stable — issue #4's router will call
    this variant when the user message references a photo.
    """
    contextualised = f"[photo at {photo_path}] {question}" if photo_path else question
    return ask(contextualised)


__all__ = ["ask", "ask_with_photo"]
