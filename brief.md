# Brief for opencode (Plan agent)

> Read this file end-to-end before doing anything. After reading, your job is to **produce a step-by-step plan only** — do not write code, do not create files outside `docs/plans/`, do not commit.

## Project

**Repo:** https://github.com/A-I-M-S/skill-chatbot
**Local path:** `/root/.openclaw/workspace/dev/projects/skill-chatbot`
**Issue tracker:** https://github.com/A-I-M-S/skill-chatbot/issues
**Driver model:** `minimax/MiniMax-M3` (this session)
**Mode:** Plan (no code yet — switch to Build only after the user approves this plan)

## What we're building

A WhatsApp chatbot for SAAC FARM tour. Two main functions, both driven by free-form customer messages (no menus):

1. **FAQ** — answer customer questions from a Qdrant-backed RAG corpus (existing `rag-qdrant` skill).
2. **Booking** — create / edit / cancel farm tour bookings on the Outlook calendar (existing `farm-tour-booking` skill, calendar `outlook_24E83AD2E7F5F77D@outlook.com`).

Out-of-scope intents (refund dispute, complaint, custom pricing, abuse) → **handoff**: notify admins in WhatsApp, tell customer to call a configured number.

Multilingual: **EN + 中文**, auto-detected from the inbound message. LLM replies in the same language.

## Stack & key decisions (locked)

- **WhatsApp transport:** Baileys (Node.js), single long-lived process, QR auth, persistent creds on disk. Replies only — no broadcast, no Meta template approval needed.
- **Hosting:** same box as `rag-qdrant` and `farm-tour-booking`.
- **Topology:** two daemons + a tail-able NDJSON inbox:
  - `wa-bridge` (Node + TS): owns the Baileys socket, exposes `POST /send`, `GET /health`, `GET /status`, `POST /inbound` events written to `inbox.ndjson`.
  - `orchestrator` (Python 3.11): tails the NDJSON, runs the LLM router, owns per-phone conversation state in SQLite, calls `rag_qdrant.ask()` and `booking_flow.py` as subprocesses.
- **RAG:** query-only on the WhatsApp side. Reuse the existing Qdrant collection (do not create a new one). Ingest is admin-only via the existing rag-qdrant CLI plus a one-shot `ingest_rules.py` helper.
- **Booking flow:** multi-turn — collect required fields across messages, present a summary, require explicit `YES` (or `是` in 中文) to commit. Reuse the existing `booking_flow.py` `--confirm` semantics. **Phone filtering is in the orchestrator** (not in `farm-tour-booking`) — pull events with `list --from today --to +90d` and filter in-process by caller phone.
- **Handoff:** `WA_NOTIFY` env (comma-separated phone numbers) is DM'd on out-of-scope and on new bookings. `ADMIN_CONTACT_NUMBER` env is the phone number the bot tells customers to call. Both are dynamic.
- **Destructive ops (edit / cancel):** always require explicit `YES`. Any other reply = abort.
- **DM only:** ignore group messages.
- **Media:** text + images in v1. Images stored to `RAG_PHOTOS_DIR/inbound/`; captions routed to the LLM.
- **Open code skill-patches:** do **not** patch the upstream `rag-qdrant` or `farm-tour-booking` skills — they are the source of truth. The orchestrator adapts.

## Reuses (do not reinvent)

- `from rag_qdrant import ask, ingest_text, ingest_file, ensure_collection, stats` — at `/root/.openclaw/skills/rag-qdrant/`
- `python3 /root/.openclaw/workspace/admin/skills/farm-tour-booking/scripts/booking_flow.py …` — with `COMPOSIO_API_KEY` and (optionally) `COMPOSIO_CONNECTED_ACCOUNT_ID` in the subprocess env
- `config/booking_rules.yaml` in `farm-tour-booking` is the source of truth for hours, capacity, pricing, blackout dates
- Same `INFERENCE_BASE_URL` / `INFERENCE_API_KEY` / `INFERENCE_MODEL` as `rag-qdrant`
- Same `QDRANT_URL` / `QDRANT_API_KEY` as `rag-qdrant`

## Existing skills — quick reference (snapshots in this repo)

The upstream skill files are **snapshotted** into `references/upstream/` so the Plan agent can read them as in-project files. **Read from `references/upstream/`, not from the live skill paths.**

