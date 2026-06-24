"""Bilingual (EN + 中文) system prompts and tool schemas for the router.

Two prompt variants — one per language — and a single ``pick()`` helper that
returns the right one. The flow files import ``t()`` for any user-facing
string they need; that helper enforces "every string has both languages"
at call time (raises ``KeyError`` if a key is missing in either language).
"""

from __future__ import annotations

from typing import Any, Callable

from .enums import Flow, HandoffReason, Language

# ──────────────────────────────────────────────────────────────────────
# System prompts
# ──────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_EN = """\
You are the WhatsApp assistant for SAAC FARM tour bookings. You reply in \
English. Be brief, friendly, and only answer from the knowledge base or the \
booking tools provided.

You have five tools:

- ``faq(question)`` — answer a factual question from the knowledge base
  (opening hours, location, what to expect, pricing, etc.).
- ``book_new(date?, time?, pax?, contact_name?, contact_email?, \
contact_phone?, org?, notes?)`` — start or continue a new booking. All \
fields are optional in the function call; the orchestrator will ask the \
customer one field at a time over multiple turns.
- ``book_edit(event_id?, date?, time?, pax?)`` — edit an existing booking. \
The orchestrator looks up the customer's events by phone and shows them \
to the user; the LLM passes back the chosen ``event_id`` and the fields \
to change.
- ``book_cancel(event_id?)`` — cancel an existing booking. Same lookup \
behaviour as ``book_edit``.
- ``handoff(reason, summary)`` — escalate to a human. Use this for refunds, \
complaints, custom pricing, abuse, or anything outside the four tools \
above.

Rules:

1. Always call exactly one tool. Never reply with free text when a tool \
fits. If nothing fits, call ``handoff``.
2. Keep the user's language. If the user writes in Chinese, call \
``handoff`` with the Chinese transcript in the summary and let the human \
take over; you yourself only answer in English.
3. Never confirm a destructive action (``book_edit`` / ``book_cancel``) \
without an explicit user ``YES`` reply to a confirmation prompt. \
Destructive ops must include an ``event_id`` once the user has chosen one.
4. Never invent prices, availability, or calendar events. If the tools \
can't answer, hand off.
"""

SYSTEM_PROMPT_ZH = """\
你是 SAAC FARM 参观预约的 WhatsApp 助手。请用简体中文回复。回答要简洁、友好，\
只能根据知识库或提供的预约工具来回答。

你有五个工具：

- ``faq(question)`` — 从知识库回答事实性问题（开放时间、地址、参观内容、价格等）。
- ``book_new(date?, time?, pax?, contact_name?, contact_email?, \
contact_phone?, org?, notes?)`` — 发起或继续新的预约。函数调用里的字段\
都是可选的；orchestrator 会在多轮对话中一次向客户问一个字段。
- ``book_edit(event_id?, date?, time?, pax?)`` — 修改已有预约。\
orchestrator 会按手机号查找客户的预约并展示给用户；LLM 在用户选好后回传\
``event_id`` 和要修改的字段。
- ``book_cancel(event_id?)`` — 取消已有预约。查找逻辑与 ``book_edit`` 相同。
- ``handoff(reason, summary)`` — 转人工。退款、投诉、定制价格、辱骂，\
以及其他不在上面四个工具范围内的情况都用它。

规则：

