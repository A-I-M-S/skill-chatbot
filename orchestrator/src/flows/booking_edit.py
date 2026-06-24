"""Book-edit and book-cancel flows (issue #6).

Both flows follow the same shape:

1. **Lookup**: list events in the next ``BOOKING_HORIZON_DAYS`` via
   :func:`src.booking_subprocess.list_events`, then filter in-process
   by the caller's phone (no upstream skill patch — plan decision E).
2. **Pick**: 0 matches → reply "no bookings on this number" + offer
   handoff. 1 match → present and ask for confirmation. >1 match →
   numbered list, ask user to pick one.
3. **Confirm**: phone goes to ``awaiting_confirm``. ``YES`` / ``是``
   commits via :func:`src.booking_subprocess.edit` (or :func:`cancel`);
   anything else aborts but preserves the chosen event so the user
   can resume.

Contact lookup uses two signals (because the upstream skill's event
body format varies):

- The event body's ``Contact: <name> <email> <phone>`` line.
- The event's ``attendees`` list (Outlook stores phone there too).

We do a substring match of the digits-only phone against either the
body text or any attendee's phone field. This is best-effort: if a
legacy event was booked without the customer's phone, edit/cancel
won't see it.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from src.booking_subprocess import (
    BookingSubprocessError,
    cancel,
    edit,
    list_events,
)
from src.enums import Flow
from src.i18n import t

logger = logging.getLogger(__name__)

import os

BOOKING_HORIZON_DAYS = int(os.environ.get("BOOKING_HORIZON_DAYS") or "90")

PHONE_CHARS = re.compile(r"\D+")


def _digits_only(s: str) -> str:
    return PHONE_CHARS.sub("", s or "")


def _event_matches_phone(event: dict[str, Any], phone: str) -> bool:
    """True if the event body or attendees reference ``phone``."""
    target = _digits_only(phone)
    if not target:
        return False
    body = (event.get("body") or "") + " " + (event.get("description") or "")
    if target in _digits_only(body):
        return True
    for att in event.get("attendees") or []:
        if target in _digits_only(str(att.get("phone") or "")):
            return True
        if target in _digits_only(str(att.get("email") or "")):
            return True
    return False


def _summarize_event(event: dict[str, Any], language: str) -> str:
    """One-line summary suitable for the confirm prompt."""
    start = event.get("start") or event.get("start_iso") or "?"
    pax = event.get("pax") or "?"
    subj = event.get("subject") or ""
    short_subj = subj[:60] + ("…" if len(subj) > 60 else "")
    if language.lower().startswith("zh"):
        return f"{start} · {pax} 位 · {short_subj}"
    return f"{start} · {pax} pax · {short_subj}"


def _event_id(event: dict[str, Any]) -> str | None:
    """Pull the event id out of an upstream ``list_events`` row.

    The upstream CLI emits rows with ``id`` (Composio event id) or
    ``event_id``. Fall back to the subject hash if neither is present.
    """
    return event.get("id") or event.get("event_id")


# ──────────────────────────────────────────────────────────────────────
# Shared lookup
# ──────────────────────────────────────────────────────────────────────


def _lookup_user_events(
    phone: str,
    horizon_days: int,
    state: Any,
) -> list[dict[str, Any]]:
    """Return events for ``phone`` in the next ``horizon_days``.

    Cached in phone_state.draft[\"candidates\"] for 60 seconds so a
    multi-message pick sequence doesn't re-query Composio every turn.
    """
    existing = state.get_phone_state(phone) or {}
    cached = existing.get("draft") or {}
    cached_for = cached.get("lookup_at") if isinstance(cached, dict) else None
    cached_events = cached.get("candidates") if isinstance(cached, dict) else None
    if (
        cached_for
        and cached_events is not None
        and (datetime.now().timestamp() - float(cached_for)) < 60
    ):
        return cached_events

    start = datetime.now().strftime("%Y-%m-%dT00:00:00")
    end = (datetime.now() + timedelta(days=horizon_days)).strftime("%Y-%m-%dT23:59:59")
    try:
        events = list_events(start, end)
    except BookingSubprocessError as e:
        logger.warning("list_events failed for book_edit/cancel: %s", e)
        return []
    mine = [e for e in events if _event_matches_phone(e, phone)]
    # Cache
    new_draft = {
        **(cached if isinstance(cached, dict) else {}),
        "lookup_at": datetime.now().timestamp(),
        "candidates": mine,
    }
    state.set_phone_state(
        phone,
        existing.get("flow", "idle"),
        draft=new_draft,
        language=existing.get("language", "en"),
    )
    return mine


# ──────────────────────────────────────────────────────────────────────
# book_edit
# ──────────────────────────────────────────────────────────────────────


def handle_edit(
    *,
    phone: str,
    user_text: str,
    tool_args: dict[str, Any],
    state: Any,
    language: str = "en",
    confirm_reply: str | None = None,
) -> str:
    """Drive one turn of the ``book_edit`` flow.

    State machine:

    - idle / collecting: look up events; if 0 → handoff msg, if 1 →
      show + ask YES, if >1 → numbered list + ask to pick.
    - awaiting_confirm: process YES / abort.
    """
    current = state.get_phone_state(phone) or {
        "phone": phone, "flow": "idle", "draft": None, "pending_confirm": None,
        "language": language, "updated_at": 0.0,
    }
    current_flow = current.get("flow", "idle")

    # Confirm branch
    if confirm_reply is not None and current_flow == Flow.BOOK_EDIT.value:
        return _handle_edit_confirm(phone, confirm_reply, current, state, language)

    # Already picked an event — just merge new fields and re-prompt
    if current_flow == Flow.BOOK_EDIT.value and (current.get("draft") or {}).get("picked_event_id"):
        return _continue_edit_with_pick(phone, user_text, tool_args, current, state, language)

    # Fresh start — look up events.
    candidates = _lookup_user_events(phone, BOOKING_HORIZON_DAYS, state)
    if not candidates:
        # No bookings — clear state and offer handoff
        state.clear_phone_state(phone)
        return t("edit_no_events", language)

    if len(candidates) == 1:
        ev = candidates[0]
        eid = _event_id(ev)
        if not eid:
            return t("handoff_msg", language, admin_contact=os.environ.get("ADMIN_CONTACT_NUMBER", ""))
        # Merge any new fields from tool_args
        draft = {**(current.get("draft") or {}), "picked_event_id": eid, "summary": _summarize_event(ev, language)}
        for k, v in tool_args.items():
            if v is not None and v != "":
                draft[k] = v
        if any(k in draft for k in ("date", "time", "pax")):
            state.set_phone_state(
                phone,
                Flow.BOOK_EDIT.value,
                draft=draft,
                pending_confirm={"stage": "awaiting", "kind": "edit"},
                language=language,
            )
            new_summary = _build_new_summary(draft, language)
            return _edit_confirm_prompt(draft["summary"], new_summary, language)
        # No new fields yet — ask which field to change
        state.set_phone_state(
            phone,
            Flow.BOOK_EDIT.value,
            draft=draft,
            language=language,
        )
        return _edit_ask_field(language)

    # Multiple candidates — numbered list + ask user to pick
    lines = []
    for i, ev in enumerate(candidates, 1):
        lines.append(f"{i}. {_summarize_event(ev, language)}")
    state.set_phone_state(
        phone,
        Flow.BOOK_EDIT.value,
        draft={**(current.get("draft") or {}), "candidates": candidates, "lookup_at": datetime.now().timestamp()},
        pending_confirm={"stage": "awaiting_pick"},
        language=language,
    )
    return t("edit_pick_event", language, events="\n".join(lines))


def _continue_edit_with_pick(
    phone: str,
    user_text: str,
    tool_args: dict[str, Any],
    current: dict[str, Any],
    state: Any,
    language: str,
) -> str:
    draft = dict(current.get("draft") or {})
    for k, v in tool_args.items():
        if v is not None and v != "":
            draft[k] = v
    if not any(k in draft for k in ("date", "time", "pax")):
        return _edit_ask_field(language)
    state.set_phone_state(
        phone,
        Flow.BOOK_EDIT.value,
        draft=draft,
        pending_confirm={"stage": "awaiting", "kind": "edit"},
        language=language,
    )
    new_summary = _build_new_summary(draft, language)
    return _edit_confirm_prompt(draft.get("summary", "?"), new_summary, language)


def _handle_edit_confirm(
    phone: str,
    reply: str,
    current: dict[str, Any],
    state: Any,
    language: str,
) -> str:
    pending = current.get("pending_confirm") or {}
    stage = pending.get("stage")

    if stage == "awaiting_pick":
        # The user replied with a number to pick from the list
        candidates = (current.get("draft") or {}).get("candidates") or []
        try:
            idx = int(reply.strip()) - 1
        except ValueError:
            return t("edit_pick_event", language, events="\n".join(
                f"{i}. {_summarize_event(ev, language)}" for i, ev in enumerate(candidates, 1)
            ))
        if idx < 0 or idx >= len(candidates):
            return t("edit_pick_event", language, events="\n".join(
                f"{i}. {_summarize_event(ev, language)}" for i, ev in enumerate(candidates, 1)
            ))
        ev = candidates[idx]
        eid = _event_id(ev)
        if not eid:
            state.clear_phone_state(phone)
            return t("handoff_msg", language, admin_contact=os.environ.get("ADMIN_CONTACT_NUMBER", ""))
        draft = {**(current.get("draft") or {}), "picked_event_id": eid, "summary": _summarize_event(ev, language)}
        state.set_phone_state(
            phone,
            Flow.BOOK_EDIT.value,
            draft=draft,
            language=language,
        )
        return _edit_ask_field(language)

    if stage == "awaiting":
        if reply.strip().lower() in ("yes", "y", "是"):
            draft = current.get("draft") or {}
            eid = draft.get("picked_event_id")
            if not eid:
                state.clear_phone_state(phone)
                return t("handoff_msg", language, admin_contact=os.environ.get("ADMIN_CONTACT_NUMBER", ""))
            try:
                result = edit(
                    eid,
                    date=draft.get("date"),
                    time=draft.get("time"),
                    pax=int(draft["pax"]) if draft.get("pax") is not None else None,
                )
            except BookingSubprocessError as e:
                logger.error("book_edit commit failed: %s", e)
                state.clear_phone_state(phone)
                return t("handoff_msg", language, admin_contact=os.environ.get("ADMIN_CONTACT_NUMBER", ""))
            state.clear_phone_state(phone)
            upstream_reply = result.get("reply", "")
            return upstream_reply or t("yes_received_edited", language, event_id=eid)

        # Anything else = abort
        state.set_phone_state(
            phone,
            Flow.BOOK_EDIT.value,
            draft=current.get("draft"),
            language=language,
        )
        return t("aborted", language)

    # Unknown stage — reset
    state.clear_phone_state(phone)
    return t("aborted", language)


def _build_new_summary(draft: dict[str, Any], language: str) -> str:
    parts = []
    for k in ("date", "time", "pax"):
        if k in draft:
            parts.append(f"{k}={draft[k]}")
    return ", ".join(parts) or "(no changes)"


def _edit_confirm_prompt(old: str, new: str, language: str) -> str:
    return t("edit_confirm", language, old=old, new=new)


def _confirm_template(old: str, new: str, language: str) -> str:
    return t("edit_confirm", language, old=old, new=new)


def _edit_ask_field(language: str) -> str:
    return t("edit_ask_field", language)


# ──────────────────────────────────────────────────────────────────────
# book_cancel
# ──────────────────────────────────────────────────────────────────────


def handle_cancel(
    *,
    phone: str,
    user_text: str,
    tool_args: dict[str, Any],
    state: Any,
    language: str = "en",
    confirm_reply: str | None = None,
) -> str:
    """Drive one turn of the ``book_cancel`` flow.

    Same shape as edit: lookup → pick → confirm.
    """
    current = state.get_phone_state(phone) or {
        "phone": phone, "flow": "idle", "draft": None, "pending_confirm": None,
        "language": language, "updated_at": 0.0,
    }
    current_flow = current.get("flow", "idle")

    if confirm_reply is not None and current_flow == Flow.BOOK_CANCEL.value:
        return _handle_cancel_confirm(phone, confirm_reply, current, state, language)

    candidates = _lookup_user_events(phone, BOOKING_HORIZON_DAYS, state)
    if not candidates:
        state.clear_phone_state(phone)
        return t("edit_no_events", language)

    if len(candidates) == 1:
        ev = candidates[0]
        eid = _event_id(ev)
        if not eid:
            return t("handoff_msg", language, admin_contact=os.environ.get("ADMIN_CONTACT_NUMBER", ""))
        draft = {
            **(current.get("draft") or {}),
            "picked_event_id": eid,
            "summary": _summarize_event(ev, language),
        }
        state.set_phone_state(
            phone,
            Flow.BOOK_CANCEL.value,
            draft=draft,
            pending_confirm={"stage": "awaiting"},
            language=language,
        )
        return _cancel_confirm_prompt(draft["summary"], language)

    lines = [f"{i}. {_summarize_event(ev, language)}" for i, ev in enumerate(candidates, 1)]
    state.set_phone_state(
        phone,
        Flow.BOOK_CANCEL.value,
        draft={**(current.get("draft") or {}), "candidates": candidates, "lookup_at": datetime.now().timestamp()},
        pending_confirm={"stage": "awaiting_pick"},
        language=language,
    )
    return t("edit_pick_event", language, events="\n".join(lines))


def _handle_cancel_confirm(
    phone: str,
    reply: str,
    current: dict[str, Any],
    state: Any,
    language: str,
) -> str:
    pending = current.get("pending_confirm") or {}
    stage = pending.get("stage")

    if stage == "awaiting_pick":
        candidates = (current.get("draft") or {}).get("candidates") or []
        try:
            idx = int(reply.strip()) - 1
        except ValueError:
            return t("edit_pick_event", language, events="\n".join(
                f"{i}. {_summarize_event(ev, language)}" for i, ev in enumerate(candidates, 1)
            ))
        if idx < 0 or idx >= len(candidates):
            return t("edit_pick_event", language, events="\n".join(
                f"{i}. {_summarize_event(ev, language)}" for i, ev in enumerate(candidates, 1)
            ))
        ev = candidates[idx]
        eid = _event_id(ev)
        if not eid:
            state.clear_phone_state(phone)
            return t("handoff_msg", language, admin_contact=os.environ.get("ADMIN_CONTACT_NUMBER", ""))
        draft = {**(current.get("draft") or {}), "picked_event_id": eid, "summary": _summarize_event(ev, language)}
        state.set_phone_state(
            phone,
            Flow.BOOK_CANCEL.value,
            draft=draft,
            pending_confirm={"stage": "awaiting"},
            language=language,
        )
        return _cancel_confirm_prompt(draft["summary"], language)

    if stage == "awaiting":
        if reply.strip().lower() in ("yes", "y", "是"):
            draft = current.get("draft") or {}
            eid = draft.get("picked_event_id")
            if not eid:
                state.clear_phone_state(phone)
                return t("handoff_msg", language, admin_contact=os.environ.get("ADMIN_CONTACT_NUMBER", ""))
            try:
                result = cancel(eid)
            except BookingSubprocessError as e:
                logger.error("book_cancel commit failed: %s", e)
                state.clear_phone_state(phone)
                return t("handoff_msg", language, admin_contact=os.environ.get("ADMIN_CONTACT_NUMBER", ""))
            state.clear_phone_state(phone)
            upstream_reply = result.get("reply", "")
            return upstream_reply or t("yes_received_cancelled", language, event_id=eid)

        state.set_phone_state(
            phone,
            Flow.BOOK_CANCEL.value,
            draft=current.get("draft"),
            language=language,
        )
        return t("aborted", language)

    state.clear_phone_state(phone)
    return t("aborted", language)


def _cancel_confirm_prompt(summary: str, language: str) -> str:
    return t("cancel_confirm", language, event_summary=summary)


__all__ = ["handle_edit", "handle_cancel", "BOOKING_HORIZON_DAYS"]