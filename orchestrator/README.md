# orchestrator

Python 3.11 orchestrator for skill-chatbot.

- NDJSON tailer (via `pygtail`) over `wa-bridge/inbox.ndjson`.
- SQLite WAL state with `PhoneState` + per-message `state_log`.
- LLM router with tool-calling (`openai` SDK; `pydantic` schema validation).
- Flows: FAQ, booking new / edit / cancel, handoff, admin notify.
- i18n: EN + 中文, language auto-detected from the inbound message.
- Admin HTTP sub-app at `/admin/*` (issue #30) — token + telegram-id
  authenticated, reuses Qdrant + Composio for ACL ingest and bookings.

## Layout

```
orchestrator/
├── pyproject.toml
├── ruff.toml
├── Makefile
├── .env.example
├── src/                 # python package; entrypoint `python -m src.main`
│   ├── main.py
│   ├── tail.py
│   ├── state.py
│   ├── http.py
│   ├── router.py
│   ├── inference.py
│   ├── rag.py
│   ├── booking_subprocess.py
│   ├── i18n.py
│   ├── language.py
│   ├── notify.py
│   ├── enums.py
│   ├── admin/            # issue #30: HTTP sub-app
│   │   ├── __init__.py   # auth + dispatch table
│   │   ├── handlers.py   # one function per route
│   │   └── yaml_patch.py # ruamel round-trip for booking_rules.yaml
│   ├── prompts/         # router_en.py / router_zh.py
│   └── flows/           # faq, booking_new, booking_edit, booking_cancel, handoff, confirm
├── scripts/             # smoke.py, ingest_rules.py, ingest_file.py, reindex.py
└── tests/
    └── admin/           # unit tests for the admin sub-app
```

## Dev loop

From the repo root: `make orch-venv` then `make orch-install` then `make orch-dev`. From this dir: `make venv` then `make install` then `make dev`.

## Test

`make orch-test` (or `make test-cov` for coverage).

## Wiring the two customer features (RAG + booking)

The bot behind the WhatsApp number does exactly two things for customers —
**answer FAQs (RAG)** and **book/edit/cancel farm tours** — plus a `handoff`
safety net that escalates to a human and notifies admins when the LLM is unsure
or a call fails. Both features are code-complete; making them work live needs
the steps below. Fill env from the repo-root `.env.example` (single source of
truth) into `.env` (dev) or `/etc/skill-chatbot.env` (prod).

### 1. RAG / FAQ answering (`faq` tool)

`src/rag.py` is a thin shim over the **`rag_qdrant`** engine
([skill-rag-qdrant](https://github.com/A-I-M-S/skill-rag-qdrant), local
FastEmbed embeddings — no embedding endpoint needed). It is not pip-installable,
so install it into the orchestrator venv:

```bash
make orch-install-rag          # clones skill-rag-qdrant + installs deps + makes it importable
```

Required env: `QDRANT_URL`, `QDRANT_API_KEY`, `INFERENCE_BASE_URL`,
`INFERENCE_API_KEY`, `INFERENCE_MODEL`, and **`ADMIN_TELEGRAM_IDS`** (the engine
fail-closes at import if this is empty). Then create the collection and ingest
the corpus (until you do, every FAQ answer is `"No relevant information found"`):

```bash
. .venv/bin/activate && python -m rag_qdrant init
make ingest-file FILE=orchestrator/data/faq.md
make ingest-rules                                  # booking_rules.yaml → Qdrant
```

### 2. Farm-tour booking (`book_new` / `book_edit` / `book_cancel`)

The booking CLI (`booking_cli/booking_flow.py` + `composio_outlook.py`) is
**vendored in this repo** and driven by `src/booking_subprocess.py` (runs it
with the venv interpreter; no external skill required). It writes to an Outlook
calendar via Composio. Required env: `COMPOSIO_API_KEY` (plus
`COMPOSIO_CONNECTED_ACCOUNT_ID` / `COMPOSIO_ENTITY_ID` if more than one Outlook
account is linked). Booking rules come from `BOOKING_RULES_PATH` — point it at
`orchestrator/data/booking_rules.yaml` and **replace the placeholders**
(`TODO_REAL_UEN`, capacity/pricing/hours) before quoting real customers.

Dry-run without touching Composio (fails an early rule check):

```bash
BOOKING_RULES_PATH=$PWD/data/booking_rules.yaml \
  python -c "from src import booking_subprocess as b; print(b.new_draft(date='2026-08-15', time='03:00', pax=10))"
# → {'error': 'outside_hours', ...}  (proves the CLI + rules + arg contract work)
```

### 3. Replies reach the customer

The orchestrator POSTs replies to the bridge `POST /send` as `{to, text}` where
`to` is the sender's number (see `post_reply` in `src/main.py`; matches
`wa-bridge` `SendRequestSchema`).

### Go-live checklist

1. Fill `/etc/skill-chatbot.env` from the root `.env.example` (all REQUIRED vars).
2. `make orch-install-rag`; `python -m rag_qdrant init`; ingest `faq.md` + rules.
3. Set Composio creds; fill real values in `booking_rules.yaml`.
4. Start the orchestrator; send a real WhatsApp FAQ (expect a grounded answer)
   and a booking (expect a confirmation + an Outlook event).

## Admin sub-app (issue #30)

The orchestrator HTTP server (`ORCHESTRATOR_PORT`, default 7789) now
serves an admin surface at `/admin/*` alongside the existing `/health`
route. All admin endpoints share a single auth gate — every request
must carry both headers:

| Header                  | Required value                                  |
|-------------------------|-------------------------------------------------|
| `X-Admin-Token`         | equal to env `ADMIN_HTTP_TOKEN`                 |
| `X-Admin-Telegram-Id`   | one of `ADMIN_TELEGRAM_IDS` (comma-separated)   |

A missing or wrong token returns 401; a missing or non-admin
telegram id returns 403. The same env is checked locally by the
`admin-bot/` TG skill (issue #31), so the two halves of the
admin surface agree on who is an admin without sharing session state.

Leaving `ADMIN_HTTP_TOKEN` empty disables the sub-app entirely (every
`/admin/*` returns 401 `admin_disabled`).

### Routes

| Method  | Path                | Body / Query                                                                                                                                       | Notes |
|---------|---------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|-------|
| POST    | `/admin/ingest`     | `{source_type: "path"\|"url"\|"pdf"\|"md", target: str, telegram_id_acl: int \| "public"}`                                                          | Qdrant ingest with per-chunk ACL. Default ACL = `ADMIN_TELEGRAM_IDS` (admin-only). v0 implements `source_type=path` (auto-routes `.md`/`.pdf` by extension). |
| POST    | `/admin/grant`      | `{source: str, telegram_id: int}` (or `username` — not yet implemented)                                                                            | Forwards to `rag_qdrant.grant_access`. |
| POST    | `/admin/revoke`     | `{source: str, telegram_id: int}`                                                                                                                  | Forwards to `rag_qdrant.revoke_access`. |
| GET     | `/admin/show`       | `?source=<name>` optional                                                                                                                          | Lists ACL table; without filter, returns one row per distinct source. |
| GET     | `/admin/bookings`   | `?date=YYYY-MM-DD&tz=<IANA>` required                                                                                                              | Lists Outlook events for that day via Composio (or `booking_subprocess` fallback if `composio_outlook` isn't installed). |
| PATCH   | `/admin/config`     | `{key: str, value: any}`                                                                                                                           | Atomic ruamel round-trip patch to `BOOKING_RULES_PATH`. Allowed keys: `slot_duration_minutes`, `max_capacity_per_slot`, `operating_hours_per_day`, `blackout_dates`. Anything else (e.g. `timezone`, `pricing_tiers`, `deposit_instructions`, `location_default`, `outlook_calendar_id`) returns 400 `disk_only`. |

### Out of scope (not in issue #30)

TLS termination, rate limiting, audit log, the `admin-bot/` TG skill
(issue #31).

### Examples

```bash
# Health check (no auth) — works exactly as before
curl -s http://127.0.0.1:7789/health

# Ingest a markdown file with admin-only ACL
curl -sX POST http://127.0.0.1:7789/admin/ingest \
  -H "X-Admin-Token: $ADMIN_HTTP_TOKEN" \
  -H "X-Admin-Telegram-Id: $MY_TID" \
  -H 'Content-Type: application/json' \
  -d '{"source_type":"path","target":"/path/to/rules.md"}'

# Show ACL table for one source
curl -s "http://127.0.0.1:7789/admin/show?source=rules" \
  -H "X-Admin-Token: $ADMIN_HTTP_TOKEN" \
  -H "X-Admin-Telegram-Id: $MY_TID"

# Patch slot_duration_minutes
curl -sX PATCH http://127.0.0.1:7789/admin/config \
  -H "X-Admin-Token: $ADMIN_HTTP_TOKEN" \
  -H "X-Admin-Telegram-Id: $MY_TID" \
  -H 'Content-Type: application/json' \
  -d '{"key":"slot_duration_minutes","value":90}'

# Bookings for a given day
curl -s "http://127.0.0.1:7789/admin/bookings?date=2026-06-25" \
  -H "X-Admin-Token: $ADMIN_HTTP_TOKEN" \
  -H "X-Admin-Telegram-Id: $MY_TID"
```