1. 每次必须正好调用一个工具。工具能解决时不要用自由文本回复。如果都不适合，\
调用 ``handoff``。
2. 保持用户语言。用户用中文写就直接 ``handoff`` 并在 summary 里附上中文原文，\
交由人工处理；你只用中文回复。
3. 任何破坏性操作（``book_edit`` / ``book_cancel``）必须等用户对确认提示\
明确回复 ``YES`` 才能执行。用户选定后破坏性操作必须带 ``event_id``。
4. 不要凭空捏造价格、可用性或日历事件。工具查不到就 handoff。
"""

# ──────────────────────────────────────────────────────────────────────
# Tool descriptions (per language). Sent as part of the OpenAI tool schema.
# ──────────────────────────────────────────────────────────────────────

TOOL_DESC_EN: dict[str, str] = {
    "faq": "Answer a factual question from the SAAC FARM knowledge base.",
    "book_new": "Start or continue a new farm tour booking.",
    "book_edit": "Edit an existing booking (date, time, or pax only).",
    "book_cancel": "Cancel an existing booking.",
    "handoff": "Escalate to a human operator (refund, complaint, custom pricing, abuse, or anything outside the four booking tools).",
}

TOOL_DESC_ZH: dict[str, str] = {
    "faq": "从 SAAC FARM 知识库回答事实性问题。",
    "book_new": "发起或继续新的参观预约。",
    "book_edit": "修改已有预约（仅日期、时间、人数）。",
    "book_cancel": "取消已有预约。",
    "handoff": "转接人工（退款、投诉、定制价格、辱骂，或不在上述四个工具范围内的任何情况）。",
}

# ──────────────────────────────────────────────────────────────────────
# Per-flow confirmation prompts (the message the bot sends before a YES)
# ──────────────────────────────────────────────────────────────────────

CONFIRM_BOOK_NEW_EN = (
    "Here's what I'll book: **{date} {time}, {pax} pax, contact {contact_name} "
    "<{contact_email_or_phone}>**. Reply **YES** to confirm or **NO** to cancel."
)
CONFIRM_BOOK_NEW_ZH = (
    "我将为您预约：**{date} {time}，{pax} 位，联系人 {contact_name} "
    "<{contact_email_or_phone}>**。回复 **YES** 确认，回复 **NO** 取消。"
)

CONFIRM_BOOK_EDIT_EN = (
    "I'll change **{old_summary}** → **{new_summary}**. Reply **YES** to confirm."
)
CONFIRM_BOOK_EDIT_ZH = (
    "我将把 **{old_summary}** 改为 **{new_summary}**。回复 **YES** 确认。"
)

CONFIRM_BOOK_CANCEL_EN = (
    "I'll cancel **{event_summary}**. This is irreversible. Reply **YES** to confirm, "
    "or anything else to keep the booking."
)
CONFIRM_BOOK_CANCEL_ZH = (
    "我将取消 **{event_summary}**。此操作不可撤销。回复 **YES** 确认，"
    "或回复其他内容保留预约。"
)

# ──────────────────────────────────────────────────────────────────────
# Question prompts (per-flow "I need X" messages)
# ──────────────────────────────────────────────────────────────────────

ASK_NEXT_EN = "What {field} would you like?"
ASK_NEXT_ZH = "请问 {field_zh} 是？"

# ──────────────────────────────────────────────────────────────────────
# Static UI strings
# ──────────────────────────────────────────────────────────────────────

STRINGS: dict[tuple[str, str], str] = {
    # (lang, key)
    ("en", "edit_no_events"): "I don't see any upcoming bookings on this number. Want me to check a different one, or hand this over to the team?",
    ("zh", "edit_no_events"): "我这边查不到这个手机号下的即将到来的预约。需要查询其他号码，还是把这件事转给团队？",
    ("en", "edit_pick_event"): "I found a few bookings on your number — which one? Reply with the number:\n{events}",
    ("zh", "edit_pick_event"): "我在这个手机号下找到几个预约——请回复编号：\n{events}",
    ("en", "edit_one_match"): "I found this booking on your number: {event}. Reply YES to edit, or NO to leave it as-is.",
    ("zh", "edit_one_match"): "我在这个手机号下找到这个预约：{event}。回复 YES 修改，回复 NO 保持不变。",
    ("en", "handoff_msg"): "I've flagged this for the team — they'll be in touch shortly. For immediate help, contact {admin_contact}.",
    ("zh", "handoff_msg"): "已经转给团队处理，他们会尽快联系您。如需紧急协助，请拨打 {admin_contact}。",
    ("en", "abusive_msg"): "I'm not able to help with that. The team has been notified.",
    ("zh", "abusive_msg"): "抱歉，我无法处理。已通知团队。",
    ("en", "yes_received_booked"): "Booked. Confirmation #{event_id}.",
    ("zh", "yes_received_booked"): "已预约。编号 #{event_id}。",
    ("en", "yes_received_edited"): "Updated. New confirmation #{event_id}.",
    ("zh", "yes_received_edited"): "已修改。新编号 #{event_id}。",
    ("en", "yes_received_cancelled"): "Cancelled. Confirmation #{event_id}.",
    ("zh", "yes_received_cancelled"): "已取消。编号 #{event_id}。",
    ("en", "aborted"): "OK, no problem.",
    ("zh", "aborted"): "好的，没问题。",
    # ── book_new per-field asks (#9) ─────────────────────────────────
    ("en", "ask_date"): "What date would you like? (YYYY-MM-DD)",
    ("zh", "ask_date"): "请问您想预约哪一天？（YYYY-MM-DD）",
    ("en", "ask_time"): "What time? (HH:MM, 24-hour)",
    ("zh", "ask_time"): "请问几点？（24 小时制，HH:MM）",
    ("en", "ask_pax"): "How many people?",
    ("zh", "ask_pax"): "请问有多少人？",
    ("en", "ask_contact_name"): "Contact name?",
    ("zh", "ask_contact_name"): "联系人姓名？",
    ("en", "ask_contact"): "Contact email or phone? (at least one)",
    ("zh", "ask_contact"): "联系邮箱或电话？（至少填一个）",
    ("en", "ask_org"): "Organisation or school? (optional — type 'skip' to omit)",
    ("zh", "ask_org"): "学校或单位？（可选 — 输入「跳过」即可）",
    ("en", "ask_notes"): "Any notes? (optional — type 'skip' to omit)",
    ("zh", "ask_notes"): "备注？（可选 — 输入「跳过」即可）",
    # ── book_edit confirm + field ask (#9) ────────────────────────────
    ("en", "edit_confirm"): "I'll change **{old}** → **{new}**. Reply **YES** to confirm.",
    ("zh", "edit_confirm"): "我将把 **{old}** 改为 **{new}**。回复 **YES** 确认。",
    ("en", "edit_ask_field"): "Which would you like to change — date, time, or pax?",
    ("zh", "edit_ask_field"): "您想修改哪一项？日期、时间，还是人数？",
    # ── book_cancel confirm (#9) ──────────────────────────────────────
    ("en", "cancel_confirm"): "I'll cancel **{event_summary}**. This is irreversible. Reply **YES** to confirm, or anything else to keep the booking.",
    ("zh", "cancel_confirm"): "我将取消 **{event_summary}**。此操作不可撤销。回复 **YES** 确认，或回复其他内容保留预约。",
}


def t(key: str, language: str, **kwargs: Any) -> str:
    """Translate ``key`` for ``language`` and format with ``kwargs``.

    Raises ``KeyError`` if the key is missing in either language — the
    test suite enforces that every key is bilingual. Empty / default
    language is EN. Locale tags like ``zh-CN`` / ``zh-TW`` are
    normalised to ``zh``.
    """
    lang = (language or "en").lower()
    if lang.startswith("zh"):
        lang = "zh"
    else:
        lang = "en"
    s = STRINGS.get((lang, key))
    if s is None:
        raise KeyError(f"missing i18n key ({lang}, {key!r})")
    try:
        return s.format(**kwargs)
    except KeyError as exc:
        raise KeyError(
            f"i18n key ({lang}, {key!r}) missing placeholder {exc.args[0]!r}"
        ) from exc


def system_prompt(language: str) -> str:
    lang = (language or "en").lower()
    if lang.startswith("zh"):
        return SYSTEM_PROMPT_ZH
    return SYSTEM_PROMPT_EN


def tool_descriptions(language: str) -> dict[str, str]:
    lang = (language or "en").lower()
    if lang.startswith("zh"):
        return TOOL_DESC_ZH
    return TOOL_DESC_EN


def confirm_prompt(flow: Flow, language: str) -> str:
    if flow == Flow.BOOK_NEW:
        return CONFIRM_BOOK_NEW_ZH if language.startswith("zh") else CONFIRM_BOOK_NEW_EN
    if flow == Flow.BOOK_EDIT:
        return CONFIRM_BOOK_EDIT_ZH if language.startswith("zh") else CONFIRM_BOOK_EDIT_EN
    if flow == Flow.BOOK_CANCEL:
        return CONFIRM_BOOK_CANCEL_ZH if language.startswith("zh") else CONFIRM_BOOK_CANCEL_EN
    raise ValueError(f"no confirm prompt for flow {flow}")


def handoff_prompt(language: str) -> Callable[[str], str]:
    return lambda admin_contact: t("handoff_msg", language, admin_contact=admin_contact)


__all__ = [
    "SYSTEM_PROMPT_EN",
    "SYSTEM_PROMPT_ZH",
    "TOOL_DESC_EN",
    "TOOL_DESC_ZH",
    "STRINGS",
    "t",
    "system_prompt",
    "tool_descriptions",
    "confirm_prompt",
]
