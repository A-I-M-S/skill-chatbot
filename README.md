# skill-chatbot

> WhatsApp chatbot for **SAAC FARM** tour — answers FAQs via Qdrant RAG, takes tour bookings via Outlook calendar, escalates the rest to a human.

## What it does

| Function | Backing skill | What the user gets |
|---|---|---|
| **FAQ** | [`rag-qdrant`](https://github.com/A-I-M-S/skill-chatbot) (Qdrant + FastEmbed E5 multilingual) | Grounded answer from the knowledge base. EN / 中文 |
| **Booking — new / edit / cancel** | `farm-tour-booking` (Composio Outlook, `outlook_24E83AD2E7F5F77D@outlook.com`) | Multi-turn flow, draft → user confirms "YES" → commits |
| **Handoff** | `WA_NOTIFY` env + `ADMIN_CONTACT_NUMBER` env | Out-of-scope (refund, complaint, custom pricing) → notify admins, tell customer to call |

All outbound is in **replies only** (within the 24h customer-service window) — no Meta-approved message templates required.

## Architecture

```
       customers ─┐                                    ┌─ admins
                  ▼                                    ▼
            ┌────────────────┐                  ┌────────────┐
            │  wa-bridge     │  HTTP / NDJSON   │  wa-bridge │
            │  (Node,        │ ───────────────▶ │  (same)    │
            │   Baileys)     │                  └────────────┘
            └────────────────┘
                    ▲
                    │ replies
            ┌───────┴──────────┐
            │  orchestrator    │  Python, LLM router (tool-calling)
            │  - state (SQLite)│  per-phone conversation memory
            │  - i18n EN/中文   │  dedupe by message_id
            └────┬──────────┬──┘
                 │          │
        ┌────────▼─┐   ┌────▼─────────────┐
        │ rag-     │   │ farm-tour-       │──▶ Composio ▶ Outlook
        │ qdrant   │   │ booking          │     (SAAC Tour calendar)
        │ (.ask)   │   │ (booking_flow.py)│
        └──────────┘   └──────────────────┘
```

Two daemons on the same box as the existing skills, talking over localhost.

| Process | Language | Job |
|---|---|---|
| `wa-bridge` | Node.js + TypeScript | Baileys session; receive / send WhatsApp messages |
| `orchestrator` | Python 3.11 | LLM intent + entity extraction, flow state, replies |

## Conversation model

Free-form, LLM-driven. The orchestrator's system prompt gives the LLM five tool choices:

1. `faq(question)` → `rag_qdrant.ask(question)`
2. `book_new(fields)` → multi-turn gather → `booking_flow.py new --confirm`
3. `book_edit(fields)` → lookup by phone → user picks event → `booking_flow.py edit --confirm`
4. `book_cancel(fields)` → lookup by phone → user picks event → `booking_flow.py cancel --confirm`
5. `handoff(reason)` → notify `WA_NOTIFY` numbers, reply with `ADMIN_CONTACT_NUMBER`

Anything off-scope (refund dispute, complaint, custom pricing, abuse) → handoff. Bot never improvises.

Language is **auto-detected** from the inbound message. The LLM replies in the same language (EN or 中文).

Destructive ops (edit / cancel) **always** require a plain `YES` reply to a confirmation prompt. Any other reply = abort.

## Repo layout

```
skill-chatbot/
├── README.md             # this file
├── SKILL.md              # openclaw skill wrapper (status, logs, deploy, smoke)
├── .env.example
├── .gitignore
├── docs/
│   ├── architecture.md
│   ├── ops.md
│   └── message-flows.md
├── wa-bridge/            # Node.js + Baileys
│   ├── package.json
│   ├── tsconfig.json
│   ├── src/
│   ├── bin/auth.ts
│   └── tests/
└── orchestrator/         # Python 3.11
    ├── pyproject.toml
    ├── src/
    │   ├── main.py
    │   ├── router.py
    │   ├── state.py
    │   ├── flows/
    │   ├── notify.py
    │   └── i18n.py
    ├── scripts/
    └── tests/
```

## Reuses (do not reinvent)

- **rag-qdrant** — `/root/.openclaw/skills/rag-qdrant/` · `from rag_qdrant import ask`
- **farm-tour-booking** — `/root/.openclaw/workspace/admin/skills/farm-tour-booking/` · invoked as `python3 scripts/booking_flow.py …`
- **booking_rules.yaml** — `config/booking_rules.yaml` in farm-tour-booking; source of truth for hours, capacity, pricing, blackout dates

`booking_flow.py list` does **not** support phone filtering. Per design decision the orchestrator pulls events and filters in-process (the farm-tour skill may migrate into this repo later — no patch to the upstream skill in v1).

## Quick start

```bash
# 1. Clone + env
git clone https://github.com/A-I-M-S/skill-chatbot
cd skill-chatbot
cp .env.example .env
# edit .env — see .env.example for all keys

# 2. wa-bridge (Node)
cd wa-bridge
npm install
npm run auth    # prints QR — scan from WhatsApp app
npm run dev     # start the bridge

# 3. orchestrator (Python)
cd ../orchestrator
python -m venv .venv && . .venv/bin/activate
pip install -e .
python -m src.main
```

See `docs/ops.md` for systemd units, log paths, restart, and the QR re-auth flow.

## Status

**v1 in development.** Tasks are tracked as GitHub Issues — see the [Issues](../../issues) tab. Opencode is driving implementation in a Plan → Build loop against the model `MiniMax-M3`.

## License

Proprietary — © A-I-M-S. Internal use only.
