# Build task — Issue #10 (Phase 6: Image handling)

> You are the opencode **Build** agent. The project planning doc is `docs/plans/phase-0-bootstrap.md`. Issues #1, #2, #3, #15 are landed. The GitHub issue is **#11** (the title prefix is #10, GitHub number is offset).

## ⚠️ PR-per-issue workflow

1. From `main`, create a branch: `git switch -c feat/10-image-handling`
2. Do all your work on that branch. Do **not** commit to `main`.
3. When done, commit: `git commit -m "feat(media): inbound image receive + ack + caption routing (#10)"`
4. Push the branch: `git push -u origin feat/10-image-handling`
5. Open a PR: `gh pr create --base main --head feat/10-image-handling --title "feat(media): inbound image receive + ack + caption routing (#10)" --body "Closes #11 — see .opencode/tasks/issue-10.md for the brief." --label "phase:6-media" --label "parallel-safe"`
6. **Do not merge** the PR.
7. Print the PR URL to stdout.

## What to build

Receive images from customers, ack them, store them, and let the LLM answer questions about them (best-effort, since we have no multimodal model by default).

## Acceptance criteria (verbatim from issue #11)

- [ ] wa-bridge: download image to `RAG_PHOTOS_DIR/inbound/<sha256[:16]>.<ext>` and write `{message_id, from, image: {path, sha256, filename}}` to the NDJSON (text may be empty or a caption)
- [ ] orchestrator: on inbound with an image, save the metadata to state (`state.last_image`) and reply with an ack (`Got the photo.` / `收到图片了。`)
- [ ] If the caption is a question, prepend `I have a photo at <path> from this chat.` to the router's user-message; router may call `faq` to search the rag-photos corpus
- [ ] Rate limit: ignore inbound images >10 MB
- [ ] vitest + pytest tests: image-only message, image+caption, oversize rejected
- [ ] Update SKILL.md: image handling notes

## Inputs you must respect

- Plan §1 layout (`wa-bridge/src/image.ts`, `wa-bridge/src/inbox.ts` extension, `orchestrator/src/state.py` extension, `orchestrator/src/rag.py` extension), §2 tech choices, §3 Makefile targets.
- The NDJSON contract from issues #2 + #3 (already documented at the top of `orchestrator/src/main.py`).
- `references/upstream/rag-qdrant-SKILL.md` (photo support section).
- `.env.example`: `RAG_PHOTOS_DIR` (default `/root/rag-photos`).

## Hard constraints

- **Do not modify wa-bridge auth / sender / socket / http / log files** — they belong to other issues.
- **Do not modify the orchestrator's main.py / tail.py / state.py / http_server.py core** — only add image-specific helpers. If you need to change `state.py`'s schema, surface it in the deliverable summary.
- **Do not commit secrets or real photos.**
- **Do not merge the PR.**
- **Do not run real Qdrant or real Baileys during tests.** Mock both.
- **Work on the feature branch only.** Never `git commit` on `main`.

## When you're done

Print to stdout:
1. The PR URL.
2. The full `git status` of the repo (on the feature branch).
3. The list of new/modified files (relative paths).
4. The `git log --oneline -3` showing the branch's commits.
5. The test command outputs: `cd wa-bridge && npx vitest run --reporter=basic` and `cd orchestrator && . .venv/bin/activate && pytest -q tests/test_image.py`.
6. A one-paragraph summary.
