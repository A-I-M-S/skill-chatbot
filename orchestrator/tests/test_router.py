"""Tests for the LLM router (issue #4).

All tests use a stubbed OpenAI client — no real inference call. The
``FakeChatCompletions`` class below captures the request and returns a
pre-canned response so we can assert the router's behaviour end-to-end
without a network.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest
from openai import OpenAI

from src.enums import Flow, HandoffReason
from src.i18n import (
    STRINGS,
    confirm_prompt,
    system_prompt,
    t,
    tool_descriptions,
)
from src.inference import InferenceExhausted, chat_completions
from src.router import (
    BookCancelArgs,
    BookEditArgs,
    BookNewArgs,
    FaqArgs,
    HandoffArgs,
    RouterDecision,
    _build_tool_schemas,
    _validate_args,
    route_message,
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ──────────────────────────────────────────────────────────────────────
# i18n: every key is present in both languages
# ──────────────────────────────────────────────────────────────────────


def test_i18n_every_key_is_bilingual() -> None:
    en_keys = {k for (lang, k) in STRINGS if lang == "en"}
    zh_keys = {k for (lang, k) in STRINGS if lang == "zh"}
    assert en_keys, "no EN keys"
    assert en_keys == zh_keys, f"missing in ZH: {en_keys - zh_keys}; missing in EN: {zh_keys - en_keys}"


def test_i18n_t_returns_expected_strings() -> None:
    assert "flagged this for the team" in t("handoff_msg", "en", admin_contact="+65XXXX")
    assert "转给团队" in t("handoff_msg", "zh", admin_contact="+65XXXX")
    # Default lang is EN
    assert t("aborted", "") == "OK, no problem."
    assert t("aborted", "ja") == "OK, no problem."
    # Missing key raises
    with pytest.raises(KeyError):
        t("nonsense_key", "en")
    # Missing placeholder raises
    with pytest.raises(KeyError):
        t("handoff_msg", "en")  # no admin_contact kwarg


def test_system_prompt_languages() -> None:
    en = system_prompt("en")
    zh = system_prompt("zh")
    assert "five tools" in en
    assert "五个工具" in zh
    assert en != zh


def test_tool_descriptions_languages() -> None:
    en = tool_descriptions("en")
    zh = tool_descriptions("zh")
    assert "knowledge base" in en["faq"]
    assert "知识库" in zh["faq"]


def test_confirm_prompts_per_flow() -> None:
    assert "Reply **YES**" in confirm_prompt(Flow.BOOK_NEW, "en")
    assert "**YES**" in confirm_prompt(Flow.BOOK_EDIT, "en")
    assert "irreversible" in confirm_prompt(Flow.BOOK_CANCEL, "en")
    assert "回复 **YES**" in confirm_prompt(Flow.BOOK_NEW, "zh")


# ──────────────────────────────────────────────────────────────────────
# Tool schema building
# ──────────────────────────────────────────────────────────────────────


def test_tool_schemas_built_with_five_tools() -> None:
    schemas = _build_tool_schemas("en")
    names = [s["function"]["name"] for s in schemas]
    assert names == ["faq", "book_new", "book_edit", "book_cancel", "handoff"]


def test_tool_schemas_have_required_fields() -> None:
    schemas = _build_tool_schemas("en")
    by_name = {s["function"]["name"]: s for s in schemas}
    # faq: question required
    assert "question" in by_name["faq"]["function"]["parameters"]["required"]
    # handoff: reason + summary required
    h = by_name["handoff"]["function"]["parameters"]
    assert "reason" in h["required"]
    assert "summary" in h["required"]
    # handoff reason enum has all 5 values
    enum = h["properties"]["reason"]["enum"]
    assert set(enum) == {r.value for r in HandoffReason}


# ──────────────────────────────────────────────────────────────────────
# Schema validation
# ──────────────────────────────────────────────────────────────────────


def test_validate_args_strips_none_and_validates_types() -> None:
    cleaned = _validate_args("book_new", {"date": "2026-08-15", "pax": 30})
    assert cleaned == {"date": "2026-08-15", "pax": 30}

    # None values are stripped (orchestrator state stores partial drafts)
    cleaned = _validate_args("book_new", {"date": "2026-08-15", "time": None})
    assert cleaned == {"date": "2026-08-15"}


def test_validate_args_rejects_bad_pax() -> None:
    assert _validate_args("book_new", {"pax": "thirty"}) is None
    assert _validate_args("book_new", {"pax": 0}) is None
    assert _validate_args("book_new", {"pax": 501}) is None


def test_validate_args_rejects_bad_handoff_reason() -> None:
    assert _validate_args("handoff", {"reason": "spam", "summary": "x"}) is None
    cleaned = _validate_args("handoff", {"reason": "refund", "summary": "I want a refund"})
    assert cleaned == {"reason": "refund", "summary": "I want a refund"}


def test_validate_args_unknown_tool() -> None:
    assert _validate_args("not_a_tool", {}) is None


# ──────────────────────────────────────────────────────────────────────
# Stubbed OpenAI client — drives route_message end-to-end
# ──────────────────────────────────────────────────────────────────────


class FakeMessage:
    """Plain object that exposes dict-like access via __getattr__ shim.

    The router calls ``.get(...)`` on nested fields after
    ``_safe_model_dump`` returns the raw dict; we just return a real
    dict so the parsing path works without further changes.
    """


def _fake_response_dict(tool_calls: list[dict[str, Any]] | None, text: str | None = None) -> dict[str, Any]:
    """Build a response in the dict shape _safe_model_dump would return."""
    return {
        "choices": [
            {
                "message": {
                    "content": text,
                    "tool_calls": tool_calls or [],
                }
            }
        ]
    }


def _tool_call_dict(name: str, args: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _stub_client(reply: dict[str, Any], captures: list[dict[str, Any]] | None = None) -> OpenAI:
    """Return a fake OpenAI client whose chat.completions.create returns ``reply``."""
    class _StubCompletions:
        def create(self, **kwargs: Any) -> Any:
            if captures is not None:
                captures.append(kwargs)
            return reply

    class _StubChat:
        completions = _StubCompletions()

    class _StubClient:
        chat = _StubChat()

    return _StubClient()  # type: ignore[return-value]


# ── Happy paths ────────────────────────────────────────────────────────


def test_route_faq_tool_call_en() -> None:
    reply = _fake_response_dict([
        _tool_call_dict("faq", {"question": "what time do you open?"}),
    ])
    decision = route_message(
        user_text="what time do you open?",
        language="en",
        client=_stub_client(reply),
        model="m", temperature=0,
    )
    assert decision.tool == "faq"
    assert decision.arguments == {"question": "what time do you open?"}
    assert decision.fallback is False
    assert decision.language == "en"


def test_route_book_new_partial_fields() -> None:
    reply = _fake_response_dict([
        _tool_call_dict("book_new", {"date": "2026-08-15", "pax": 30}),
    ])
    decision = route_message(
        user_text="30 kids on 15 Aug",
        language="en",
        client=_stub_client(reply),
        model="m", temperature=0,
    )
    assert decision.tool == "book_new"
    assert decision.arguments == {"date": "2026-08-15", "pax": 30}


def test_route_book_edit() -> None:
    reply = _fake_response_dict([
        _tool_call_dict("book_edit", {"event_id": "EVT123", "time": "14:30"}),
    ])
    decision = route_message(
        user_text="change to 14:30",
        language="en",
        client=_stub_client(reply),
        model="m", temperature=0,
    )
    assert decision.tool == "book_edit"
    assert decision.arguments == {"event_id": "EVT123", "time": "14:30"}


def test_route_book_cancel() -> None:
    reply = _fake_response_dict([
        _tool_call_dict("book_cancel", {"event_id": "EVT123"}),
    ])
    decision = route_message(
        user_text="please cancel",
        language="en",
        client=_stub_client(reply),
        model="m", temperature=0,
    )
    assert decision.tool == "book_cancel"
    assert decision.arguments == {"event_id": "EVT123"}


def test_route_handoff_refund() -> None:
    reply = _fake_response_dict([
        _tool_call_dict("handoff", {"reason": "refund", "summary": "I want a refund"}),
    ])
    decision = route_message(
        user_text="I want a refund",
        language="en",
        client=_stub_client(reply),
        model="m", temperature=0,
    )
    assert decision.tool == "handoff"
    assert decision.arguments == {"reason": "refund", "summary": "I want a refund"}


def test_route_chinese_system_prompt_used() -> None:
    captures: list[dict[str, Any]] = []
    reply = _fake_response_dict([
        _tool_call_dict("faq", {"question": "几点开门？"}),
    ])
    decision = route_message(
        user_text="几点开门？",
        language="zh",
        client=_stub_client(reply, captures),
        model="m", temperature=0,
    )
    assert decision.tool == "faq"
    # System prompt sent to the model is the ZH one
    msgs = captures[0]["messages"]
    assert "简体中文" in msgs[0]["content"] or "五个工具" in msgs[0]["content"]


# ── Edge cases / guardrails ────────────────────────────────────────────


def test_route_unknown_tool_falls_back_to_handoff() -> None:
    reply = _fake_response_dict([
        _tool_call_dict("send_email", {"to": "x@y"}),
    ])
    decision = route_message(
        user_text="email this",
        language="en",
        client=_stub_client(reply),
        model="m", temperature=0,
    )
    assert decision.tool == "handoff"
    assert decision.arguments["reason"] == "other"
    assert "send_email" in decision.arguments["summary"]
    assert decision.fallback is True


def test_route_schema_violation_falls_back_to_handoff() -> None:
    reply = _fake_response_dict([
        _tool_call_dict("book_new", {"pax": "thirty"}),  # wrong type
    ])
    decision = route_message(
        user_text="book for thirty people",
        language="en",
        client=_stub_client(reply),
        model="m", temperature=0,
    )
    assert decision.tool == "handoff"
    assert decision.fallback is True


def test_route_no_tool_call_returns_faq_with_text() -> None:
    """If the model returns free text (no tool call), the model's text is
    used as the question and the user's text is preserved as a fallback."""
    reply = _fake_response_dict([], text="Sure, here you go...")
    decision = route_message(
        user_text="hi there",
        language="en",
        client=_stub_client(reply),
        model="m", temperature=0,
    )
    assert decision.tool == "faq"
    # When the model has nothing to say, the user's text is the question.
    assert decision.arguments["question"] == "hi there"


