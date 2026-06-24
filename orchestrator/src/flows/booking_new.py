"""Booking new flow (issue #5).

State machine:

    idle → collecting → awaiting_confirm → (committed | aborted)
       ↑        ↑              │
       └────────┴──────────────┘  (re-collect missing fields, or abort)

- ``collecting``: we have a draft with some fields. Each new router
  decision with ``tool='book_new'`` may carry more fields; we merge
  them into the draft, then ask the user for the next missing required
  field.
- ``awaiting_confirm``: all required fields are present. We show a
  localised summary and wait for an explicit ``YES`` (or ``是`` in
  中文). Anything else aborts (but preserves the draft for 10 min so
  the user can say "actually, continue").
- ``committed``: booked via booking_flow.py new --confirm. Phone goes
  back to ``idle``. Admin DM fires.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from src.booking_subprocess import (
    BookingSubprocessError,
    new_commit,
    new_draft,
)
from src.enums import Flow
from src.i18n import confirm_prompt, t

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("date", "time", "pax")
CONTACT_FIELDS = ("contact_name", "contact_email", "contact_phone")

FIELD_ORDER = REQUIRED_FIELDS + CONTACT_FIELDS + ("org", "notes")

# Maximum days into the future we accept a booking. Override via env.
import os

BOOKING_HORIZON_DAYS = int(os.environ.get("BOOKING_HORIZON_DAYS") or "90")

# Draft TTL — if the user goes silent for this long, we drop the draft.
DRAFT_TTL_SECONDS = 600


def _draft_missing_fields(draft: dict[str, Any]) -> list[str]:
    """Return required fields that aren't yet populated, in asking order."""
    missing: list[str] = []
    for f in REQUIRED_FIELDS:
        v = draft.get(f)
        if v is None or v == "":
            missing.append(f)
    if not missing:
        # Contact info: need at least one of email or phone.
        if not (draft.get("contact_email") or draft.get("contact_phone")):
            missing.append("contact")
    return missing


def _validate_draft(draft: dict[str, Any], booking_horizon_days: int = BOOKING_HORIZON_DAYS) -> str | None:
    """Sanity-check the draft. Returns an error message or ``None`` if OK."""
    from datetime import date as _date, datetime, timedelta

    # Date parse + horizon check
    try:
        start = datetime.strptime(f"{draft['date']} {draft['time']}", "%Y-%m-%d %H:%M")
    except (KeyError, ValueError) as e:
        return f"invalid date/time ({e})"
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if start < today:
        return "date is in the past"
    if start > today + timedelta(days=booking_horizon_days):
        return f"date is more than {booking_horizon_days} days out"

    # Pax sanity
    try:
        pax = int(draft["pax"])
    except (KeyError, ValueError, TypeError):
        return "pax must be an integer"
    if pax < 1 or pax > 500:
        return "pax out of range (1-500)"

    return None


def _draft_expired(state_row: dict[str, Any]) -> bool:
    """True if the draft is older than ``DRAFT_TTL_SECONDS``."""
    if not state_row:
        return False
    updated = state_row.get("updated_at")
    if updated is None:
        return False
    return (time.time() - updated) > DRAFT_TTL_SECONDS


def _draft_summary(draft: dict[str, Any], language: str) -> str:
    """Build a short user-facing summary of the draft (for confirm prompt)."""
    parts = [
        draft.get("date", "?"),
        draft.get("time", "?"),
        f"{draft.get('pax', '?')} pax",
    ]
    name = draft.get("contact_name") or ""
    email = draft.get("contact_email") or ""
    phone = draft.get("contact_phone") or ""
    contact = name + (f" <{email}>" if email else "") + (f" {phone}" if phone else "")
    parts.append(f"contact {contact.strip()}")
    if draft.get("org"):
        parts.append(f"org {draft['org']}")
    if draft.get("notes"):
        parts.append(f"notes: {draft['notes']}")
    return ", ".join(p for p in parts if p)


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────


def handle(
    *,
    phone: str,
    user_text: str,
    tool_args: dict[str, Any],
    state: Any,  # State — typed loosely to avoid the import cycle
    language: str = "en",
    confirm_reply: str | None = None,
) -> str:
    """Drive one turn of the ``book_new`` flow.

    ``confirm_reply`` is non-None only when the state machine is in
    ``awaiting_confirm`` and the caller wants us to process the user's
    YES/abort reply. In all other cases it's None.

    Returns the text to send back to the customer.
    """
    current = state.get_phone_state(phone) or {
        "phone": phone, "flow": "idle", "draft": None, "pending_confirm": None,
        "language": language, "updated_at": 0.0,
    }
    current_flow = current.get("flow", "idle")

    # If the existing draft is stale, drop it.
    if current_flow == Flow.BOOK_NEW.value and _draft_expired(current):
        logger.info("book_new: dropping stale draft for phone=%s", phone)
        state.clear_phone_state(phone)
        current_flow = "idle"
        current["flow"] = "idle"
        current["draft"] = None

    # Confirm branch — only when we're in awaiting_confirm and the
    # caller supplied a user reply.
    if confirm_reply is not None and current_flow == Flow.BOOK_NEW.value:
        return _handle_confirm(phone, confirm_reply, current, state, language)

    # If the user is mid-flow (collecting / awaiting_confirm) and the
    # router still said book_new, merge the new args into the draft
    # and ask the next question.
    if current_flow in (Flow.BOOK_NEW.value, "awaiting_confirm"):
        draft = dict(current.get("draft") or {})
        for k, v in tool_args.items():
            if v is not None and v != "":
                draft[k] = v
        return _continue_collection(phone, draft, state, language)

    # Fresh flow — start collecting.
    draft = {k: v for k, v in tool_args.items() if v is not None and v != ""}
    state.set_phone_state(phone, Flow.BOOK_NEW.value, draft=draft, language=language)
    return _continue_collection(phone, draft, state, language)


