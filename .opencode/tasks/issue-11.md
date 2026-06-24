# Build task — Issue #11 (Phase 7: Ingest helper)

> You are the opencode **Build** agent. The project planning doc is `docs/plans/phase-0-bootstrap.md`. Issues #1, #2, #3, #15 are landed; commits `023ed3e`, `b7cfeab`, `8fd3405`, `5474ac1`. The GitHub issue is **#12** (the title prefix is #11, GitHub number is offset).

## ⚠️ NEW: PR-per-issue workflow

**You must work on a feature branch and open a PR.** Direct commits to `main` are no longer allowed.

1. From `main` (already up to date), create a branch: `git switch -c feat/11-ingest-helper`
2. Do all your work on that branch. Do **not** commit to `main`.
3. When done, commit: `git commit -m "feat(ingest): booking_rules.yaml + faq.md one-shot helpers (#11)"`
4. Push the branch: `git push -u origin feat/11-ingest-helper`
5. Open a PR with: `gh pr create --base main --head feat/11-ingest-helper --title "feat(ingest): booking_rules.yaml + faq.md one-shot helpers (#11)" --body "Closes #12 — see .opencode/tasks/issue-11.md for the brief and the deliverable summary below." --label "phase:7-data" --label "parallel-safe"`
6. **Do not merge** the PR. Leave it open for review.
7. Print the PR URL to stdout.

## What to build

One-shot scripts that push structured content into the existing Qdrant collection so the FAQ tool has something to answer with.

## Acceptance criteria (verbatim from issue #12)

- [ ] `orchestrator/scripts/ingest_rules.py`: reads `BOOKING_RULES_PATH`, formats it as markdown sections (location, hours, slot duration, capacity, pricing, blackout, deposit), calls `from rag_qdrant import ingest_text; ingest_text(markdown, source='booking_rules_v1')`
- [ ] `orchestrator/scripts/ingest_file.py <path>`: supports `.md`, `.txt`, `.pdf`; `source` defaults to filename without ext
- [ ] Idempotent: re-running on the same `source` updates in place (the existing rag-qdrant skill handles point-id hashing)
- [ ] README: `Data sources` section updated
- [ ] pytest tests: yaml→markdown format with a fixture, file-type dispatch, source default
- [ ] Run once against the real collection; record the resulting `source` ids in the README

## Inputs you must respect

- The plan doc's §1 layout (`orchestrator/scripts/ingest_rules.py`, `ingest_file.py`, `orchestrator/data/`), §2 tech choices, §3 Makefile targets (`ingest-rules`, `ingest-file`).
- `references/upstream/booking_rules.yaml` for the actual structure to parse.
- `references/upstream/rag-qdrant-SKILL.md` and `references/upstream/rag-qdrant-README.md` for the `ingest_text()` API: `from rag_qdrant import ingest_text; ingest_text(text, source=source)`. Returns nothing useful; errors propagate.
- `.env.example` for env var names: `QDRANT_URL`, `QDRANT_API_KEY`, `BOOKING_RULES_PATH`.

## Hard constraints

- **No code outside `orchestrator/scripts/`, `orchestrator/tests/`, `orchestrator/data/`, `orchestrator/README.md`, root `README.md`** (the README's `Data sources` section is the only root file you may modify).
- **Do not commit secrets, real creds, or `auth_info/`.**
- **Do not merge the PR.**
- **Do not modify any other issue's files** (wa-bridge, the orchestrator's `src/`, the existing scripts, the system units — all of those belong to other issues).
- **Do not run real `ingest_text` calls** during the test suite. Mock `rag_qdrant.ingest_text` in tests.
- **Commit on the feature branch only.** Never `git commit` on `main`.
- **Push the branch and open the PR** as described at the top of this brief.

## When you're done

Print to stdout:
1. The PR URL.
2. The full `git status` of the repo (on the feature branch).
3. The list of new/modified files (relative paths).
4. The `git log --oneline -3` showing the branch's commits.
5. The test command output: `cd orchestrator && . .venv/bin/activate && pytest -q tests/test_ingest.py` (use `-v` so I can see each test).
6. A one-paragraph summary: what each new file does, any deviation from the plan, the SHA.