def test_route_image_includes_photo_context() -> None:
    captures: list[dict[str, Any]] = []
    reply = _fake_response_dict([
        _tool_call_dict("faq", {"question": "what breed?"}),
    ])
    decision = route_message(
        user_text="what breed?",
        image={"path": "/p/cow.jpg", "sha256": "x" * 32, "filename": "cow.jpg"},
        language="en",
        client=_stub_client(reply, captures),
        model="m", temperature=0,
    )
    assert decision.tool == "faq"
    msgs = captures[0]["messages"]
    user_content = msgs[-1]["content"]
    assert "I have a photo at /p/cow.jpg from this chat." in user_content
    assert "what breed?" in user_content


def test_route_image_includes_photo_context_zh() -> None:
    captures: list[dict[str, Any]] = []
    reply = _fake_response_dict([
        _tool_call_dict("faq", {"question": "什么品种？"}),
    ])
    decision = route_message(
        user_text="什么品种？",
        image={"path": "/p/cow.jpg", "sha256": "x" * 32, "filename": "cow.jpg"},
        language="zh",
        client=_stub_client(reply, captures),
        model="m", temperature=0,
    )
    assert decision.tool == "faq"
    msgs = captures[0]["messages"]
    user_content = msgs[-1]["content"]
    assert "我这边有一张图片" in user_content


