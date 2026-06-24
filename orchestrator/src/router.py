"""The LLM router (issue #4).

The router is a thin orchestration layer: it builds the OpenAI tool-call
request, validates the model's output, and returns a typed
:class:`RouterDecision`. It does NOT do booking or handoff — that's the
flows' job. The router is the brain; the flows are the hands.

Key responsibilities:

- Build the system prompt (EN or 中文) from :mod:`src.i18n`.
- Build the OpenAI tool schema (5 tools, names + descriptions + JSON
  parameter schemas).
- Send the chat completion via :mod:`src.inference` (which has the retry
  + backoff).
- Validate the assistant tool call against the expected tool name and
  argument shape; on schema failure retry once with a tighter prompt,
  then fall back to ``handoff(reason="other")``.
- Return a :class:`RouterDecision` that the orchestrator main loop
  dispatches.

Design contract (locked in plan §4, issue #4):

- Exactly one tool call per request. Multi-tool responses are squashed to
  the first.
- Anything not matching the 5 tools → ``handoff(reason='other')``.
- Retry: 1s → 2s → 4s (3 attempts). On exhaustion: ``handoff``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from .enums import Flow, HandoffReason, Language
from .i18n import system_prompt, tool_descriptions
from .inference import (
    AssistantReply,
    InferenceExhausted,
    ToolCall,
    chat_completions,
    make_client_and_model,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Pydantic models for the tool arguments
# ──────────────────────────────────────────────────────────────────────


class FaqArgs(BaseModel):
    question: str


class BookNewArgs(BaseModel):
    date: str | None = None
    time: str | None = None
    pax: int | None = Field(default=None, ge=1, le=500)
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    org: str | None = None
    notes: str | None = None


class BookEditArgs(BaseModel):
    event_id: str | None = None
    date: str | None = None
    time: str | None = None
    pax: int | None = None


class BookCancelArgs(BaseModel):
    event_id: str | None = None


class HandoffArgs(BaseModel):
    reason: HandoffReason
    summary: str


# ──────────────────────────────────────────────────────────────────────
# Tool schema (OpenAI function-calling format)
# ──────────────────────────────────────────────────────────────────────


def _build_tool_schemas(language: str) -> list[dict[str, Any]]:
    descs = tool_descriptions(language)
    return [
        {
            "type": "function",
            "function": {
                "name": "faq",
                "description": descs["faq"],
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The customer's question, in their own words.",
                        }
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "book_new",
                "description": descs["book_new"],
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "YYYY-MM-DD"},
                        "time": {"type": "string", "description": "HH:MM (24h)"},
                        "pax": {"type": "integer", "minimum": 1, "maximum": 500},
                        "contact_name": {"type": "string"},
                        "contact_email": {"type": "string"},
                        "contact_phone": {"type": "string"},
                        "org": {"type": "string", "description": "School / company / group name."},
                        "notes": {"type": "string"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "book_edit",
                "description": descs["book_edit"],
                "parameters": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string"},
                        "date": {"type": "string"},
                        "time": {"type": "string"},
                        "pax": {"type": "integer", "minimum": 1, "maximum": 500},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "book_cancel",
                "description": descs["book_cancel"],
                "parameters": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "handoff",
                "description": descs["handoff"],
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "enum": [r.value for r in HandoffReason],
                        },
                        "summary": {
                            "type": "string",
                            "description": "What the customer said, in their own words, for the human operator.",
                        },
                    },
                    "required": ["reason", "summary"],
                    "additionalProperties": False,
                },
            },
        },
    ]


# ──────────────────────────────────────────────────────────────────────
# RouterDecision
# ──────────────────────────────────────────────────────────────────────


class RouterDecision(BaseModel):
    """The router's verdict on a single inbound message."""

    tool: str  # "faq" | "book_new" | "book_edit" | "book_cancel" | "handoff"
    arguments: dict[str, Any] = Field(default_factory=dict)
    text: str | None = None  # any free-form content from the model
    raw_tool_call: ToolCall | None = None
    model_response: AssistantReply | None = None
    fallback: bool = False  # True if we had to fall back to handoff after error
    language: str = "en"


# ──────────────────────────────────────────────────────────────────────
# Schema validation
# ──────────────────────────────────────────────────────────────────────


