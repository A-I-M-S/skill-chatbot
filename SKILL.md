---
name: "skill-chatbot"
description: "Operate the SAAC FARM WhatsApp chatbot: start/stop bridge + orchestrator, watch logs, re-auth QR, run smoke tests, list GitHub issues, view deployment status."
---

# skill-chatbot

Project-management skill for the WA Farm Tour chatbot. The chatbot itself is a long-running service (Node `wa-bridge` + Python `orchestrator`); this skill is the operator surface — start, stop, observe, repair, ship.

> **The chat service is the work, not this skill.** This skill just drives the project: it does not parse customer messages, does not call the LLM, does not touch Qdrant or Outlook.

## When to invoke

- Status / health of the running chatbot
- Start or stop the bridge or orchestrator
- Tail logs (`wa-bridge`, `orchestrator`, smoke test)
- Re-link WhatsApp (pairing code) when the Baileys session drops
- Run a smoke test (NDJSON replay or end-to-end)
- Ingest a new FAQ source into Qdrant
- List / triage GitHub Issues for the project
- Pull latest and redeploy

Do **not** invoke to read or answer customer WhatsApp messages — that path is the running service.

## Required env

| Var | Source | Purpose |
|---|---|---|
| `CHATBOT_REPO` | `.env` | Repo root (default `~/projects/skill-chatbot`) |
| `WA_NOTIFY` | `.env` | Comma list of admin phone numbers (handoff alerts) |
| `ADMIN_CONTACT_NUMBER` | `.env` | Phone number the bot tells customers to call on handoff |
| `QDRANT_URL`, `QDRANT_API_KEY` | shared with `rag-qdrant` | RAG backend |
| `INFERENCE_BASE_URL`, `INFERENCE_API_KEY`, `INFERENCE_MODEL` | shared with `rag-qdrant` | LLM (MiniMax-M3 already authed) |
| `COMPOSIO_API_KEY`, `COMPOSIO_CONNECTED_ACCOUNT_ID` | shared with `farm-tour-booking` | Outlook calendar |

## Operating the stack

The two daemons run under **system** systemd on the box (installed by
`scripts/install-systemd-system.sh`). Everything below uses commands that
exist today — no wrapper scripts to generate. `REPO` is the checkout root
(prod: `/opt/skill-chatbot`).

| Unit | Port | Health |
|---|---|---|
| `skill-chatbot-wa-bridge.service` | 7788 | `GET /status` |
| `skill-chatbot-orchestrator.service` | 7789 | `GET /health` |

### Status

```bash
curl -s localhost:7788/status | jq .    # {session, last_message_at, reconnecting, attempt, qr_needed_count}
curl -s localhost:7789/health | jq .    # {ok, db, last_processed_message_id}
systemctl status skill-chatbot-wa-bridge skill-chatbot-orchestrator --no-pager
```

`session` is `ok` | `connecting` | `qr_needed`. `qr_needed` means the
WhatsApp session dropped — re-link with the pairing-code routine below.

### Start / stop / restart

```bash
sudo systemctl start   skill-chatbot-wa-bridge skill-chatbot-orchestrator
sudo systemctl stop    skill-chatbot-wa-bridge skill-chatbot-orchestrator
sudo systemctl restart skill-chatbot-wa-bridge        # bridge only
sudo systemctl restart skill-chatbot-orchestrator     # orchestrator only
```

### Logs

```bash
sudo journalctl -u skill-chatbot-wa-bridge    -n 200 -f
sudo journalctl -u skill-chatbot-orchestrator -n 200 -f
```

### Re-link WhatsApp — pairing code (NO QR)

This is the **one** way to (re)link the session. QR is not used. Set
`WA_PAIR_NUMBER` in `/etc/skill-chatbot.env` (the WhatsApp number in E.164)
once, then the routine is deterministic:

```bash
sudo systemctl stop skill-chatbot-wa-bridge                 # 1. free the auth dir
cd $REPO/wa-bridge && sudo -E npm run auth:code             # 2. prints ONE 8-char code
#    (or pass the number explicitly: sudo -E npm run auth:code -- +6591234567)
# 3. On the phone: WhatsApp → Settings → Linked Devices → Link a Device
#    → "Link with phone number instead" → enter the code.
#    Wait for "paired successfully". The code does NOT rotate; if it lapses
#    before you type it, just re-run step 2.
sudo systemctl start skill-chatbot-wa-bridge                # 4. bring the bridge back
curl -s localhost:7788/status | jq -r .session             # 5. expect "ok"
```

If it exits without pairing, re-run step 2 and enter the fresh code
promptly — do not try other commands. Creds persist at `WA_AUTH_DIR`
(`/var/lib/skill-chatbot/wa-bridge/auth`) and survive restarts, so this is
only needed when the number is genuinely unlinked (`session: qr_needed`).

> Emergency QR fallback only (not the routine): `sudo bash scripts/wa-bridge-qr.sh`
> renders a scannable PNG. Prefer the pairing-code flow above.

### Smoke test

```bash
cd $REPO/orchestrator && python3 scripts/smoke.py          # NDJSON replay
```

### Ingest helper

```bash
cd $REPO/orchestrator
python3 scripts/ingest_rules.py                            # push booking_rules.yaml into Qdrant
python3 scripts/ingest_file.py /path/to/faq.md
```

### Issues

