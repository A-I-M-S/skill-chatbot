"""Shared enums and type aliases for the orchestrator."""

from __future__ import annotations

from enum import Enum


class Flow(str, Enum):
    """The flow a phone is currently in.

    ``idle`` is the default — no active multi-turn collection.
    ``book_new`` / ``book_edit`` / ``book_cancel`` are the three booking
    surfaces. ``handoff`` is the terminal state for an out-of-scope intent.
    """

    IDLE = "idle"
    BOOK_NEW = "book_new"
    BOOK_EDIT = "book_edit"
    BOOK_CANCEL = "book_cancel"
    HANDOFF = "handoff"


class HandoffReason(str, Enum):
    """Why we escalated to a human. Used for the admin DM + the runbook."""

    REFUND = "refund"
    COMPLAINT = "complaint"
    CUSTOM_PRICING = "custom_pricing"
    ABUSE = "abuse"
    OTHER = "other"


class Language(str, Enum):
    EN = "en"
    ZH = "zh"


__all__ = ["Flow", "HandoffReason", "Language"]
