# Build task — Issue #2 (Phase 1: wa-bridge: Baileys auth + receive + send + /health)

> You are the opencode **Build** agent. The project planning doc is `docs/plans/phase-0-bootstrap.md` — read it end-to-end. Issue #1 (project layout, package manifests, Makefiles) is already done; commit `023ed3e`. The GitHub issue is **#3** (the title prefix is #2, GitHub number is offset).

## What to build

A long-lived Node process that owns the Baileys socket, persists auth, and exposes a tiny HTTP surface for the orchestrator. **No business logic, no flows, no LLM calls** — that's issues #3 onward. This is just the transport.

## Acceptance criteria (verbatim from issue #3)

- [ ] `wa-bridge/src/index.ts` boots Baileys with `useMultiFileAuthState(WA_AUTH_DIR)`
- [ ] QR auth CLI: `npm run auth` prints QR to stdout, waits for sync, exits 0
- [ ] On every inbound non-group, non-status message, append one JSON line to `WA_AUTH_DIR/../inbox.ndjson`: `{message_id, from, text, image?, timestamp}`
- [ ] HTTP server on `WA_BRIDGE_PORT`:
  - `GET /health` → 200 `{ok:true, session:'ok'|'qr_needed'|'connecting'}`
  - `GET /status` → 200 `{session, last_message_at, queued_send}`
  - `POST /send {to, text}` (Bearer `WA_BRIDGE_TOKEN`) → 200 `{message_id}`; on fail 502 `{error:'send_failed', reason}`
- [ ] Auto-reconnect with exponential backoff (max 60s); logs to `WA_BRIDGE_LOG`
- [ ] Graceful SIGTERM: flush outbound queue, then close
- [ ] vitest tests: `send` happy path, `send` auth-missing, inbox line format

## Inputs you must respect

- The plan doc's §1 layout (file paths) and §2 tech choices (versions).
- `references/upstream/booking_flow.py` for the JSON shape conventions used elsewhere.
- The plan's **Risk #1, #2, #10** callouts (Baileys device cap, reconnect, NDJSON atomic writes).

## Hard constraints

- **No orchestrator code, no LLM calls, no Qdrant, no Composio.** This is purely the transport.
- **No real auth during tests.** Mock the Baileys socket in `tests/`. Don't try to scan a QR.
- **Do not push to the remote** — I'll push from the orchestrator side.
- **Do not edit the issue body, do not open new issues, do not create PRs.**
- **Commit** when done. Message: `feat(bridge): Baileys auth + receive + send + /health (#2)`.
- Use the existing `package.json` (`@whiskeysockets/baileys@^6.7.0`, `express@^4.19.2`, `pino@^9.4.0`, `zod@^3.23.8`, `tsx`, `vitest`, `typescript`).
- All env access goes through a `src/env.ts` zod-validated loader. No `process.env.X` reads scattered around.
- The HTTP server runs on the port from `WA_BRIDGE_PORT` (default 7788 from `.env.example`).
- Bearer token check on `POST /send` and any future state-changing endpoint — read from `WA_BRIDGE_TOKEN`.
- NDJSON line format is exactly: `{"message_id": "...", "from": "...", "text": "...", "image": {"path": "...", "sha256": "...", "filename": "..."} | null, "timestamp": "..."}`. Image field is null when there's no image.
- Inbound filter: skip `fromMe === true`, skip `type === 'status'`, skip messages whose `key.remoteJid` ends with `@g.us` (groups). Only process `@s.whatsapp.net` and `@c.us` 1:1 chats.
- `from` in the NDJSON line is the digits-only phone (strip `@s.whatsapp.net` / `@c.us` suffix) per plan risk #13.

## When you're done

Print to stdout:
1. The full `git status` of the repo.
2. The list of new files (relative paths).
3. The `git log --oneline -3`.
4. The test command output: `cd wa-bridge && npx vitest run` (use `--reporter=verbose` so I can see each spec).
5. A one-paragraph summary: what each new file does, any deviation from the plan, the SHA.
