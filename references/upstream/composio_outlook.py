#!/usr/bin/env python3
"""Composio Outlook client (read/write calendar events) for farm-tour-booking.

Reads COMPOSIO_API_KEY from env. If COMPOSIO_CONNECTED_ACCOUNT_ID is not set,
auto-resolves the active Outlook account on first call and caches it. Set
COMPOSIO_CONNECTED_ACCOUNT_ID explicitly only if you have multiple Outlook
accounts linked and want to pin to a specific one.

Never hardcodes, never commits, never writes either secret to disk.

Verified tool slugs (tested end-to-end 2026-06-21):
  OUTLOOK_OUTLOOK_LIST_EVENTS
  OUTLOOK_OUTLOOK_GET_EVENT
  OUTLOOK_OUTLOOK_CALENDAR_CREATE_EVENT
  OUTLOOK_OUTLOOK_UPDATE_CALENDAR_EVENT
  OUTLOOK_OUTLOOK_DELETE_EVENT
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://backend.composio.dev/api/v3"
log = logging.getLogger("composio_outlook")

# Cached resolved Outlook account (process-lifetime).
_cached: tuple[str, str] | None = None  # (connected_account_id, entity_id)


class ComposioError(RuntimeError):
    def __init__(self, slug: str, payload: dict):
        self.slug = slug
        self.payload = payload
        msg = payload.get("error") if isinstance(payload.get("error"), str) else payload
        super().__init__(f"composio tool {slug!r} failed: {msg}")


def _resolve_outlook_ca_id(api_key: str) -> tuple[str, str]:
    """Find the active Outlook connected account and entity id for this API key.

    Returns (connected_account_id, entity_id). entity_id is Composio v3's required
    per-request user identifier; we pull it from the account's `user_id` field.
    Override via COMPOSIO_ENTITY_ID env var if your account's user_id is not
    the value you want to send (rare).

    Errors clearly when zero or multiple Outlook accounts are linked so the
    operator can either link a single account or set COMPOSIO_CONNECTED_ACCOUNT_ID
    to disambiguate. Result is cached for the process lifetime.
    """
    global _cached
    if _cached:
        return _cached
    r = requests.get(
        f"{BASE_URL}/connected_accounts?limit=100",
        headers={"x-api-key": api_key}, timeout=15,
    )
    r.raise_for_status()
    all_accounts = r.json().get("items", [])
    outlook_active = [
        a for a in all_accounts
        if (a.get("toolkit", {}) or {}).get("slug") == "outlook"
        and a.get("status") == "ACTIVE"
    ]
    if not outlook_active:
        raise ComposioError(
            "env",
            {"error": "no active Outlook account linked. Link one in the Composio dashboard, then re-run."},
        )
    if len(outlook_active) > 1:
        listing = "\n".join(
            f"  {a['id']}  user_id={a.get('user_id', '?')}" for a in outlook_active
        )
        raise ComposioError(
            "env",
            {"error": f"multiple active Outlook accounts linked. Set COMPOSIO_CONNECTED_ACCOUNT_ID (and optionally COMPOSIO_ENTITY_ID) to one of:\n{listing}"},
        )
    a = outlook_active[0]
    entity_id = os.environ.get("COMPOSIO_ENTITY_ID") or a.get("user_id")
    if not entity_id:
        raise ComposioError(
            "env",
            {"error": f"could not resolve entity_id for account {a['id']}. Set COMPOSIO_ENTITY_ID explicitly."},
        )
    _cached = (a["id"], entity_id)
    log.info("auto-resolved Outlook account: %s entity_id=%s", a["id"], entity_id)
    return _cached


def _env() -> tuple[str, str, str]:
    api_key = os.environ.get("COMPOSIO_API_KEY")
    if not api_key:
        raise ComposioError("env", {"error": "missing env: COMPOSIO_API_KEY"})
    explicit_ca = os.environ.get("COMPOSIO_CONNECTED_ACCOUNT_ID")
    if explicit_ca:
        explicit_entity = os.environ.get("COMPOSIO_ENTITY_ID")
        if explicit_entity:
            return api_key, explicit_ca, explicit_entity
        # Resolve entity_id from the explicitly-pinned account
        rr = requests.get(
            f"{BASE_URL}/connected_accounts/{explicit_ca}",
            headers={"x-api-key": api_key}, timeout=15,
        )
        rr.raise_for_status()
        a = rr.json()
        if isinstance(a, dict) and a.get("user_id"):
            return api_key, explicit_ca, a["user_id"]
        raise ComposioError(
            "env",
            {"error": f"could not resolve entity_id for {explicit_ca}. Set COMPOSIO_ENTITY_ID explicitly."},
        )
    ca_id, entity_id = _resolve_outlook_ca_id(api_key)
    return api_key, ca_id, entity_id


def _exec(slug: str, arguments: dict[str, Any]) -> dict:
    api_key, ca_id, entity_id = _env()
    log.info("composio %s args_keys=%s", slug, sorted(arguments))
    r = requests.post(
        f"{BASE_URL}/tools/execute/{slug}",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json={"connected_account_id": ca_id, "entity_id": entity_id, "arguments": arguments},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    if not body.get("successful"):
        raise ComposioError(slug, body)
    return (body.get("data") or {}).get("response_data") or {}


def list_events(start: datetime, end: datetime, top: int = 50) -> list[dict]:
    # TODO: Composio's OUTLOOK_OUTLOOK_LIST_EVENTS tool currently ignores
    # start_date/end_date and does not expose @odata.nextLink for pagination.
    # We fetch with a large top and filter locally. When the account exceeds
    # 1000 events, this will silently drop older ones — move to a direct
    # Graph API call (or a Composio tool that supports pagination) at that point.
    raw = _exec("OUTLOOK_OUTLOOK_LIST_EVENTS", {
        "start_date": start.astimezone(timezone.utc).isoformat(),
        "end_date": end.astimezone(timezone.utc).isoformat(),
        "top": 1000,
        "calendar_id": None,
    }).get("value", [])
    if len(raw) >= 1000:
        log.warning(
            "list_events: got %d events back (>= 1000 cap). Some may be missing — "
            "consider migrating off this Composio tool.", len(raw),
        )
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    filtered: list[dict] = []
    for e in raw:
        start_obj = e.get("start") or {}
        dts = start_obj.get("dateTime")
        if not dts:
            continue
        try:
            # Outlook returns either "...Z", "...+00:00", or naive. When naive,
            # the timeZone field tells us the wall-clock zone (e.g. our writes
            # now store "Asia/Singapore" — legacy events are "UTC").
            dt = datetime.fromisoformat(dts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            tz_name = (start_obj.get("timeZone") or "UTC").strip()
            try:
                dt = dt.replace(tzinfo=ZoneInfo(tz_name))
            except Exception:
                dt = dt.replace(tzinfo=timezone.utc)
        dt_utc = dt.astimezone(timezone.utc)
        if start_utc <= dt_utc <= end_utc:
            filtered.append(e)
    return filtered[:top]


def get_event(event_id: str) -> dict:
    return _exec("OUTLOOK_OUTLOOK_GET_EVENT", {"event_id": event_id})


def _to_naive_in_zone(dt: datetime, timezone_name: str) -> datetime:
    """Return dt as a naive datetime in the named IANA zone, for Graph API.

    Graph API stores events in the wall-clock time of the named zone when the
    dateTime is naive + timeZone is set. If the dateTime carries its own offset,
    Graph normalizes to UTC for storage instead — meaning the calendar shows
    a different wall-clock time in any non-UTC viewer. Strip the offset and
    convert to the target zone first.
    """
    zone = ZoneInfo(timezone_name)
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(zone).replace(tzinfo=None)


def create_event(
    *,
    subject: str,
    body: str,
    start: datetime,
    end: datetime,
    location: str | None = None,
    attendees: list[dict] | None = None,
    timezone_name: str = "Asia/Singapore",
    html: bool = False,
    online_meeting: bool = False,
) -> dict:
    args: dict[str, Any] = {
        "subject": subject,
        "body": body,
        "start_datetime": _to_naive_in_zone(start, timezone_name).isoformat(),
        "end_datetime": _to_naive_in_zone(end, timezone_name).isoformat(),
        "time_zone": timezone_name,
        "is_html": html,
    }
    if location:
        args["location"] = location
    if attendees:
        args["attendees_info"] = attendees
    if online_meeting:
        args["is_online_meeting"] = True
    return _exec("OUTLOOK_OUTLOOK_CALENDAR_CREATE_EVENT", args)


def update_event(event_id: str, **fields: Any) -> dict:
    if "start" in fields:
        tz = fields.get("timezone_name", "Asia/Singapore")
        fields["start_datetime"] = _to_naive_in_zone(fields.pop("start"), tz).isoformat()
    if "end" in fields:
        tz = fields.get("timezone_name", "Asia/Singapore")
        fields["end_datetime"] = _to_naive_in_zone(fields.pop("end"), tz).isoformat()
    if "timezone_name" in fields:
        fields["time_zone"] = fields.pop("timezone_name")
    if "html" in fields:
        fields["is_html"] = fields.pop("html")
    fields["event_id"] = event_id
    return _exec("OUTLOOK_OUTLOOK_UPDATE_CALENDAR_EVENT", fields)


def delete_event(event_id: str) -> None:
    _exec("OUTLOOK_OUTLOOK_DELETE_EVENT", {"event_id": event_id})


if __name__ == "__main__":
    from datetime import timedelta
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=7)
        events = list_events(start, end)
        print(f"events in next 7d: {len(events)}")
        for e in events:
            print(f"  {e.get('start', {}).get('dateTime')}  {e.get('subject')}")
    else:
        print(f"unknown cmd: {cmd}", file=sys.stderr)
        sys.exit(2)
