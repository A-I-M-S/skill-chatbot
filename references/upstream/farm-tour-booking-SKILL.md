---
name: "farm-tour-booking"
description: "Manage SAAC FARM tour bookings via Composio Outlook. Detect booking intent, create/update/delete calendar events, return confirmations + deposit instructions."
---

# farm-tour-booking

Detect BlueAcres/SAAC FARM farm-tour booking intent on incoming messages, drive the corresponding Outlook calendar event via Composio, and reply with confirmations + payment instructions.

**Calendar:** `outlook_24E83AD2E7F5F77D@outlook.com` ("SAAC Tour"). All baadminbot users share one Composio API key + one Outlook account — anyone can read or edit any event.

## When to invoke

Invoke on any inbound message that contains:

- **Booking intent:** book, reserve, sign up, visit, tour, school trip, "can we come"
- **Edit intent:** change, reschedule, move, switch, update, "instead", "can we do"
- **Cancel intent:** cancel, refund, can't make it, no longer need, won't be coming
- **Inquiry:** prices, cost, opening hours, where, address, available

Do NOT invoke for: general knowledge, refund disputes, complaints about past tours, custom pricing, or anything outside farm-tour scope — those escalate to Boon.

## Required env (in the shell that runs the scripts)

- `COMPOSIO_API_KEY` — `ak_…` from https://app.composio.dev → Settings → API Keys. Required.
- `COMPOSIO_CONNECTED_ACCOUNT_ID` — `ca_…` of the linked Outlook account. Optional; auto-resolved from the API key on first call when only one Outlook account is linked.
- `COMPOSIO_ENTITY_ID` — Optional. Composio v3 requires this per request; `composio_outlook.py` auto-fills it from the account's `user_id` field, so you only need to set it if auto-resolve fails (rare).

Secrets are read from env only — never hardcoded, never written to disk.

## Required config

`config/booking_rules.yaml` (path overridable via `BOOKING_RULES_PATH`). Contains: location, timezone, per-day operating hours, slot duration, max capacity per slot, pricing tiers, blackout dates, deposit rules. Edit by hand — file is small and well-commented.

## Scripts (all CLI; each prints one JSON line on stdout)

```bash
SKILL=/root/.openclaw/workspace/admin/skills/farm-tour-booking/scripts
```

### Step 1 — Classify the message

```bash
echo "<raw message text>" | python3 $SKILL/intent.py
```

Returns: `{"intent": "new_booking|edit_booking|cancel_booking|inquiry|other", "confidence": 0.0–1.0, "fields": {...}}`

If `intent` is `other` and the message clearly meant booking, the agent (LLM) may override based on context.

### Step 2a — new_booking

Required fields before commit: date, time, pax, contact email or phone. Gather missing ones via conversation.

```bash
python3 $SKILL/booking_flow.py new \
  --date "2026-08-15" --time "10:30" --pax 30 \
  --org "Acme School" \
  --contact-name "Jane Doe" --contact-email "jane@acme.edu" --contact-phone "+6591234567" \
  --notes "Primary 5 science camp"
# draft returned — show user, then re-run with --confirm to commit
```

Two-step: omit `--confirm` for draft, add it to commit. On success the JSON contains `event_id` + a friendly `reply` for the user (deposit instructions, location, deadline).

### Step 2b — edit_booking / cancel_booking

```bash
python3 $SKILL/booking_flow.py list --from "2026-08-01" --to "2026-08-31"
# returns events[] — match by contact email or org + date

python3 $SKILL/booking_flow.py edit --event-id "AQMk..." \
  --date "2026-08-17" --time "10:30" --confirm

python3 $SKILL/booking_flow.py cancel --event-id "AQMk..." --confirm
```

`--confirm` is **mandatory** for cancel — never cancel on inference alone.

### Step 2c — inquiry

Answer from `config/booking_rules.yaml` directly. Use `list` only if the user asks about real-time slot availability.

## Output contract

Deliver the JSON's `reply` field **verbatim** to the user. The `event_id` is for internal logging. The `error` field is for agent routing only — never surface the raw error code.

If the response includes a calendar photo attachment (not currently the case), emit one `MEDIA:<path>` line per path on its own line.

## Edge cases (all return JSON with `error` + a user-friendly `reply`)

- `no_capacity` — slot at/over max for that window
- `blackout` — date in `blackout_dates`
- `outside_hours` — start outside `operating_hours` for that weekday
- `over_capacity` — group size > `max_capacity_per_slot`
- `ambiguous` — multiple events match edit/cancel criteria
- `no_event` — event id missing for edit/cancel
- `needs_confirm` — destructive op called without `--confirm`
- `composio_failed` — Composio API error (network, auth, rate limit)
- `env_missing` — `COMPOSIO_API_KEY` not set

## Known quirks to remember

- **Composio `OUTLOOK_OUTLOOK_LIST_EVENTS` ignores `start_date`/`end_date`.** The skill's `list_events` fetches with `top=1000` and filters locally in Python. If the account ever exceeds 1000 events, migrate to direct Graph API.
- **Outlook dateTime fields are naive when `timeZone` is `"UTC"`.** The local filter assumes UTC for naive datetimes (correct for Composio's normalization).
- **Smoke test creates real Outlook events.** Always run with try/finally — `scripts/smoke_test.py` already does this.

## Escalation to Boon

Hand off via `sessions_send` to Boon's main session when:

- Customer asks for a refund or disputes a charge
- Customer complains about a past tour
- Booking requires custom pricing (large group, special needs)
- Two confirmations in a row fail (user can't decide)
- Composio is unreachable for >2 minutes

Pass a short summary + the original customer message.

## Verification

```bash
COMPOSIO_API_KEY=*** python3 $SKILL/smoke_test.py
```

Should print `SMOKE TEST OK` and leave zero test events in the calendar. Also a CLI listing:

```bash
python3 $SKILL/composio_outlook.py list
```

Should print `events in next 7d: N` without error.

## Files

- `scripts/composio_outlook.py` — REST client (5 verified Composio Outlook tools, auto-resolves entity_id)
- `scripts/intent.py` — rule-based intent classifier
- `scripts/booking_flow.py` — orchestrator (rules + capacity + create/update/delete)
- `scripts/smoke_test.py` — end-to-end create→list→update→delete round-trip
- `config/booking_rules.yaml` — operator-editable SAAC FARM rules
