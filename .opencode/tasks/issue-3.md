# Build task — Issue #3 (Phase 2: orchestrator v0: NDJSON tail + RAG echo + reply)

> You are the opencode **Build** agent. The project planning doc is `docs/plans/phase-0-bootstrap.md` — read it end-to-end. Issue #1 (scaffolding) is done; commit `023ed3e`. The GitHub issue is **#4** (the title prefix is #3, GitHub number is offset).

## What to build

Bare orchestrator: tails the inbox NDJSON, calls `rag_qdrant.ask()`, posts the reply to the bridge. No LLM routing, no state, no booking. **This is the "is the loop closed" milestone.**

## Acceptance criteria (verbatim from issue #4)

- [ ] `orchestrator/src/main.py` boots, opens `ORCHESTRATOR_DB` (SQLite, WAL mode), tails `INBOX_PATH`
- [ ] For each new NDJSON line: load the matching row's text, call `from rag_qdrant import ask; print(ask(text)['answer'])`
- [ ] POST reply to `WA_BRIDGE_URL/send` with Bearer token
- [ ] HTTP server on `ORCHESTRATOR_PORT`:
  - `GET /health` → 200 `{ok:true, db:'ok', last_processed_message_id}`
- [ ] Crash-safe: track last processed message_id; on restart, skip up to that offset in the NDJSON
- [ ] pytest tests: tailer advance on new line, tailer skip on duplicate, /health shape
- [ ] End-to-end smoke: write a fake line to the inbox, observe it flow to `wa-bridge/send` (mock)

## Inputs you must respect

- The plan doc's §1 layout (file paths) and §2 tech choices (`pygtail@^0.14`, `httpx@^0.27`, `python-json-logger`, `pydantic@^2.8`, stdlib `sqlite3`).
- `references/upstream/rag-qdrant-SKILL.md` and `references/upstream/rag-qdrant-README.md` for the `ask()` API. Use it exactly as documented — `from rag_qdrant import ask; result = ask(question); reply = result['answer']`.
- The plan's **Risk #6, #10, #13** callouts (SQLite WAL + flock, NDJSON race, phone JID canonicalisation).
- Issue #2 will produce the matching `wa-bridge` daemon. The NDJSON line format from issue #2 is exactly:
  `{"message_id": "...", "from": "<digits-only-phone>", "text": "...", "image": null|{...}, "timestamp": "..."}`
  Read this from `references/upstream/wa-bridge-ndjson-contract.md` if it exists, otherwise assume the shape above and add a brief comment in `main.py` documenting the contract version (`v1`).

## Hard constraints

- **No LLM router, no flows, no booking logic.** This is purely the tail + echo loop.
- **No real Qdrant, no real Baileys during tests.** Mock the `rag_qdrant.ask` import and mock the `httpx` call to `wa-bridge/send` with `respx` (a pytest-httpx mock library; add to dev deps if not present).
- **No real env values.** Use `monkeypatch` (pytest fixture) or a fixture-injected `Settings` for tests.
- **Do not push to the remote** — I'll push.
- **Do not edit issue body, do not open new issues, do not open PRs.**
- **Commit** when done. Message: `feat(orchestrator): NDJSON tail + RAG echo + reply loop (#3)`.
- Use the existing `pyproject.toml` (`httpx`, `pydantic`, `pygtail`, `python-json-logger`, plus dev `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`). Add `respx` to dev deps if it's not there.
- All env access goes through a `Settings` class (pydantic BaseSettings or pydantic-settings if already a dep; otherwise hand-rolled). No `os.environ['X']` scattered around.
- NDJSON line parsing: skip lines that don't parse (log warn and continue). The tailer must be robust to a partial last line (use `pygtail`).
- SQLite: `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;` at boot. Plus an `fcntl.flock` (LOCK_EX | LOCK_NB) on the DB file to fail-fast on second boot — per plan risk #6.
- The HTTP server runs on `ORCHESTRATOR_PORT` (default 7789 from `.env.example`).
- Log to stdout (JSON via `python-json-logger`) AND to `ORCHESTRATOR_LOG` if set.
- `last_processed_message_id` is persisted in SQLite (`processed_messages` table) — that's the dedupe boundary, even though the brief's full idempotency is in issue #12. We just need it to survive a restart for v0.

## When you're done

Print to stdout:
1. The full `git status` of the repo.
2. The list of new files (relative paths).
3. The `git log --oneline -3`.
4. The test command output: `cd orchestrator && . .venv/bin/activate && pytest -q tests/` (use `-v` so I can see each test).
5. A one-paragraph summary: what each new file does, any deviation from the plan, the SHA.
