#!/usr/bin/env python3
"""Intent classifier for farm tour booking messages.

Rule-based primary, optional LLM fallback hook. Outputs JSON to stdout.
The LLM agent (BAAdmin) is the real classifier for edge cases — this
script is the fast pre-filter so the agent can decide whether to
invest further.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from typing import Literal

Intent = Literal["new_booking", "edit_booking", "cancel_booking", "inquiry", "other"]

RULES: dict[Intent, list[str]] = {
    "new_booking": [
        r"\bbook(?:ing)?\b", r"\breserve\b", r"\bsign[- ]?up\b",
        r"\bvisit(?:ing)?\b", r"\bwould like to come\b", r"\bcan we (?:come|visit)\b",
        r"\btour\b", r"\bgroup\b.*\b(visit|come|tour|book)\b",
        r"\bprimary\s*\d\b", r"\bkindy\b", r"\bschool\s+trip\b",
    ],
    "edit_booking": [
        r"\bchange\b", r"\breschedule\b", r"\bmove\b", r"\bswitch\b",
        r"\bcan we (?:do|make it)\b", r"\binstead\b", r"\bupdate\b.*\b(book|tour|booking)\b",
        r"\bpostpone\b", r"\bbring forward\b",
    ],
    "cancel_booking": [
        r"\bcancel\b", r"\brefund\b", r"\bcan'?t make\b", r"\bwon'?t be able\b",
        r"\bno longer (?:need|want|coming)\b", r"\bdon'?t need\b",
        r"\bsorry to\b.*\b(cancel|inform)\b",
    ],
    "inquiry": [
        r"\bhow much\b", r"\bprice\b", r"\bcost\b", r"\bfees?\b",
        r"\bwhat time\b", r"\bopening hours?\b", r"\bavailable\b",
        r"\bwhere\b", r"\baddress\b", r"\blocation\b",
        r"\bmenu\b", r"\bworkshop\b", r"\bactivities\b",
    ],
}

FIELD_PATTERNS: dict[str, str] = {
    "pax": r"\b(\d{1,4})\s*(pax|people|persons?|students?|kids?|adults?|guests?|tickets?|teachers?|children)\b",
    "date": (
        r"\b(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"
        r"|(?:\d{1,2}\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:\s+\d{2,4})?"
        r"|(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*)\b"
    ),
    "email": r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    "phone": r"\+?\d[\d\s\-]{7,}\d",
}


@dataclass
class ClassifiedIntent:
    intent: Intent
    confidence: float
    fields: dict = field(default_factory=dict)
    raw: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


def _score(text: str) -> dict[Intent, int]:
    lower = text.lower()
    return {intent: sum(1 for p in pats if re.search(p, lower)) for intent, pats in RULES.items()}


def _extract(text: str) -> dict:
    out: dict = {}
    for key, pat in FIELD_PATTERNS.items():
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            out[key] = m.group(1) if m.groups() else m.group(0)
    return out


def classify(text: str) -> ClassifiedIntent:
    text = (text or "").strip()
    if not text:
        return ClassifiedIntent(intent="other", confidence=0.5, raw="")
    scores = _score(text)
    best_intent, best_hits = max(scores.items(), key=lambda kv: kv[1])
    if best_hits == 0:
        return ClassifiedIntent(intent="other", confidence=0.5, fields=_extract(text), raw=text)
    confidence = min(0.95, 0.55 + 0.15 * best_hits)
    return ClassifiedIntent(intent=best_intent, confidence=confidence, fields=_extract(text), raw=text)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        text = sys.stdin.read().strip()
    print(classify(text).to_json())
