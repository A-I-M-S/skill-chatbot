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
