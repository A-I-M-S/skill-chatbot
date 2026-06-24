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
    client: httpx.Client | None = None,
) -> int:
    """Notify admins that a new booking was committed.

    Stub for v1 (issue #8 will add language detection + bilingual
    formatting). Currently sends the same format as :func:`notify_handoff`
    but with a "new booking" prefix.
    """
    admins = _split_admins()
    if not admins:
        return 0
    url, token = _bridge_send_url()
    fields = ", ".join(f"{k}={v}" for k, v in summary.items() if v is not None)
    body = (
        f"\U0001f195 New farm tour booking\n"
        f"From: +{sender.lstrip('+')}\n"
        f"event_id: {event_id}\n"
        f"{fields}"
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
                    "notify_new_booking failed for admin=%s err=%s", admin, e
                )
    finally:
        if client is None:
            own_client.close()
    return sent


__all__ = ["notify_handoff", "notify_new_booking"]