def _continue_collection(
    phone: str,
    draft: dict[str, Any],
    state: Any,
    language: str,
) -> str:
    """Given the current draft, either ask the next question or move to confirm."""
    missing = _draft_missing_fields(draft)
    if missing:
        # Persist whatever we have so far.
        state.set_phone_state(phone, Flow.BOOK_NEW.value, draft=draft, language=language)
        return _ask_for(missing[0], language)
    # All required fields present — validate, then prompt for confirmation.
    err = _validate_draft(draft)
    if err is not None:
        # Drop the offending field and re-ask.
        bad_field = _field_for_error(err, draft)
        if bad_field and bad_field in draft:
            del draft[bad_field]
        state.set_phone_state(phone, Flow.BOOK_NEW.value, draft=draft, language=language)
        return t("aborted", language) + " " + err + " — " + _ask_for(bad_field or "date", language)
    # Ready for confirmation.
    state.set_phone_state(
        phone,
        Flow.BOOK_NEW.value,
        draft=draft,
        pending_confirm={"stage": "awaiting"},
        language=language,
    )
    return confirm_prompt(Flow.BOOK_NEW, language).format(
        date=draft.get("date", "?"),
        time=draft.get("time", "?"),
        pax=draft.get("pax", "?"),
        contact_name=draft.get("contact_name", "") or "—",
        contact_email_or_phone=(
            draft.get("contact_email") or draft.get("contact_phone") or "—"
        ),
    )


def _handle_confirm(
    phone: str,
    reply: str,
    current: dict[str, Any],
    state: Any,
    language: str,
) -> str:
    """Process the user's YES/abort in ``awaiting_confirm``."""
    pending = current.get("pending_confirm") or {}
    if pending.get("stage") != "awaiting":
        # Stuck in a bad state — reset.
        state.clear_phone_state(phone)
        return t("aborted", language)

    if reply.strip().lower() in ("yes", "y", "是"):
        draft = current.get("draft") or {}
        # Commit via upstream skill
        try:
            result = new_commit(
                date=draft.get("date", ""),
                time=draft.get("time", ""),
                pax=int(draft.get("pax", 0)),
                contact_name=draft.get("contact_name"),
                contact_email=draft.get("contact_email"),
                contact_phone=draft.get("contact_phone"),
                org=draft.get("org"),
                notes=draft.get("notes"),
            )
        except BookingSubprocessError as e:
            logger.error("book_new commit failed: %s", e)
            state.clear_phone_state(phone)
            return t("handoff_msg", language, admin_contact=os.environ.get("ADMIN_CONTACT_NUMBER", ""))

        # Success — clear state and notify admins (issue #8), then
        # return the upstream reply (which includes the booking id).
        event_id = (result.get("event") or {}).get("id") or result.get("event_id") or "unknown"
        try:
            from src import notify as notify_mod
            notify_mod.notify_new_booking(
                sender=phone,
                event_id=str(event_id),
                summary={
                    "date": draft.get("date", ""),
                    "time": draft.get("time", ""),
                    "pax": draft.get("pax"),
                    "contact_name": draft.get("contact_name"),
                    "contact_email": draft.get("contact_email"),
                    "contact_phone": draft.get("contact_phone"),
                    "org": draft.get("org"),
                },
                language=language,
            )
        except Exception as e:  # noqa: BLE001
            # Best-effort — log and continue; the booking already succeeded.
            logger.warning("notify_new_booking failed (booking already committed): %s", e)
        state.clear_phone_state(phone)
        upstream_reply = result.get("reply", "")
        if result.get("error"):
            # Treat upstream error as soft handoff
            return upstream_reply or t("handoff_msg", language, admin_contact=os.environ.get("ADMIN_CONTACT_NUMBER", ""))
        return upstream_reply

    # Anything else = abort. Preserve the draft so the user can resume.
    state.set_phone_state(
        phone,
        Flow.BOOK_NEW.value,
        draft=current.get("draft"),
        language=language,
    )
    return t("aborted", language)


def _ask_for(field: str, language: str) -> str:
    prompts = {
        "date": {
            "en": "What date would you like? (YYYY-MM-DD)",
            "zh": "请问您想预约哪一天？（YYYY-MM-DD）",
        },
        "time": {
            "en": "What time? (HH:MM, 24-hour)",
            "zh": "请问几点？（24 小时制，HH:MM）",
        },
        "pax": {
            "en": "How many people?",
            "zh": "请问有多少人？",
        },
        "contact_name": {
            "en": "Contact name?",
            "zh": "联系人姓名？",
        },
        "contact": {
            "en": "Contact email or phone? (at least one)",
            "zh": "联系邮箱或电话？（至少填一个）",
        },
        "org": {
            "en": "Organisation or school? (optional — type 'skip' to omit)",
            "zh": "学校或单位？（可选 — 输入「跳过」即可）",
        },
        "notes": {
            "en": "Any notes? (optional — type 'skip' to omit)",
            "zh": "备注？（可选 — 输入「跳过」即可）",
        },
    }
    p = prompts.get(field, prompts["date"])
    return p.get("zh" if language.lower().startswith("zh") else "en", p["en"])


def _field_for_error(err: str, draft: dict[str, Any]) -> str | None:
    """Map a validation error back to the field we should re-ask for."""
    if "date" in err and "time" not in err:
        return "date"
    if "time" in err:
        return "time"
    if "pax" in err:
        return "pax"
    return None


__all__ = ["handle", "DRAFT_TTL_SECONDS", "BOOKING_HORIZON_DAYS"]