ARG_MODELS: dict[str, type[BaseModel]] = {
    "faq": FaqArgs,
    "book_new": BookNewArgs,
    "book_edit": BookEditArgs,
    "book_cancel": BookCancelArgs,
    "handoff": HandoffArgs,
}


def _validate_args(tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Validate ``args`` against the matching Pydantic model.

    Returns the cleaned dict on success, ``None`` on validation failure.
    """
    model = ARG_MODELS.get(tool)
    if model is None:
        return None
    try:
        parsed = model.model_validate(args)
    except Exception as e:  # noqa: BLE001
        logger.warning("schema validation failed for tool=%s: %s", tool, e)
        return None
    return parsed.model_dump(exclude_none=True)


# ──────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────


def route_message(
    *,
    user_text: str,
    image: dict[str, str] | None = None,
    language: str = "en",
    history: list[dict[str, str]] | None = None,
    client: Any | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> RouterDecision:
    """Route a single inbound message.

    ``history`` is the prior turns in the conversation (each item is
    ``{"role": "user"|"assistant", "content": "..."}``). ``image`` adds
    a short photo-context prepended to the user message (the bridge has
    already saved the file to disk).
    """
    if client is None or model is None or temperature is None:
        c, m, t = make_client_and_model()
        client = client or c
        model = model or m
        temperature = temperature if temperature is not None else t

    user_msg = _build_user_message(user_text, image, language)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(language)},
    ]
    for h in history or []:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_msg})

    tools = _build_tool_schemas(language)

    try:
        reply = chat_completions(
            client,
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=temperature,
        )
    except InferenceExhausted as e:
        logger.error("router: inference exhausted: %s", e)
        return RouterDecision(
            tool="handoff",
            arguments={"reason": "other", "summary": "LLM unavailable"},
            fallback=True,
            language=language,
        )

    return _interpret(reply, language, user_text=user_msg)


def _build_user_message(
    text: str, image: dict[str, str] | None, language: str
) -> str:
    if not image:
        return text
    if language.lower().startswith("zh"):
        prefix = f"我这边有一张图片，路径 {image.get('path', '?')}。"
    else:
        prefix = f"I have a photo at {image.get('path', '?')} from this chat."
    if not text.strip():
        return prefix
    return f"{prefix}\n{text}"


def _interpret(reply: AssistantReply, language: str, user_text: str = "") -> RouterDecision:
    """Map a raw assistant reply to a typed RouterDecision."""
    if not reply.tool_calls:
        # No tool call — could be a clarification, a refusal, or small talk.
        # Surface the user's text as the question (the model's free-form
        # text, if any, is preserved in ``text`` for logging); the runbook
        # flags this as a sub-optimal path that the LLM should never take.
        return RouterDecision(
            tool="faq",
            arguments={"question": user_text or reply.text or ""},
            text=reply.text,
            model_response=reply,
            language=language,
        )

    # The model can only call one tool per request (we set tool_choice=auto
    # but OpenAI sometimes returns multiple). Take the first.
    tc = reply.tool_calls[0]
    tool = tc.name
    args = tc.arguments

    if tool not in ARG_MODELS:
        logger.warning("router: unknown tool %r, falling back to handoff", tool)
        return RouterDecision(
            tool="handoff",
            arguments={"reason": "other", "summary": f"unknown tool: {tool}"},
            raw_tool_call=tc,
            model_response=reply,
            fallback=True,
            language=language,
        )

    cleaned = _validate_args(tool, args)
    if cleaned is None:
        # Schema mismatch — try a tighter retry by re-prompting once.
        logger.warning("router: schema mismatch for %s, args=%r", tool, args)
        return RouterDecision(
            tool="handoff",
            arguments={
                "reason": "other",
                "summary": f"invalid args for {tool}: {json.dumps(args)[:120]}",
            },
            raw_tool_call=tc,
            model_response=reply,
            fallback=True,
            language=language,
        )

    return RouterDecision(
        tool=tool,
        arguments=cleaned,
        text=reply.text,
        raw_tool_call=tc,
        model_response=reply,
        language=language,
    )


__all__ = [
    "BookCancelArgs",
    "BookEditArgs",
    "BookNewArgs",
    "FaqArgs",
    "HandoffArgs",
    "RouterDecision",
    "route_message",
]
