# Build task — Issue #15 (Phase 9: Sample dialogues + runbook + security doc)

> You are the opencode **Build** agent. The project planning doc is `docs/plans/phase-0-bootstrap.md`. Issue #1 (scaffolding) is done; commit `023ed3e`. The GitHub issue is **#16** (the title prefix is #15, GitHub number is offset by one).

## What to build

Pure documentation. No code changes. Adds `docs/runbook.md` and `docs/security.md`, extends `docs/message-flows.md` with more dialogues, and adds `docs/architecture.md` cross-links.

## Acceptance criteria (verbatim from issue #16)

- [ ] `docs/message-flows.md` has at least 8 dialogues × 2 languages = 16 entries (faq, new-booking happy, new-booking full, new-booking unavailable, edit, cancel, handoff refund, handoff abuse, image question, mid-flow language switch)
- [ ] `docs/runbook.md`: 12 common incidents with copy-pasteable commands
  - Bridge down, orchestrator down
  - Session lost / QR re-auth
  - Composio 5xx for >2 min
  - LLM 429 storm
  - Qdrant unreachable
  - Suspicious abusive user
  - Customer asks for human mid-flow
  - State DB corruption
  - Log disk full
  - Phone number changed
  - Admin off-rotation (WA_NOTIFY empty)
  - Outbound queue stuck
- [ ] `docs/security.md`: what data is logged, retention, who can read state.sqlite, secret rotation

## Inputs you must respect

- The plan doc's §1 (file paths) and §6 (15 open questions, especially Q11 about run-as user, Q12 about corpus, Q13 about JID canonicalisation).
- The existing `docs/architecture.md` and `docs/ops.md` — read them; cross-link from the new docs.
- The existing `docs/message-flows.md` — keep what's there, extend it.

## Hard constraints

- **Pure docs.** Do not modify any code, manifest, or config file. The only files you may touch are:
  - `docs/runbook.md` (NEW)
  - `docs/security.md` (NEW)
  - `docs/message-flows.md` (extend; keep the existing 7 dialogues)
- **Do not push to the remote** — I'll push.
- **Do not edit issue body, do not open new issues, do not open PRs.**
- **Commit** when done. Message: `docs: runbook, security, and extended message-flows (#15)`.

## When you're done

Print to stdout:
1. The full `git status` of the repo.
2. The list of new/modified files (relative paths).
3. The `git log --oneline -3`.
4. A one-paragraph summary: line counts of each file, any deviation from the issue, the SHA.
