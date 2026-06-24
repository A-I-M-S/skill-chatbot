"""Thin wrapper over ``rag_qdrant.ask``.

Kept deliberately small so tests can monkeypatch :func:`ask` without mocking
the entire RAG stack. Real Qdrant / inference is out of scope for v0 tests
(per the brief).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def ask(question: str) -> str:
    """Call ``rag_qdrant.ask(question)`` and return its ``answer`` string.

    Matches the upstream contract:

        from rag_qdrant import ask as _ask
        result = _ask(question)
        return result["answer"]
    """
    from rag_qdrant import ask as _rag_ask

    result: Any = _rag_ask(question)
    if not isinstance(result, dict) or "answer" not in result:
        raise RuntimeError(f"rag_qdrant.ask returned unexpected shape: {result!r}")
    return str(result["answer"])


__all__ = ["ask"]