- `references/upstream/rag-qdrant-SKILL.md` — agent adapter, ACL, caching, photo support
- `references/upstream/rag-qdrant-README.md` — full API surface
- `references/upstream/rag-qdrant-requirements.txt` — pin compatible versions
- `references/upstream/farm-tour-booking-SKILL.md` — intent routing, edge cases, escalation
- `references/upstream/booking_flow.py` — see `op_new`, `op_list`, `op_edit`, `op_cancel` for the exact CLI surface
- `references/upstream/intent.py`, `composio_outlook.py` — supporting scripts
- `references/upstream/booking_rules.yaml` — source of truth for hours, capacity, pricing, blackout

If the snapshots ever go stale, refresh with `bash scripts/snapshot-upstream.sh`.

## The 15 issues (use **title prefix** as canonical order, not GitHub issue number)

| # | Title | Phase | Depends on | Parallel with |
|---|---|---|---|---|
| 1 | Project layout + build tooling | phase:0-bootstrap | — | (blocks all) |
| 2 | wa-bridge: Baileys auth + receive + send + /health | phase:1-bridge | #1 | #3, #10, #11, #12, #13 |
| 3 | orchestrator v0: NDJSON tail + RAG echo + reply | phase:2-orchestrator | #1 | #2, #10, #11, #12, #13 |
| 4 | LLM router: tool-calling, intent + entity extraction | phase:3-router | #2, #3 | (unblocks 5-9) |
| 5 | book_new flow | phase:4-flows | #4 | #6, #7, #8, #9 |
| 6 | book_edit / book_cancel flow | phase:4-flows | #4 | #5, #7, #8, #9 |
| 7 | handoff flow | phase:4-flows | #4 | #5, #6, #8, #9 |
| 8 | admin notify on new booking | phase:4-flows | #5 | #6, #7, #9 |
| 9 | Multilingual EN / 中文 | phase:5-i18n | #4 | #5, #6, #7, #8 |
| 10 | Image handling | phase:6-media | #2, #3 | #4–#9, #11, #12, #13 |
| 11 | Ingest helper (booking_rules.yaml + faq.md) | phase:7-data | none | everything |
| 12 | Idempotency, dedupe, reconnection, SQLite WAL | phase:8-hardening | #2, #3 | #4–#11, #13 |
| 13 | systemd units + log rotation | phase:8-hardening | #2, #3 | #4–#12 |
| 14 | smoke.py (NDJSON replay + live E2E) | phase:9-smoke | #5, #6, #7, #8 | — |
| 15 | Sample dialogues (EN + 中文) + runbook | phase:9-smoke | #1 | everything |

## Your deliverable for this Plan pass

Produce a single planning document at `docs/plans/phase-0-bootstrap.md` (and only that file — do not create other files). It must contain:

1. **Project layout** — the exact directory tree under `wa-bridge/` and `orchestrator/` (down to the file level), with one-line purpose for each file. Cross-check against the repo's existing `README.md` and `docs/architecture.md`.
2. **Tech choices** — locked-in library versions and one-sentence justifications (e.g. why `tsx` over `ts-node`, why `hatchling` for Python, why `vitest` for Node and `pytest` for Python, why `pino` for Node logging, etc.).
3. **Makefile target catalogue** — every target at the root, in `wa-bridge/`, and in `orchestrator/`, with the exact command each one runs.
4. **Sequencing plan for all 15 issues** — issue order respecting the dependency graph above, grouped into "parallel batches" (issues opencode should attack in the same session) and "gates" (places where you must surface a plan to the user before continuing).
5. **Risk callouts** — anything that could blow up at runtime (Baileys multi-device policy, Composio entity_id quirks, SQLite WAL + concurrent writers, the LLM tool-calling schema not being honored by the configured model, etc.).
6. **Open questions for the user** — anything you genuinely cannot decide from this brief.

Hard constraints:
- Do not write code, do not commit, do not push.
- Do not open PRs, do not file new issues, do not edit issue bodies.
- Do not create files outside `docs/plans/phase-0-bootstrap.md`.
- If you need to read code from outside the repo, read it; do not modify it.
- When you're done, print a one-paragraph summary to stdout and exit.