```bash
gh issue list  --repo A-I-M-S/skill-chatbot
gh issue view  <number> --repo A-I-M-S/skill-chatbot
gh issue create --repo A-I-M-S/skill-chatbot --title "..." --body "..." --label "phase:4-flows"
```

## Output contract

The health endpoints return one JSON object; surface the `error`/`ok` field
on failure. Never leak the WhatsApp session creds, the pairing code, or
`INFERENCE_API_KEY` in logs.

## Edge cases

- `bridge_down` — wa-bridge not running; `sudo systemctl start skill-chatbot-wa-bridge` or check logs
- `qr_needed` — session invalid; run the pairing-code re-link routine above
- `composio_failed` — propagates from `farm-tour-booking`; the orchestrator will have already notified `WA_NOTIFY`
- `inference_429` — LLM rate-limited; the orchestrator retries with backoff and tells the user briefly
- `qdrant_unreachable` — orchestrator falls back to "I'll have the team reach out" and notifies admins

## Image handling (issue #10)

Inbound WhatsApp **images** are saved to disk and acked; if the image has a caption that looks like a question, the caption is routed to the LLM with the photo path prepended.

### Where images live

`<RAG_PHOTOS_DIR>/inbound/<sha256[:16]>.<ext>` — content-addressed, dedupes automatically (same bytes = same file). `RAG_PHOTOS_DIR` defaults to `/root/rag-photos` (shared with `rag-qdrant`). There is **no rotation** in v1; plan risk #12 calls out a weekly prune job to add later.

### Pipeline

1. **wa-bridge** (`src/image.ts` + `socket.ts`): detects an `imageMessage`, downloads the bytes via Baileys (`downloadMediaMessage`), writes them to `<RAG_PHOTOS_DIR>/inbound/<sha>.<ext>`, and appends an NDJSON line `{message_id, from, text, image: {path, sha256, filename}, timestamp}` to the inbox.
2. **Rate limit**: `MAX_IMAGE_BYTES` (default **10 MiB**) is checked twice — first against the declared `fileLength` in the `imageMessage`, then against the actual byte length after download. Oversize images are silently dropped: the NDJSON line is still written (so the caption / message body is preserved) but `image` is `null` and a `onImageRejected` hook fires.
3. **orchestrator** (`src/image_handler.py`): when an inbox line carries an image, the orchestrator saves the metadata to `state.last_image` (new SQLite table keyed by sender phone, upsert on conflict) and replies with a short ack in the customer's language:
   - English caption / no caption → `Got the photo.`
   - Chinese caption → `收到图片了。`
4. **Caption routing**: if the caption looks like a question (ends in `?` / `？`, or starts with a WH-word in EN/ZH), the photo path is prepended to the user message and the router calls `rag.ask_with_photo(question, photo_path)`. Non-question captions (e.g. `"see this"`) → ack only, no RAG call.
5. **No real Qdrant ingest at the orchestrator level** — the bridge has already saved the file. The `rag-qdrant` photo path (via the `Photo` / `ingest_photo` API) is the source of truth for embedding descriptions; #10 just hands the photo context to the router and lets the existing photo corpus search do the work. v1 is best-effort because we have no multimodal model by default.

### NDJSON contract (v1, image field)

```json
{
  "message_id": "ABC123",
  "from": "6591234567",
  "text": "what is this?",
  "image": {
    "path": "/root/rag-photos/inbound/5c6fb3dfe09f.jpg",
    "sha256": "5c6fb3dfe09f...",
    "filename": "inbound.jpg"
  },
  "timestamp": "2026-06-24T10:00:00Z"
}
```

`image` is `null` when the inbound had no image, or when the image was dropped for being oversize.

### Supported image types

`.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`, `.bmp`, `.tiff`, `.heic`, `.heif`. (Mirrors the `rag-qdrant` photo-support section in `references/upstream/rag-qdrant-SKILL.md`.)

### Operational notes

- **Disk growth**: there is no rotation script yet — see plan risk #12. Until that's added, manually `find /root/rag-photos/inbound -mtime +30 -delete` if needed.
- **Inbound + LLM**: the orchestrator never *ingests* the photo into Qdrant. Only the description (the caption) is vectorised by `rag-qdrant.ask` via the existing photo corpus search.
- **Bridge outage during image**: if the bridge is down, the WhatsApp message will simply be re-delivered by WhatsApp once the bridge reconnects; no special handling.
- **Tests**: vitest covers image download, dedupe, oversize rejection (declared + actual), and the full socket → inbox integration. Pytest covers image-only ack, image+question caption routing, image+non-question ack-only, and the bridge-dropped-oversize path. Real Baileys and Qdrant are never hit — both are mocked.

## Escalation to Boon

- WhatsApp session cannot be re-linked via pairing code (number banned, hardware lost)
- Composio / Outlook account is down for >15 min during business hours
- Repeated LLM 4xx/5xx after backoff (likely model config drift)
- Customer is abusive → escalate to Boon; there is no self-serve block command yet

## Reference

- `docs/architecture.md` — full architecture + deployment topology
- `docs/ops.md` — systemd units, log rotation, restart-on-crash
- `docs/message-flows.md` — sample dialogues in EN and 中文
- README.md — what the chatbot does and how to run it
- GitHub: https://github.com/A-I-M-S/skill-chatbot · Issues: https://github.com/A-I-M-S/skill-chatbot/issues
