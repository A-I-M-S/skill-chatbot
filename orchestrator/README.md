# orchestrator

Python 3.11 orchestrator for skill-chatbot.

- NDJSON tailer (via `pygtail`) over `wa-bridge/inbox.ndjson`.
- SQLite WAL state with `PhoneState` + per-message `state_log`.
- LLM router with tool-calling (`openai` SDK; `pydantic` schema validation).
- Flows: FAQ, booking new / edit / cancel, handoff, admin notify.
- i18n: EN + 中文, language auto-detected from the inbound message.

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
│   ├── prompts/         # router_en.py / router_zh.py
│   └── flows/           # faq, booking_new, booking_edit, booking_cancel, handoff, confirm
├── scripts/             # smoke.py, ingest_rules.py, ingest_file.py, reindex.py
└── tests/
```

## Dev loop

From the repo root: `make orch-venv` then `make orch-install` then `make orch-dev`. From this dir: `make venv` then `make install` then `make dev`.

## Test

`make orch-test` (or `make test-cov` for coverage).
