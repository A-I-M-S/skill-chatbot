# Cutover smoke test (issue #33)

End-to-end validation against the test WhatsApp number. No real customer traffic. Pass = green light for the cutover (issue #36).

## Pre-conditions (verify all before starting)

- [ ] `git checkout main && git pull` — clean working tree, latest merged
- [ ] `systemctl status skill-chatbot-{wa-bridge,orchestrator}` — both `active (running)`
- [ ] `curl -sf http://127.0.0.1:7789/health` → `{"status":"ok",...}`
- [ ] wa-bridge paired with the test WhatsApp number (journal shows the auth state, no recent QR prompts)
- [ ] `/etc/skill-chatbot.env` has all keys filled (no `unset` values)
- [ ] `ADMIN_HTTP_TOKEN` matches between orchestrator's env and the test baadminbot's `admin-bot/.env`
- [ ] `ADMIN_TELEGRAM_IDS` matches both sides
- [ ] `WA_NOTIFY` has your test admin number + 1 backup
- [ ] Second WA number available for sending test messages (NOT the test number — to keep logs clean)
- [ ] baadminbot OpenClaw running, `admin-bot` skill symlinked and `.env` filled
- [ ] `make baseline-capture` ran today — `migration-baseline/<timestamp>.md` exists
- [ ] Qdrant nightly snapshot succeeded in the last 24h (`make snapshot-now` to force)

## Test cases

Run in order. Each case: send → wait for reply → mark PASS/FAIL with note.

### Customer side — second WA number

| # | Input | Expected | Verify |
|---|---|---|---|
| C1 | `hi` or `what tours do you offer?` | Grounded answer from Qdrant, no hallucinated prices or dates | Reply cites at least one ingested chunk; no made-up tour names |
| C2 | `can I book 20 pax Saturday 10:30?` | Reply confirms tentative booking, asks for contact email/phone | Reply includes "I'll need an email to confirm" or similar; no Outlook event yet |
| C3 | Reply with the email you control | Outlook event created in SAAC Tour calendar; reply includes deposit instructions | `GET /admin/bookings?date=<saturday>` shows the event (via baadminbot `/bookings <saturday>`) |
| C4 | `actually can we do Sunday 14:00 instead?` | Event moved to Sunday 14:00 | Saturday's event gone; Sunday's exists |
| C5 | `we can't make it, cancel` | Event deleted | Both dates return empty |
| C6 | `what's the price for 25 people?` | Grounded answer with the correct pricing tier from Qdrant | Reply matches a pricing_tier in `booking_rules.yaml` |
| C7 | Out-of-scope question (`who's the CEO of Microsoft?`) | Polite refusal / escalation to admin, no hallucination | Reply doesn't contain hallucinated facts; mentions `ADMIN_CONTACT_NUMBER` on escalation |

### Admin side — DM baadminbot

| # | Command | Expected | Verify |
|---|---|---|---|
| A1 | `/show access` | ACL table | One row per source ingested so far; shape matches rag-qdrant |
| A2 | `/bookings` (no date) | Bot asks for date | Reply is `which date? (YYYY-MM-DD)` |
| A3 | `/bookings <saturday>` (post-C5) | Empty list | Reply is `No bookings on <saturday>.` |
| A4 | `/ingest <test-doc-url>` | Success, ingestion count reported | New chunk retrievable via C1-style query (Qdrant collection name unchanged) |
| A5 | `/config slot_duration_minutes 45` | Success | `booking_rules.yaml` reflects the change after restart; bot reply confirms |
| A6 | `/config location "Mars"` | Rejected (not in allowlist) | Error message lists allowed keys |
| A7 | From a **non-admin** telegram id: `/show access` | Refusal, no API call made | Bot reply is `Refused: you're not an admin.`; no entry in orchestrator access log |

## Pass criteria

- All 14 cases PASS
- No 500s in `journalctl -u skill-chatbot-orchestrator --since "1 hour ago"`
- Qdrant ingest in A4 lands in the same collection as production data (verify by `QDRANT_COLLECTION` env var unchanged from baseline)
- `/config` in A5 actually changes `booking_rules.yaml`; restart orchestrator, confirm the new value is loaded (`GET /admin/config` round-trip via a follow-up PATCH/GET, or just `grep` the file)

## Failure-mode observations (record if hit)

If any case fails, note:

- **Which case** (C1..C7, A1..A7)
- **Input verbatim** (what was sent)
- **Reply verbatim** (what came back)
- **Logs** (paste the relevant `journalctl` lines, NOT a full log dump)
- **Hypothesis** (one sentence: "I think this failed because X")

Do NOT diagnose in-line during the run. Run all 14 cases, then triage.

## Output

Commit `migration-baseline/smoke-test-report-<timestamp>.md` to the repo with the table below filled in.

```markdown
# Smoke test report — <date>

- **Tester**: <name>
- **Time**: <UTC>
- **Stack**: orchestrator <commit>, wa-bridge <commit>, admin-bot <commit>
- **WhatsApp number**: <last 4 digits only>
- **Result**: GO | NO-GO

## Per-case results

| # | Status | Note |
|---|---|---|
| C1 | PASS/FAIL | <short note> |
| C2 | PASS/FAIL | |
| ... | ... | ... |
| A7 | PASS/FAIL | |

## Verdict

<one paragraph: what worked, what didn't, what needs follow-up>
```

## Reporting back

- **GO**: comment on the migration epic (#29) with the PR URL of the report commit. Then proceed to issue #36 (cutover runbook).
- **NO-GO**: open a new issue per failing case with the failure-mode observations. Do NOT proceed to cutover.

## What's NOT in scope for this smoke

- Load testing (single-message smoke; production load assumed low for v1)
- Failure-mode testing (Qdrant down, MiniMax timeout) — covered in the cutover runbook
- Multi-language regression (EN/中文) — covered by orchestrator's i18n tests
