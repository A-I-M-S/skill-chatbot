---
name: "skill-chatbot"
description: "Operate the SAAC FARM WhatsApp chatbot: start/stop bridge + orchestrator, watch logs, re-auth QR, run smoke tests, list GitHub issues, view deployment status."
---

# skill-chatbot

Project-management skill for the WA Farm Tour chatbot. The chatbot itself is a long-running service (Node `wa-bridge` + Python `orchestrator`); this skill is the operator surface — start, stop, observe, repair, ship.

> **The chat service is the work, not this skill.** This skill just drives the project: it does not parse customer messages, does not call the LLM, does not touch Qdrant or Outlook.

## When to invoke

- Status / health of the running chatbot
- Start or stop the bridge or orchestrator
- Tail logs (`wa-bridge`, `orchestrator`, smoke test)
- Re-run QR auth when the Baileys session drops
- Run a smoke test (NDJSON replay or end-to-end)
- Ingest a new FAQ source into Qdrant
- List / triage GitHub Issues for the project
- Pull latest and redeploy

Do **not** invoke to read or answer customer WhatsApp messages — that path is the running service.

## Required env

| Var | Source | Purpose |
|---|---|---|
| `CHATBOT_REPO` | `.env` | Repo root (default `~/projects/skill-chatbot`) |
| `WA_NOTIFY` | `.env` | Comma list of admin phone numbers (handoff alerts) |
| `ADMIN_CONTACT_NUMBER` | `.env` | Phone number the bot tells customers to call on handoff |
| `QDRANT_URL`, `QDRANT_API_KEY` | shared with `rag-qdrant` | RAG backend |
| `INFERENCE_BASE_URL`, `INFERENCE_API_KEY`, `INFERENCE_MODEL` | shared with `rag-qdrant` | LLM (MiniMax-M3 already authed) |
| `COMPOSIO_API_KEY`, `COMPOSIO_CONNECTED_ACCOUNT_ID` | shared with `farm-tour-booking` | Outlook calendar |

## Scripts (all CLI; one JSON line on stdout unless noted)

`scripts/` is auto-generated on first run from the templates in this skill.

```bash
SKILL=$CHATBOT_REPO/skill-chatbot-scripts   # created by `skill init`
```

### Status

```bash
python3 $SKILL/status.py
# → {"bridge": "up"|"down", "orchestrator": "up"|"down", "session": "ok"|"qr_needed",
#     "uptime_s": ..., "last_message_at": ..., "queue_depth": ...}
```

### Start / stop

```bash
python3 $SKILL/control.py start   # both daemons via systemd --user
python3 $SKILL/control.py stop
python3 $SKILL/control.py restart bridge
python3 $SKILL/control.py restart orchestrator
```

### Logs

```bash
python3 $SKILL/logs.py tail bridge --lines 200
python3 $SKILL/logs.py tail orchestrator --lines 200
python3 $SKILL/logs.py tail smoke
```

### Re-auth (Baileys session dropped)

```bash
python3 $SKILL/control.py auth-bridge
# prints QR to stdout, scan from WhatsApp app, waits for sync
```

### Smoke test

```bash
python3 $SKILL/smoke.py                # NDJSON replay
python3 $SKILL/smoke.py --live         # sends to the real WA number (use with care)
```

### Ingest helper

```bash
python3 $SKILL/ingest_rules.py         # one-shot: push booking_rules.yaml into the existing Qdrant collection
python3 $SKILL/ingest_file.py /path/to/faq.md
```

### Issues

```bash
python3 $SKILL/issues.py list
python3 $SKILL/issues.py show <number>
python3 $SKILL/issues.py create --title "..." --body "..." --label "phase:4-flows"
```

## Output contract

Always print **one JSON line on stdout** for scripts, plus optional human logs on stderr. Surface the `error` field for any failure. Never leak the WhatsApp session creds or `INFERENCE_API_KEY` in logs.

## Edge cases

- `bridge_down` — wa-bridge process not running; `control.py start` or check logs
- `qr_needed` — session invalid; run `auth-bridge`
- `composio_failed` — propagates from `farm-tour-booking`; the orchestrator will have already notified `WA_NOTIFY`
- `inference_429` — LLM rate-limited; the orchestrator retries with backoff and tells the user briefly
- `qdrant_unreachable` — orchestrator falls back to "I'll have the team reach out" and notifies admins

## Escalation to Boon

- WhatsApp session cannot be re-authed (number banned, hardware lost)
- Composio / Outlook account is down for >15 min during business hours
- Repeated LLM 4xx/5xx after backoff (likely model config drift)
- Customer is abusive → block via `python3 $SKILL/control.py block <phone>`

## Reference

- `docs/architecture.md` — full architecture + deployment topology
- `docs/ops.md` — systemd units, log rotation, restart-on-crash
- `docs/message-flows.md` — sample dialogues in EN and 中文
- README.md — what the chatbot does and how to run it
- GitHub: https://github.com/A-I-M-S/skill-chatbot · Issues: https://github.com/A-I-M-S/skill-chatbot/issues
