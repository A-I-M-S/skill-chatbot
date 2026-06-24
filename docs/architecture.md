# Architecture

## Topology

Two long-lived daemons on the same host as the existing `rag-qdrant` and `farm-tour-booking` skills.

```
┌────────────────────────────────────────────────────────────────────┐
│  host (same box as rag-qdrant + farm-tour-booking)                 │
│                                                                    │
│  ┌──────────────────────┐    NDJSON tail / HTTP     ┌────────────┐  │
│  │  wa-bridge (Node)    │ ────────────────────────▶ │ orchestr…  │  │
│  │  Baileys socket      │ ◀──── POST /send ──────── │ (Python)   │  │
│  │  /health, /status    │                          │            │  │
│  └──────────┬───────────┘                          └──────┬─────┘  │
│             │                                              │        │
└─────────────┼──────────────────────────────────────────────┼────────┘
              │                                              │
              │ WebSocket to WhatsApp                        │
              ▼                                              ▼
         ┌─────────┐                              ┌──────────────────────┐
         │  WA     │                              │  rag_qdrant.ask()    │
         │  users  │                              │  booking_flow.py …   │
         └─────────┘                              │  (subprocess)        │
                                                 └──────────┬───────────┘
                                                            │
                                                            ▼
                                                ┌──────────────────────┐
                                                │  Qdrant + Composio   │
                                                │  (existing services) │
                                                └──────────────────────┘
```

## Why two daemons

- **wa-bridge** owns the Baileys socket. It's a single-threaded, event-driven Node process. Restarting it must not drop messages, so the inbox is a tail-able NDJSON file (durable across restart).
- **orchestrator** owns the LLM logic + per-phone conversation state. It can be restarted independently of the bridge (e.g. after a code change) without disconnecting WhatsApp.

## Transport choices

| Choice | Why |
|---|---|
| Baileys (not Meta Cloud API) | No Meta verification, free, single number, replies-only = no template approval |
| NDJSON inbox (not Redis) | Zero extra deps, durable, debuggable, sufficient for v1 volume |
| SQLite state (not Postgres) | Single-process orchestrator, easy backup, WAL for concurrent reads |
| Subprocess to `booking_flow.py` (not import) | Keeps the farm-tour skill as the source of truth; orchestrator stays thin |

## Conversation state

- One row per phone number in `state.sqlite`:
  - `phone` (PK)
  - `flow` (`faq` | `book_new` | `book_edit` | `book_cancel` | `handoff` | `idle`)
  - `draft` (JSON blob of partially-collected fields)
  - `pending_confirm` (event_id or draft awaiting YES)
  - `language` (`en` | `zh`)
  - `last_message_id` (for dedupe)
  - `last_message_at` (ISO)
  - `history` (JSON array of last N turns; default 8)

## Idempotency

Every inbound `message_id` is recorded; re-deliveries from Baileys are dropped before they reach the LLM.

## Multilingual

The orchestrator's system prompt instructs the LLM to:

1. Detect the language of the inbound message.
2. Set `state.language`.
3. Reply in the same language.

System prompts and tool descriptions are loaded from `orchestrator/src/i18n.py` and include both EN and 中文 variants.

## Guardrails

- LLM has exactly five tool choices: `faq`, `book_new`, `book_edit`, `book_cancel`, `handoff`.
- Anything that doesn't fit → `handoff` (notify admins, give customer the `ADMIN_CONTACT_NUMBER`).
- Destructive ops (`book_edit`, `book_cancel`) require an explicit `YES` reply to a confirmation prompt; any other reply = abort.
- Edit / cancel lookup: orchestrator pulls `list` events in the next `BOOKING_HORIZON_DAYS` and filters in-process by the caller's phone number (no patch to `booking_flow.py`).

## Handoff

Out-of-scope (refund dispute, complaint, custom pricing, abusive user) →

1. Send a WhatsApp message to every number in `WA_NOTIFY` (comma-split, trim, skip blanks) with: caller phone, original message, detected reason, timestamp.
2. Reply to the caller: "I've flagged this for the team. For immediate help, contact +65XXXXXXXX (`ADMIN_CONTACT_NUMBER`)."

## Caching

The orchestrator imports `rag_qdrant` directly and uses its `ask()` function, so `SEMANTIC_CACHE_ENABLED` and `SEARCH_CACHE_ENABLED` from the existing `.env` apply automatically — no double-cache.

## Observability

- `bridge.log` — Baileys events, send/recv, errors
- `orchestrator.log` — per-message: phone, detected intent, tool called, latency, tokens
- `state.sqlite` — every state transition logged in a `state_log` table (insert-only, time + phone + flow + diff)
- `inbox.ndjson` — durable record of every inbound message for replay

## Related docs

- [message-flows.md](message-flows.md) — sample EN + 中文 dialogues for each flow and handoff reason
- [runbook.md](runbook.md) — operator playbook for the 12 common incidents (bridge down, session lost, LLM 429 storm, …)
- [security.md](security.md) — what is logged, who can read `state.sqlite`, secret rotation, PII erasure
- [ops.md](ops.md) — systemd units, smoke test, backups
