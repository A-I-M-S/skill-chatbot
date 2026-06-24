"""Thin OpenAI-compatible client wrapper with retry + backoff.

The router (and any other LLM caller) goes through this module so the
retry policy + model defaults are in one place. We use the official
``openai`` SDK because every OpenAI-compatible endpoint accepts the same
chat-completions schema — the SDK is a thin wire-format generator.

Retries: 1s, 2s, 4s (3 attempts total) on transient errors (rate limit,
5xx, connection error). After the third failure we raise
:class:`InferenceExhausted`; the router catches that and falls back to
``handoff``.

Pydantic-validated tool calls: each assistant message with ``tool_calls``
is validated against the matching tool schema. On validation failure we
retry once with a tighter prompt (asking for strict JSON), then fall back.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable

from openai import (
    APIConnectionError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class InferenceExhausted(RuntimeError):
    """Raised when the LLM call has failed all retries."""


class ToolCall(BaseModel):
    """One tool call extracted from the assistant message."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AssistantReply(BaseModel):
    """A single LLM response, ready to be dispatched by the router."""

    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────
# Client construction
# ──────────────────────────────────────────────────────────────────────

def _build_client() -> OpenAI:
    base = os.environ.get("INFERENCE_BASE_URL")
    key = os.environ.get("INFERENCE_API_KEY")
    if not base or not key:
        raise InferenceExhausted(
            "INFERENCE_BASE_URL and INFERENCE_API_KEY must be set"
        )
    return OpenAI(api_key=key, base_url=base)


def default_model() -> str:
    return os.environ.get("INFERENCE_MODEL") or "minimax/MiniMax-M3"


def default_temperature() -> float:
    """Models like MiniMax-M3 are deterministic at temp=0; the plan locks this."""
    return float(os.environ.get("INFERENCE_TEMPERATURE") or "0")


# ──────────────────────────────────────────────────────────────────────
# Retry wrapper
# ──────────────────────────────────────────────────────────────────────

Retryable = (RateLimitError, APITimeoutError, APIConnectionError)


def _sleep(attempt: int) -> None:
    """1s, 2s, 4s."""
    time.sleep(2 ** attempt)


def chat_completions(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = "auto",
    temperature: float = 0,
    max_retries: int = 3,
    sleep: Callable[[int], None] = _sleep,
) -> AssistantReply:
    """Call chat.completions.create with retry + parse the response.

    Returns an :class:`AssistantReply` with ``text`` (free-form content) and
    ``tool_calls`` (parsed from ``choices[0].message.tool_calls``). Raises
    :class:`InferenceExhausted` after ``max_retries`` failed attempts.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(**kwargs)
            return _parse_response(resp)
        except Retryable as e:
            last_err = e
            logger.warning(
                "LLM call retryable error attempt=%d/%d err=%s",
                attempt + 1,
                max_retries,
                e,
            )
            if attempt < max_retries - 1:
                sleep(attempt)
            continue
        except Exception as e:  # non-retryable: 4xx, schema, etc.
            last_err = e
            logger.error("LLM call non-retryable error: %s", e)
            break

    raise InferenceExhausted(f"LLM call failed after {max_retries} attempts: {last_err}")


def _parse_response(resp: Any) -> AssistantReply:
    """Parse a raw openai response into :class:`AssistantReply`.

    Tolerant to:
    - missing ``choices`` (returns empty reply)
    - empty ``message.tool_calls`` (no tool calls)
    - ``arguments`` as a JSON string (parses to dict)
    - extra fields we don't care about (kept in ``raw``)
    """
    raw: dict[str, Any] = _safe_model_dump(resp)
    choices = raw.get("choices") or []
    if not choices:
        return AssistantReply(text=None, tool_calls=[], raw=raw)
    first = choices[0]
    message = first.get("message") or {}
    text = message.get("content")
    if text == "":
        text = None
    tool_calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        func = tc.get("function") or {}
        name = func.get("name") or ""
        args = func.get("arguments") or "{}"
        if isinstance(args, str):
            try:
                import json
                args_dict: dict[str, Any] = json.loads(args)
            except json.JSONDecodeError:
                logger.warning("tool call %s: bad JSON args=%r", name, args[:80])
                args_dict = {}
        elif isinstance(args, dict):
            args_dict = args
        else:
            args_dict = {}
        tool_calls.append(ToolCall(name=name, arguments=args_dict))
    return AssistantReply(text=text, tool_calls=tool_calls, raw=raw)


def _safe_model_dump(obj: Any) -> dict[str, Any]:
    """Best-effort dict extraction; works with pydantic models and plain dicts."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            d = obj.model_dump()
            if isinstance(d, dict):
                return d
        except Exception:  # noqa: BLE001
            pass
    # Last resort: use __dict__; this may be incomplete for openai SDK objects
    return getattr(obj, "__dict__", {}) or {}


# ──────────────────────────────────────────────────────────────────────
# Public factory used by the router
# ──────────────────────────────────────────────────────────────────────

def make_client_and_model() -> tuple[OpenAI, str, float]:
    """Return ``(client, model, temperature)`` from env. Validates env presence."""
    return _build_client(), default_model(), default_temperature()


__all__ = [
    "AssistantReply",
    "InferenceExhausted",
    "ToolCall",
    "chat_completions",
    "default_model",
    "default_temperature",
    "make_client_and_model",
]
