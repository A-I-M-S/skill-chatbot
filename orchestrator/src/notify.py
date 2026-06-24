"""Outbound notifications to admins (issue #7 + #8).

Two notify paths:

- :func:`notify_handoff` \u2014 fires when the router hands off a customer
  to a human (refund, complaint, custom pricing, abuse, other). Notifies
  every phone in ``WA_NOTIFY`` via wa-bridge ``POST /send``.
- :func:`notify_new_booking` \u2014 fires after a successful ``book_new``
  commit. Stubbed in v1; full implementation lands in #8.

The notify functions do NOT raise on transport errors \u2014 the failure is
logged at WARN and the customer-facing flow continues. The handoff reply
to the customer is still posted even if the admin DM fails.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _split_admins() -> list[str]:
    raw = os.environ.get("WA_NOTIFY", "")
    out: list[str] = []
    for part in raw.split(","):
        phone = part.strip()
        if phone and phone.lstrip("+").isdigit():
            out.append(phone if phone.startswith("+") else f"+{phone}")
    return out


def _bridge_send_url() -> tuple[str, str]:
    base = os.environ.get("WA_BRIDGE_URL", "http://127.0.0.1:7788").rstrip("/")
    token = os.environ.get("WA_BRIDGE_TOKEN", "")
    return f"{base}/send", token


def notify_handoff(
    sender: str,
    reason: str,
    summary: str,
    is_fallback: bool = False,
    *,
    client: httpx.Client | None = None,
) -> int:
    """DM every phone in ``WA_NOTIFY`` about a customer handoff.

    Returns the number of admin DMs sent successfully (0 if ``WA_NOTIFY``
    is unset). Failures are logged at WARN, not raised.
    """
    admins = _split_admins()
    if not admins:
        logger.warning(
            "WA_NOTIFY is empty; handoff for sender=%s reason=%s was not delivered to admins",
            sender,
            reason,
        )
        return 0

    url, token = _bridge_send_url()
    fallback_tag = " [FALLBACK]" if is_fallback else ""
    body = (
        f"\U0001f6a8 Farm tour handoff{fallback_tag}\n"
        f"From: +{sender.lstrip('+')}\n"
        f"Reason: {reason}\n"
        f"Customer said: {summary[:1000]}"
    )
    own_client = client or httpx.Client()
    sent = 0
    try:
        for admin in admins:
            try:
                resp = own_client.post(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"to": admin, "text": body},
                    timeout=5.0,
                )
                resp.raise_for_status()
                sent += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "notify_handoff failed for admin=%s err=%s", admin, e
                )
    finally:
        if client is None:
            own_client.close()
    return sent


def notify_new_booking(
    sender: str,
    event_id: str,
    summary: dict[str, Any],
    *,
    language: str = "en",
    client: httpx.Client | None = None,
) -> int:
    """Notify admins that a new booking was committed.

    Bilingual message — the language is the customer's language, so the
    admin sees the booking summary in the same language the customer
    used. Failures are logged and swallowed (the booking itself
    succeeds; the admin DM is best-effort).
    """
    admins = _split_admins()
    if not admins:
        logger.warning(
            "WA_NOTIFY is empty; new-booking alert for sender=%s event=%s not delivered",
            sender,
            event_id,
        )
        return 0
    url, token = _bridge_send_url()

    if (language or "en").lower().startswith("zh"):
        title = "🆕 新预约"
        from_line = "客户"
        label_event = "编号"
        label_pax = "人数"
        label_when = "时间"
        label_contact = "联系人"
        label_org = "单位"
    else:
        title = "🆕 New farm tour booking"
        from_line = "From"
        label_event = "event_id"
        label_pax = "pax"
        label_when = "when"
        label_contact = "contact"
        label_org = "org"

    body_lines = [
        title,
        f"{from_line}: +{sender.lstrip('+')}",
        f"{label_event}: {event_id}",
    ]
    when = summary.get("when") or f"{summary.get('date', '?')} {summary.get('time', '?')}"
    if when:
        body_lines.append(f"{label_when}: {when}")
    if summary.get("pax") is not None:
        body_lines.append(f"{label_pax}: {summary['pax']}")
    if summary.get("contact_name") or summary.get("contact_email") or summary.get("contact_phone"):
        contact = " ".join(
            str(x)
            for x in (
                summary.get("contact_name"),
                f"<{summary['contact_email']}>" if summary.get("contact_email") else "",
                summary.get("contact_phone") or "",
            )
            if x
        ).strip()
        body_lines.append(f"{label_contact}: {contact}")
    if summary.get("org"):
        body_lines.append(f"{label_org}: {summary['org']}")
    body = "\n".join(body_lines)

    own_client = client or httpx.Client()
    sent = 0
    try:
        for admin in admins:
            try:
                resp = own_client.post(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"to": admin, "text": body},
                    timeout=5.0,
                )
                resp.raise_for_status()
                sent += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "notify_new_booking failed for admin=%s err=%s", admin, e
                )
    finally:
        if client is None:
            own_client.close()
    return sent


__all__ = ["notify_handoff", "notify_new_booking"]