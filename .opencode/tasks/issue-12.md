# Build task — Issue #12 (Phase 8a: Idempotency, dedupe, reconnection, SQLite WAL)

> You are the opencode **Build** agent. The project planning doc is `docs/plans/phase-0-bootstrap.md`. Issues #1, #2, #3, #15 are landed. The GitHub issue is **#13** (the title prefix is #12, GitHub number is offset).

## ⚠️ PR-per-issue workflow

1. From `main`, create a branch: `git switch -c feat/12-hardening`
2. Do all your work on that branch.
3. Commit: `git commit -m "chore(hardening): idempotency, dedupe, reconnection, WAL, log rotation (#12)"`
4. Push: `git push -u origin feat/12-hardening`
5. Open PR: `gh pr create --base main --head feat/12-hardening --title "chore(hardening): idempotency, dedupe, reconnection, WAL, log rotation (#12)" --body "Closes #13 — see .opencode/tasks/issue-12.md for the brief." --label "phase:8-hardening" --label "parallel-safe"`
6. **Do not merge.** Print PR URL.

## What to build

Make the running system safe to leave alone for a week.

## Acceptance criteria (verbatim from issue #13)

- [ ] orchestrator: every inbound `message_id` recorded in `processed_messages(message_id PK, processed_at REAL)` (already in #3 — verify); duplicates dropped before the LLM call
- [ ] orchestrator: `state_log` insert-only table capturing every state transition (phone, old_flow, new_flow, old_draft, new_draft, at) — **new in this issue**
- [ ] wa-bridge: outbound queue is a JSONL file; `POST /send` appends, a background worker drains and retries with backoff; `GET /status` shows queue depth — **enhance the existing sender from #2**
- [ ] wa-bridge: on Baileys `close`, exponential backoff reconnect (1s → 60s, jitter); on 4 successive QR-needs, log CRITICAL and stop auto-reconnect — **enhance the existing socket from #2**
- [ ] orchestrator: SQLite in WAL mode, `synchronous=NORMAL` — verify from #3
- [ ] Crash safety: orchestrator restart resumes from last message_id (already in #3); wa-bridge restart flushes the outbound queue — **enhance the sender from #2**
- [ ] vitest + pytest tests: dedupe, state-log row written, reconnect backoff schedule, queue drain after process kill
- [ ] Update `docs/ops.md` with the recovery procedures

## Inputs you must respect

- Plan §1, §2, §3, and the **Risk #2, #6, #10, #11** callouts.
- The existing wa-bridge `src/socket.ts` and `src/sender.ts` from #2 — extend, don't replace.
- The existing `orchestrator/src/state.py` from #3 — extend, don't replace.

## ⚠️ File-access boundary (HARD)

You have **read access to the project and to `references/upstream/`.** You do **NOT** need to read or grep from the live skill paths (`/root/.openclaw/skills/rag-qdrant/`, `/root/.openclaw/workspace/admin/skills/farm-tour-booking/`). Those are auto-rejected and trying to read them will abort your work.

**If a question is answered by `references/upstream/rag-qdrant-SKILL.md` or `references/upstream/rag-qdrant-README.md`, use those.** Do not "verify" against the live skills.

## ⚠️ Out-of-scope (do not attempt)

- **Do not start a real Baileys session** — mock it.
- **Do not connect to a real Qdrant** — mock `rag_qdrant.ingest_text` / `ask` if you need to test integration points.
- **Do not run `systemctl`** — only write the unit files.

## Hard constraints

- **Do not modify any feature-flow code** (`flows/`, `router.py`, `prompts/`). This issue is plumbing only.
- **Do not commit secrets.**
- **Do not merge the PR.**
- **Do not run real Baileys or real Qdrant during tests.** Mock both.
- **Work on the feature branch only.**

## When you're done

Print to stdout:
1. The PR URL.
2. The full `git status` of the repo.
3. The list of new/modified files.
4. The `git log --oneline -3`.
5. The test outputs: `cd wa-bridge && npx vitest run --reporter=basic` and `cd orchestrator && . .venv/bin/activate && pytest -q tests/`.
6. A one-paragraph summary.
