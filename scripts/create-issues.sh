#!/usr/bin/env bash
# Files the 15 implementation issues against A-I-M-S/skill-chatbot.
# Idempotent on the labels side (labels must already exist).
set -euo pipefail

REPO="A-I-M-S/skill-chatbot"

create_issue() {
  local title="$1"
  shift
  local body="$1"
  shift
  local label_args=()
  for lbl in "$@"; do
    label_args+=(--label "$lbl")
  done
  gh issue create \
    --repo "$REPO" \
    --title "$title" \
    --body "$body" \
    "${label_args[@]}" >/dev/null
  echo "  ✓ $title"
}

cat <<'BANNER'
Filing 15 issues …
BANNER

# ───────────────────────── #1 — Project layout + build tooling ─────────
create_issue "#1 Phase 0 — Project layout + build tooling" "## Scope
Scaffold the two sub-projects with build tooling and a Makefile that wraps the most common commands. No app code yet.

## Acceptance criteria
- [ ] \`wa-bridge/package.json\` with: typescript, tsx, vitest, pino, dotenv, @whiskeysockets/baileys, express, zod
- [ ] \`wa-bridge/tsconfig.json\` (strict, ES2022, NodeNext modules)
- [ ] \`wa-bridge/Makefile\` targets: \`install\`, \`dev\`, \`build\`, \`test\`, \`lint\`
- [ ] \`orchestrator/pyproject.toml\` (Python 3.11, hatchling) with deps: openai, fastembed, qdrant-client, python-dotenv, pypdf, httpx, pydantic
- [ ] \`orchestrator/Makefile\` targets: \`venv\`, \`install\`, \`dev\`, \`test\`, \`lint\`
- [ ] \`Makefile\` at repo root with: \`bridge-dev\`, \`bridge-build\`, \`orch-dev\`, \`orch-test\`, \`smoke\`, \`logs\`
- [ ] \`.editorconfig\`, root \`.gitattributes\`
- [ ] README updated with the new dev loop (replace the 'Quick start' section)

## Blocks
All later issues depend on this one." "phase:0-bootstrap"

# ───────────────────────── #2 — wa-bridge v0 ─────────────────────────────
create_issue "#2 Phase 1 — wa-bridge: Baileys auth + receive + send + /health" "## Scope
A long-lived Node process that owns the Baileys socket, persists auth, and exposes a tiny HTTP surface for the orchestrator.

## Acceptance criteria
- [ ] \`wa-bridge/src/index.ts\` boots Baileys with \`useMultiFileAuthState(WA_AUTH_DIR)\`
- [ ] QR auth CLI: \`npm run auth\` prints QR to stdout, waits for sync, exits 0
- [ ] On every inbound non-group, non-status message, append one JSON line to \`WA_AUTH_DIR/../inbox.ndjson\`: \`{message_id, from, text, image?, timestamp}\`
- [ ] HTTP server on \`WA_BRIDGE_PORT\`:
  - \`GET /health\` → 200 \`{ok:true, session:'ok'|'qr_needed'|'connecting'}\`
  - \`GET /status\` → 200 \`{session, last_message_at, queued_send}\`
  - \`POST /send {to, text}\` (Bearer \`WA_BRIDGE_TOKEN\`) → 200 \`{message_id}\`; on fail 502 \`{error:'send_failed', reason}\`
- [ ] Auto-reconnect with exponential backoff (max 60s); logs to \`WA_BRIDGE_LOG\`
- [ ] Graceful SIGTERM: flush outbound queue, then close
- [ ] vitest tests: \`send\` happy path, \`send\` auth-missing, inbox line format

## Depends on
#1

## Parallel with
#3, #10, #11, #12, #13" "phase:1-bridge" "parallel-safe"

# ───────────────────────── #3 — orchestrator v0 ──────────────────────────
create_issue "#3 Phase 2 — orchestrator v0: NDJSON tail + RAG echo + reply" "## Scope
Bare orchestrator: tails the inbox, calls \`rag_qdrant.ask()\`, posts the reply to the bridge. No LLM routing yet, no state, no booking. The 'is the loop closed' milestone.

## Acceptance criteria
- [ ] \`orchestrator/src/main.py\` boots, opens \`ORCHESTRATOR_DB\` (SQLite, WAL mode), tails \`INBOX_PATH\`
- [ ] For each new NDJSON line: load the matching row's text, call \`from rag_qdrant import ask; print(ask(text)['answer'])\`
- [ ] POST reply to \`WA_BRIDGE_URL/send\` with Bearer token
- [ ] HTTP server on \`ORCHESTRATOR_PORT\`:
  - \`GET /health\` → 200 \`{ok:true, db:'ok', last_processed_message_id}\`
- [ ] Crash-safe: track last processed message_id; on restart, skip up to that offset in the NDJSON
- [ ] pytest tests: tailer advance on new line, tailer skip on duplicate, /health shape
- [ ] End-to-end smoke: write a fake line to the inbox, observe it flow to \`wa-bridge/send\` (mock)

## Depends on
#1

## Parallel with
#2, #10, #11, #12, #13" "phase:2-orchestrator" "parallel-safe"

# ───────────────────────── #4 — LLM router ───────────────────────────────
create_issue "#4 Phase 3 — LLM router: tool-calling, intent + entity extraction" "## Scope
The brain. Single LLM call per inbound message. Five tools: \`faq\`, \`book_new\`, \`book_edit\`, \`book_cancel\`, \`handoff\`. The orchestrator hands the result to the matching flow module.

## Acceptance criteria
- [ ] \`orchestrator/src/router.py\` exposes \`route(message: AgentMessage, state: PhoneState) -> RouterDecision\`
- [ ] System prompt (EN + 中文 variants in \`i18n.py\`) defines the five tools with OpenAI-format JSON schemas
- [ ] Tool schemas:
  - \`faq(question: str)\`
  - \`book_new(date?: str, time?: str, pax?: int, contact_name?: str, contact_email?: str, contact_phone?: str, org?: str, notes?: str)\`
  - \`book_edit(event_id?: str, date?: str, time?: str, pax?: int)\`
  - \`book_cancel(event_id?: str)\`
  - \`handoff(reason: 'refund'|'complaint'|'custom_pricing'|'abuse'|'other', summary: str)\`
- [ ] LLM call uses \`INFERENCE_BASE_URL\` + \`INFERENCE_API_KEY\` + \`INFERENCE_MODEL\`
- [ ] On tool call, returns a typed \`RouterDecision\`; on no tool call, returns the LLM's text reply (small talk, clarification)
- [ ] On tool error (4xx/5xx), retries with backoff 1s/2s/4s; final fail → \`handoff(reason='other', summary=error)\`
- [ ] pytest tests with a stubbed OpenAI client: all 5 tools, no-tool small-talk, error retry, error final-fallback
- [ ] Logs: detected language, tool chosen, latency, prompt+completion tokens

## Depends on
#2, #3

## Unblocks
#5, #6, #7, #8, #9" "phase:3-router"

# ───────────────────────── #5 — book_new ─────────────────────────────────
create_issue "#5 Phase 4a — book_new flow" "## Scope
Multi-turn new-booking flow. Collects required fields, shows a summary, requires explicit \`YES\` to commit.

## Required fields
\`date\`, \`time\`, \`pax\`, plus \`contact_name\` + (\`contact_email\` OR \`contact_phone\`).

## Acceptance criteria
- [ ] \`orchestrator/src/flows/booking_new.py\` exports \`handle(decision, state, msg) -> Reply\`
- [ ] State machine: \`collecting\` → \`awaiting_confirm\` → \`committed\` | \`aborted\`
- [ ] On \`router\` returning \`book_new\`, merge any provided fields into state.draft, ask for the next missing required field (one at a time, conversational)
- [ ] When all required fields are present, switch to \`awaiting_confirm\` and emit the summary prompt (verbatim, in user's language)
- [ ] User reply \`YES\` (case-insensitive trim, must equal 'yes' or '是') → invoke \`booking_flow.py new … --confirm\`; on success emit the \`reply\` field from the JSON; on \`error\` route to \`handoff\`
- [ ] User reply anything else → \`aborted\`, draft preserved for 10 minutes (so they can say 'actually, continue')
- [ ] Booking horizon: \`date\` must be within \`BOOKING_HORIZON_DAYS\`; else reject with friendly message
- [ ] All subprocesses run with \`COMPOSIO_API_KEY\` in env (and \`COMPOSIO_CONNECTED_ACCOUNT_ID\` if set)
- [ ] pytest tests: all-required-collected happy path, missing-field mid-flow, YES=commit, anything-else=abort, horizon-reject, subprocess-fail → handoff

## Depends on
#4

## Parallel with
#6, #7, #8, #9" "phase:4-flows" "parallel-safe"

# ───────────────────────── #6 — book_edit / book_cancel ──────────────────
create_issue "#6 Phase 4b — book_edit / book_cancel flow" "## Scope
Look up the caller's events, let them pick one, apply the change, require explicit \`YES\`.

## Acceptance criteria
- [ ] \`orchestrator/src/flows/booking_edit.py\` + \`booking_cancel.py\` (or one file with two handlers)
- [ ] On \`book_edit\` / \`book_cancel\`:
  1. Run \`booking_flow.py list --from <today> --to <today+BOOKING_HORIZON_DAYS>\` (subprocess)
  2. Filter events in-process by caller phone: \`body contains state.phone\` OR \`attendees[*].phone == state.phone\`
  3. 0 matches → reply 'No upcoming bookings on this number.' + handoff
  4. 1 match → present it, ask for confirmation
  5. >1 match → numbered list, ask user to pick (one-shot question)
- [ ] Confirmation prompt: \`YES\` (or \`是\`) commits; any other reply aborts
- [ ] \`book_edit\`: only \`date\`, \`time\`, \`pax\` are editable. \`contact_*\` changes escalate to handoff.
- [ ] subprocess errors → handoff
- [ ] pytest tests: filter-by-phone, 0/1/N matches, YES=commit, anything-else=abort, contact-change → handoff

## Depends on
#4

## Parallel with
#5, #7, #8, #9

## Note
Per design decision, the phone filter is **in the orchestrator**; the upstream \`farm-tour-booking\` skill is not patched. If the skill is later migrated into this repo, this filter moves into \`booking_flow.py list --phone\`.
" "phase:4-flows" "parallel-safe"

# ───────────────────────── #7 — handoff flow ─────────────────────────────
create_issue "#7 Phase 4c — handoff flow" "## Scope
Detect out-of-scope intents and tell the customer to contact the team. Notify the admins in WA.

## Triggers
\`router\` returns \`handoff(reason, summary)\` OR a flow ends in \`error\` that the router escalated.

## Acceptance criteria
- [ ] \`orchestrator/src/flows/handoff.py\` exports \`handle(decision, state, msg) -> Reply\`
- [ ] Parse \`WA_NOTIFY\` env (comma-split, trim, drop blanks, drop non-digits) at boot; cache the list
- [ ] For each admin phone, POST \`wa-bridge/send\` with: caller phone, original message (truncated 1000 chars), reason, summary, timestamp, detected language
- [ ] Reply to customer in their language: \`I've flagged this for the team — they'll be in touch shortly. For immediate help, contact +<ADMIN_CONTACT_NUMBER>.\`
- [ ] If \`WA_NOTIFY\` is empty: log a \`WARN\` and reply \`I've flagged this for the team — they'll be in touch shortly.\` (omit phone)
- [ ] If sending to one admin fails, continue with the others; collect all errors into a single log line
- [ ] pytest tests: notify-all happy, notify-some-fail, empty-WA_NOTIFY behavior, customer reply shape (EN + 中文)

## Depends on
#4

## Parallel with
#5, #6, #8, #9" "phase:4-flows" "parallel-safe"

# ───────────────────────── #8 — admin notify on new booking ──────────────
create_issue "#8 Phase 4d — admin notify on new booking" "## Scope
After a successful \`book_new\` commit, send a heads-up to the admins (same channel as handoff, different message shape).

## Acceptance criteria
- [ ] \`orchestrator/src/notify.py\` exposes \`notify_new_booking(event_id: str, customer_phone: str, summary: dict)\`
- [ ] Reuses the \`WA_NOTIFY\` cache from #7
- [ ] Message format (one per admin):
  \`🆕 New farm tour booking — <date> <time>, <pax> pax. Contact: <name> <email|phone>. Notes: <notes|—>. event_id=<…>\`
- [ ] Bilingual: en→en, zh→zh, detected at booking commit
- [ ] Send failures do NOT roll back the booking; log warn and continue
- [ ] pytest tests: success, partial-fail, language routing

## Depends on
#5

## Parallel with
#6, #7, #9" "phase:4-flows" "parallel-safe"

# ───────────────────────── #9 — multilingual EN/中文 ─────────────────────
create_issue "#9 Phase 5 — Multilingual EN / 中文" "## Scope
End-to-end language handling: detect, store, route, reply.

## Acceptance criteria
- [ ] \`orchestrator/src/i18n.py\` with \`SYSTEM_PROMPT_EN\` + \`SYSTEM_PROMPT_ZH\` and a \`pick(language)\` helper
- [ ] Router detects language on every inbound (LLM or fast heuristic if confident: zh chars ≥ 30% of letters → zh)
- [ ] Persists \`state.language\` (default = first detected)
- [ ] All flow replies go through an \`i18n.t(key, language, **kwargs)\` helper with both EN and 中文 variants
- [ ] All customer-facing strings in flows live in \`i18n.py\` (no hardcoded English in flow files)
- [ ] pytest tests: detect-zh, detect-en, all i18n keys present in both languages, no missing translation
- [ ] Adds 3 sample dialogues per language to \`docs/message-flows.md\` (FAQ, new booking, handoff)

## Depends on
#4

## Parallel with
#5, #6, #7, #8" "phase:5-i18n" "parallel-safe"

# ───────────────────────── #10 — image handling ──────────────────────────
create_issue "#10 Phase 6 — Image handling" "## Scope
Receive images from customers, ack them, store them, and let the LLM answer questions about them (best-effort, since we have no multimodal model by default).

## Acceptance criteria
- [ ] wa-bridge: download image to \`RAG_PHOTOS_DIR/inbound/<sha256[:16]>.<ext>\` and write \`{message_id, from, image: {path, sha256, filename}}\` to the NDJSON (text may be empty or a caption)
- [ ] orchestrator: on inbound with an image, save the metadata to state (\`state.last_image\`) and reply with an ack (\`Got the photo.\` / \`收到图片了。\`)
- [ ] If the caption is a question, prepend \`I have a photo at <path> from this chat.\` to the router's user-message; router may call \`faq\` to search the rag-photos corpus
- [ ] Rate limit: ignore inbound images >10 MB
- [ ] vitest + pytest tests: image-only message, image+caption, oversize rejected
- [ ] Update SKILL.md: image handling notes

## Depends on
#2, #3

## Parallel with
#4–#9, #11, #12, #13" "phase:6-media" "parallel-safe"

# ───────────────────────── #11 — ingest helper ───────────────────────────
create_issue "#11 Phase 7 — Ingest helper (booking_rules.yaml + faq.md)" "## Scope
One-shot scripts that push structured content into the existing Qdrant collection so the FAQ tool has something to answer with.

## Acceptance criteria
- [ ] \`orchestrator/scripts/ingest_rules.py\`: reads \`BOOKING_RULES_PATH\`, formats it as markdown sections (location, hours, slot duration, capacity, pricing, blackout, deposit), calls \`from rag_qdrant import ingest_text; ingest_text(markdown, source='booking_rules_v1')\`
- [ ] \`orchestrator/scripts/ingest_file.py <path>\`: supports \`.md\`, \`.txt\`, \`.pdf\`; \`source\` defaults to filename without ext
- [ ] Idempotent: re-running on the same \`source\` updates in place (the existing rag-qdrant skill handles point-id hashing)
- [ ] README: \`Data sources\` section updated
- [ ] pytest tests: yaml→markdown format with a fixture, file-type dispatch, source default
- [ ] Run once against the real collection; record the resulting \`source\` ids in the README

## Depends on
none — can start as soon as #1 lands

## Parallel with
#2–#10, #12, #13" "phase:7-data" "parallel-safe"

# ───────────────────────── #12 — hardening ───────────────────────────────
create_issue "#12 Phase 8a — Idempotency, dedupe, reconnection, SQLite WAL" "## Scope
Make the running system safe to leave alone for a week.

## Acceptance criteria
- [ ] orchestrator: every inbound \`message_id\` recorded in \`inbox_log(message_id PK, processed_at)\`; duplicates dropped before the LLM call
- [ ] orchestrator: \`state_log\` insert-only table capturing every state transition (phone, old_flow, new_flow, old_draft, new_draft, at)
- [ ] wa-bridge: outbound queue is a JSONL file; \`POST /send\` appends, a background worker drains and retries with backoff; \`GET /status\` shows queue depth
- [ ] wa-bridge: on Baileys \`close\`, exponential backoff reconnect (1s → 60s, jitter); on 4 successive QR-needs, log CRITICAL and stop auto-reconnect
- [ ] orchestrator: SQLite in WAL mode, \`synchronous=NORMAL\`, weekly \`VACUUM INTO\` snapshot under \`/var/lib/skill-chatbot/\`
- [ ] Crash safety: orchestrator restart resumes from last message_id; wa-bridge restart flushes the outbound queue
- [ ] vitest + pytest tests: dedupe, state-log row written, reconnect backoff schedule, queue drain after process kill
- [ ] Update \`docs/ops.md\` with the recovery procedures

## Depends on
#2, #3

## Parallel with
#4–#11, #13" "phase:8-hardening" "parallel-safe"

# ───────────────────────── #13 — systemd + ops ───────────────────────────
create_issue "#13 Phase 8b — systemd units + log rotation" "## Scope
Turn the two dev commands into managed services that survive reboots and don't fill the disk.

## Acceptance criteria
- [ ] \`scripts/install-systemd.sh\` writes the two unit files from \`docs/ops.md\` to \`~/.config/systemd/user/\` and runs \`daemon-reload\` + \`enable --now\`
- [ ] \`/etc/logrotate.d/skill-chatbot\` rotates both log files weekly, retains 12, compresses, copies without truncating
- [ ] \`Makefile\` targets: \`install-svc\`, \`uninstall-svc\`, \`restart\`, \`status\`
- [ ] SKILL.md: \`status\` subcommand shape updated to match what the scripts actually print
- [ ] ops.md: a 'first-time setup' section (clone → env → install → auth → enable) and a 'common incidents' section (session lost, OOM, log fill, swap SIM)

## Depends on
#2, #3

## Parallel with
#4–#12" "phase:8-hardening" "parallel-safe"

# ───────────────────────── #14 — smoke test ──────────────────────────────
create_issue "#14 Phase 9 — smoke.py (NDJSON replay + live E2E)" "## Scope
Two smoke modes: deterministic NDJSON replay, and a real end-to-end that talks to a test WhatsApp number.

## Acceptance criteria
- [ ] \`orchestrator/scripts/smoke.py\`:
  - default: reads \`tests/fixtures/inbox.ndjson\` (10+ lines, covers faq/book_new/book_edit/book_cancel/handoff/image/abuse), starts an in-process orchestrator, asserts reply sent to a mock bridge
  - \`--live\`: requires \`WA_SMOKE_PHONE\` env; routes a test booking through the real bridge + Composio; cleans up via \`booking_flow.py cancel\`
- [ ] Fixture set covers EN + 中文
- [ ] \`make smoke\` runs the default mode in <30s
- [ ] CI: \`smoke\` runs on every PR (no live mode in CI)
- [ ] pytest tests: the fixture replays return the expected tool choices and the expected first-line of each reply
- [ ] docs/ops.md: 'Run a smoke test' section

## Depends on
#5, #6, #7, #8

## Parallel with
none — last functional issue" "phase:9-smoke"

# ───────────────────────── #15 — sample dialogues + runbook ──────────────
create_issue "#15 Phase 9 — Sample dialogues (EN + 中文) + runbook" "## Scope
Fill out the docs so a new operator can drive the system without a Slack thread.

## Acceptance criteria
- [ ] \`docs/message-flows.md\` has at least 8 dialogues × 2 languages = 16 entries (faq, new-booking happy, new-booking full, new-booking unavailable, edit, cancel, handoff refund, handoff abuse, image question, mid-flow language switch)
- [ ] \`docs/runbook.md\`: 12 common incidents with copy-pasteable commands
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
- [ ] \`docs/security.md\`: what data is logged, retention, who can read state.sqlite, secret rotation

## Depends on
#1

## Parallel with
everything — pure docs" "phase:9-smoke" "parallel-safe" "documentation"

cat <<'BANNER'
Done. 15 issues filed.
BANNER
