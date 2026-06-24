"""Handoff flow (issue #7).

When the router decides the customer needs a human (refund, complaint,
custom pricing, abuse, or anything outside the booking tools), this
flow:

1. Notifies every phone in ``WA_NOTIFY`` via wa-bridge ``POST /send``.
   The customer phone, original message, detected reason, and timestamp
   are included so the human operator has full context.
2. Replies to the customer in their language with a short message
   pointing at ``ADMIN_CONTACT_NUMBER`` (the runbook's "team will reach
   out" pattern).
3. Special case for ``abuse``: a shorter ack is used and the admin DM
   is flagged so the operator can decide whether to block the sender.

This module is deliberately thin — it does the orchestration only.
``src.notify`` does the actual HTTP, ``src.i18n`` does the strings.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from src import notify
from src.enums import HandoffReason
from src.i18n import t

logger = logging.getLogger(__name__)


def handle(
    *,
    sender: str,
    reason: str,
    summary: str,
    language: str = "en",
    is_fallback: bool = False,
    notify_client: Any | None = None,
) -> str:
    """Process a handoff.

    Returns the customer-facing reply text. Side effects: zero or more
    admin DMs via ``src.notify.notify_handoff``.

    ``is_fallback`` is True when the router fell back to handoff
    because the LLM call failed or the model returned an invalid
    tool call — the admin DM gets a ``[FALLBACK]`` tag so operators
    can spot low-quality routing.
    """
    lang = language or "en"
    admin_contact = os.environ.get("ADMIN_CONTACT_NUMBER", "")

    # Validate reason; the router should always pass a valid one but
    # be defensive in case the model emits something off-schema.
    try:
        HandoffReason(reason)
    except ValueError:
        logger.warning("invalid handoff reason %r; coercing to 'other'", reason)
        reason = HandoffReason.OTHER.value

    sent = notify.notify_handoff(
        sender=sender,
        reason=reason,
        summary=summary,
        is_fallback=is_fallback,
        client=notify_client,
    )
    logger.info(
        "handoff handled sender=%s reason=%s fallback=%s admins_notified=%d",
        sender,
        reason,
        is_fallback,
        sent,
    )

    if reason == HandoffReason.ABUSE.value:
        return t("abusive_msg", lang)

    return t("handoff_msg", lang, admin_contact=admin_contact)


__all__ = ["handle"]