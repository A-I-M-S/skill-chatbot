#!/usr/bin/env python3
"""Booking flow orchestrator for farm-tour-booking.

Reads config/booking_rules.yaml. Calls composio_outlook for calendar ops.
Returns a single line of JSON to stdout. Errors are friendly, the agent
delivers the `reply` field verbatim to the user.

Two-step confirmation: omit --confirm to get a draft back. Pass --confirm
to actually commit the change. Cancel REQUIRES --confirm.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from dateutil import parser as dateparser

sys.path.insert(0, str(Path(__file__).parent))
import composio_outlook  # noqa: E402

log = logging.getLogger("booking_flow")

RULES_PATH = Path(
    os.environ.get(
        "BOOKING_RULES_PATH",
        str(Path(__file__).parent.parent / "config" / "booking_rules.yaml"),
    )
)

DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SGT = timezone(timedelta(hours=8))


# ---------- rules + helpers ----------

def load_rules() -> dict:
    if not RULES_PATH.exists():
        return {}
    with RULES_PATH.open() as f:
        return yaml.safe_load(f) or {}


def _is_blackout(d: datetime, rules: dict) -> bool:
    for b in rules.get("blackout_dates", []) or []:
        try:
            if datetime.fromisoformat(b).date() == d.date():
                return True
        except ValueError:
            continue
    return False


def _operating_hours_for(d: datetime, rules: dict) -> tuple[int, int] | None:
    hours = rules.get("operating_hours", {}) or {}
    window = hours.get(DAY_KEYS[d.weekday()])
    if not window:
        return None
    sh, sm = map(int, window[0].split(":"))
    eh, em = map(int, window[1].split(":"))
    return sh * 60 + sm, eh * 60 + em


def _within_hours(d: datetime, rules: dict) -> bool:
    window = _operating_hours_for(d, rules)
    if not window:
        return False
    minutes = d.hour * 60 + d.minute
    return window[0] <= minutes <= window[1]


def _slot_end(start: datetime, rules: dict) -> datetime:
    return start + timedelta(minutes=int(rules.get("slot_duration_minutes", 90)))


def _parse_date(s: str) -> datetime:
    dt = dateparser.parse(s, fuzzy=True)
    if dt is None:
        raise ValueError(f"could not parse date: {s!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SGT)
    return dt


def _pax_in_window(start: datetime, end: datetime) -> int:
    events = composio_outlook.list_events(start, end, top=100)
    total = 0
    for e in events:
        haystack = f"{e.get('subject', '')} {e.get('bodyPreview', '')}"
        m = re.search(r"(\d+)\s*pax", haystack, re.IGNORECASE)
        if m:
            try:
                total += int(m.group(1))
            except ValueError:
                continue
    return total


# ---------- operations ----------

def op_new(args: argparse.Namespace) -> dict:
    rules = load_rules()
    try:
        start = _parse_date(f"{args.date} {args.time}")
    except ValueError as e:
        return {"error": "bad_date", "reply": f"Could not parse the date: {e}"}

    pax = int(args.pax)
    max_cap = int(rules.get("max_capacity_per_slot", 0))
    if max_cap and pax > max_cap:
        return {
            "error": "over_capacity",
            "reply": f"Sorry, max group size is {max_cap}. For larger groups, message Boon directly.",
        }
    if _is_blackout(start, rules):
        return {"error": "blackout", "reply": f"Sorry, we're closed on {start.strftime('%d %b %Y')}. Pick another date?"}
    if not _within_hours(start, rules):
        return {
            "error": "outside_hours",
            "reply": "Tours run during operating hours — check config/booking_rules.yaml for the schedule.",
        }

    end = _slot_end(start, rules)
    current = _pax_in_window(start, end)
    if max_cap and current + pax > max_cap:
        return {
            "error": "no_capacity",
            "reply": f"Sorry, {start.strftime('%d %b %H:%M')} is fully booked ({current} pax already). Try another time?",
        }

    org = args.org or "Group"
    subject = f"Farm tour — {org} — {pax} pax — {start.strftime('%d %b %Y %H:%M')}"
    body_lines = [
        "Booked via BlueAcres / SAAC FARM bot",
        f"Organisation: {org}",
        f"Pax: {pax}",
        f"Contact: {args.contact_name or ''} <{args.contact_email or ''}> {args.contact_phone or ''}".strip(),
    ]
    if args.notes:
        body_lines.append(f"Notes: {args.notes}")
    deposit = rules.get("deposit") or {}
    if deposit.get("required"):
        body_lines += [
            "",
            f"Deposit: SGD {deposit.get('amount_sgd')} — {deposit.get('instructions', 'TBD')}",
            f"Deadline: {deposit.get('deadline_hours_before_slot', 48)}h before slot",
        ]
    body = "\n".join(body_lines)

    if not args.confirm:
        return {
            "error": "needs_confirm",
            "reply": "draft only — pass --confirm to commit",
            "draft": {"subject": subject, "body": body, "start": start.isoformat(), "end": end.isoformat()},
        }

    attendees = []
    if args.contact_email:
        attendees.append({"email": args.contact_email, "name": args.contact_name or "", "type": "required"})

    event = composio_outlook.create_event(
        subject=subject, body=body, start=start, end=end,
        location=rules.get("location_default", "SAAC FARM"),
        attendees=attendees,
    )
    reply = (
        f"Booked! {pax} pax on {start.strftime('%a %d %b %Y, %H:%M')} SGT at "
        f"{rules.get('location_default', 'SAAC FARM')}.\n"
    )
    if deposit.get("required"):
        reply += (
            f"Deposit: SGD {deposit.get('amount_sgd')}, due {deposit.get('deadline_hours_before_slot', 48)}h before. "
            f"{deposit.get('instructions', '')}"
        )
    return {"event_id": event.get("id"), "reply": reply.strip()}


def op_edit(args: argparse.Namespace) -> dict:
    if not args.event_id:
        return {"error": "no_event", "reply": "Need an event id. Run `booking_flow.py list` first."}
    fields: dict = {}
    if args.date or args.time:
        date_str = args.date or "today"
        time_str = args.time or "10:00"
        try:
            new_start = _parse_date(f"{date_str} {time_str}")
        except ValueError as e:
            return {"error": "bad_date", "reply": f"Could not parse the date: {e}"}
        rules = load_rules()
        fields["start"] = new_start
        fields["end"] = _slot_end(new_start, rules)
    if not fields:
        return {"error": "no_change", "reply": "Nothing to change."}
    if not args.confirm:
        return {"error": "needs_confirm", "reply": "draft only — pass --confirm to commit", "draft": {k: v.isoformat() if isinstance(v, datetime) else v for k, v in fields.items()}}
    event = composio_outlook.update_event(args.event_id, **fields)
    return {
        "event_id": event.get("id"),
        "reply": f"Updated. New time: {event.get('start', {}).get('dateTime')}",
    }


def op_cancel(args: argparse.Namespace) -> dict:
    if not args.event_id:
        return {"error": "no_event", "reply": "Need an event id."}
    if not args.confirm:
        return {"error": "needs_confirm", "reply": "refusing to cancel without --confirm"}
    composio_outlook.delete_event(args.event_id)
    return {"reply": "Cancelled. Sorry to hear — message back anytime to rebook."}


def op_list(args: argparse.Namespace) -> dict:
    try:
        start = _parse_date(f"{args.frm} 00:00")
        end = _parse_date(f"{args.to} 23:59")
    except ValueError as e:
        return {"error": "bad_date", "reply": f"Could not parse the date range: {e}"}
    events = composio_outlook.list_events(start, end, top=200)
    return {
        "events": [
            {
                "id": e.get("id"),
                "subject": e.get("subject"),
                "start": e.get("start", {}).get("dateTime"),
                "end": e.get("end", {}).get("dateTime"),
            }
            for e in events
        ]
    }


# ---------- main ----------

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new")
    p_new.add_argument("--date", required=True)
    p_new.add_argument("--time", required=True)
    p_new.add_argument("--pax", required=True)
    p_new.add_argument("--org")
    p_new.add_argument("--contact-name")
    p_new.add_argument("--contact-email")
    p_new.add_argument("--contact-phone")
    p_new.add_argument("--notes")
    p_new.add_argument("--confirm", action="store_true")

    p_edit = sub.add_parser("edit")
    p_edit.add_argument("--event-id", required=True)
    p_edit.add_argument("--date")
    p_edit.add_argument("--time")
    p_edit.add_argument("--pax")
    p_edit.add_argument("--confirm", action="store_true")

    p_cancel = sub.add_parser("cancel")
    p_cancel.add_argument("--event-id", required=True)
    p_cancel.add_argument("--confirm", action="store_true")

    p_list = sub.add_parser("list")
    p_list.add_argument("--from", dest="frm", required=True)
    p_list.add_argument("--to", required=True)

    args = ap.parse_args()
    ops = {"new": op_new, "edit": op_edit, "cancel": op_cancel, "list": op_list}
    result = ops[args.cmd](args)
    print(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