def test_route_inference_exhausted_falls_back_to_handoff() -> None:
    """When the LLM call has failed all retries, return handoff(other)."""
    from openai import APITimeoutError

    class _Exploder:
        class chat:
            class completions:
                @staticmethod
                def create(**_: Any) -> Any:
                    raise APITimeoutError("simulated")

    decision = route_message(
        user_text="hi",
        language="en",
        client=_Exploder(),  # type: ignore[arg-type]
        model="m", temperature=0,
    )
    assert decision.tool == "handoff"
    assert decision.arguments["reason"] == "other"
    assert decision.fallback is True


def test_route_history_is_appended() -> None:
    captures: list[dict[str, Any]] = []
    reply = _fake_response_dict([
        _tool_call_dict("faq", {"question": "and on Sunday?"}),
    ])
    history = [
        {"role": "user", "content": "what time do you open?"},
        {"role": "assistant", "content": "9 to 5 weekdays."},
    ]
    decision = route_message(
        user_text="and on Sunday?",
        language="en",
        history=history,
        client=_stub_client(reply, captures),
        model="m", temperature=0,
    )
    assert decision.tool == "faq"
    msgs = captures[0]["messages"]
    # system + 2 history + 1 user = 4
    assert len(msgs) == 4
    assert msgs[1]["role"] == "user"
    assert "what time do you open?" in msgs[1]["content"]
    assert msgs[2]["role"] == "assistant"


# ──────────────────────────────────────────────────────────────────────
# Argument model direct tests
# ──────────────────────────────────────────────────────────────────────


def test_book_new_args_partial_ok() -> None:
    args = BookNewArgs.model_validate({"date": "2026-08-15", "pax": 30})
    assert args.date == "2026-08-15"
    assert args.pax == 30
    assert args.contact_name is None


def test_book_edit_args_rejects_contact_changes() -> None:
    """book_edit schema only allows event_id/date/time/pax."""
    # contact_name is not in BookEditArgs, so it gets ignored (or rejected)
    args = BookEditArgs.model_validate({"event_id": "X", "contact_name": "ignored"})
    assert args.event_id == "X"
    assert not hasattr(args, "contact_name") or args.contact_name is None


def test_faq_args_requires_question() -> None:
    with pytest.raises(Exception):
        FaqArgs.model_validate({})


def test_handoff_args_requires_reason_and_summary() -> None:
    with pytest.raises(Exception):
        HandoffArgs.model_validate({"reason": "refund"})


def test_book_cancel_args_optional_event_id() -> None:
    """event_id is optional in the schema — the orchestrator fills it in
    after the user picks an event from the list."""
    args = BookCancelArgs.model_validate({})
    assert args.event_id is None
