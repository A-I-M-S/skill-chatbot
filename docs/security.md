# Security

> What data the `skill-chatbot` stack touches, what it logs, who can read
> what, and how secrets are rotated. Read [architecture.md](architecture.md)
> for the topology this document protects, and [runbook.md](runbook.md)
> for the operational side of the same boundary.
>
> **TL;DR for auditors**
> - Conversation state lives in one SQLite file at `ORCHESTRATOR_DB`
>   (default `orchestrator/state.sqlite`), chmod `600`, owner = run-as user.
> - Logs are append-only JSONL in `/var/log/skill-chatbot/`, rotated weekly,
>   retained 12 weeks, never contain raw message bodies from abuse tickets
>   (they contain `reason` + a 200-char preview only).
> - Credentials live in `auth_info/` (Baileys) and `.env` (everything else);
>   both are chmod `600`, gitignored, and never leave the host.

---

## 1. Threat model (v1)

| Adversary | Goal | Mitigation |
|---|---|---|
| Stranger on the open internet | Spam / abuse the bot | Handoff on `reason=abuse`; soft block via `state.sqlite`; hard block via bridge `blocked.txt` (runbook §7) |
| Curious teammate | Read customer messages | `state.sqlite` is `chmod 600`; `/var/log/skill-chatbot/` is `chmod 750`, group = run-as user only |
| Compromised subprocess (`booking_flow.py`) | Exfiltrate `COMPOSIO_API_KEY` | Subprocess runs in the orchestrator's venv with no network beyond the Composio endpoint (no shell, no `os.system`) |
| Stolen backup | Replay sessions | `state.sqlite` backups are encrypted at rest (see §4); `auth_info/` is never backed up off-host |
| LLM prompt injection | Make the bot reveal system prompts / exfiltrate state | Tools are whitelisted to 5 names; tool-call args are pydantic-validated; pii-redaction on outbound logs (see §3) |
| Meta / WhatsApp | Account ban for ToS violation | Replies-only flow (no template messages); single linked device; we never initiate contact with a customer who hasn't messaged us first |

Out of scope for v1: end-to-end message encryption between bridge and
orchestrator (both daemons run on the same host, communicate over
loopback HTTP with a bearer token — see §2.3).

---

## 2. Trust boundaries

### 2.1 Network

```
customer phone ──► WhatsApp ──► wa-bridge  (loopback HTTP) ──► orchestrator
                                   │                              │
                                   ▼                              ▼
                              Qdrant (loopback)            Composio / OpenAI
                                                          (outbound HTTPS)
```

- The bridge exposes `POST /send` and `GET /health, /status` on
  `127.0.0.1:7788` (configurable via `WA_BRIDGE_PORT`). It does **not**
  bind to a public interface.
- The orchestrator exposes `GET /health` on `127.0.0.1:7789`. No
  write endpoint.
- The bridge never reaches the internet directly other than the
  Baileys WebSocket to `web.whatsapp.com` and image downloads from
  WhatsApp's media CDN.

### 2.2 Filesystem

| Path | Owner | Mode | Git | Off-host backup? |
|---|---|---|---|---|
| `auth_info/` | run-as user | `700` | ignored | **no** — credential |
| `state.sqlite` | run-as user | `600` | ignored | yes, encrypted (§4) |
| `state.sqlite-wal`, `-shm` | run-as user | `600` | ignored | yes, with `.sqlite` |
| `inbox.ndjson` | run-as user | `640` | ignored | optional, weekly |
| `wa-bridge/queue/outbound.jsonl` | run-as user | `640` | ignored | no |
| `/var/log/skill-chatbot/*.log` | root:run-as user | `640` | n/a | yes, logrotate §3 |
| `RAG_PHOTOS_DIR/inbound/` | run-as user | `750` | ignored | optional, monthly |

Enforce the modes after every deploy:

```bash
chmod 700 "$REPO/wa-bridge/auth_info"
chmod 600 "$REPO/orchestrator/state.sqlite"*
chmod 750 /var/log/skill-chatbot
chown -R root:$(id -un) /var/log/skill-chatbot
chmod 640 /var/log/skill-chatbot/*
```

### 2.3 Inter-process auth

`POST /send` requires `Authorization: Bearer $WA_BRIDGE_TOKEN`. The
token is a 32-byte random hex string in `.env`, rotated per §5.

The orchestrator reads the same `.env` to set the `Authorization`
header. **Never** log the bearer token; `pino` and stdlib `logging`
are both configured to redact it (see §3.2).

---

## 3. Logging

### 3.1 What is logged

| Log | Where | Format | Contents |
|---|---|---|---|
| Bridge events | `bridge.log` (pino) | NDJSON | connect/disconnect, send/recv (no body), `qr` events, errors |
| Orchestrator per-message | `orchestrator.log` | NDJSON | phone (E.164), detected intent, tool called, latency ms, token counts |
| State transitions | `state.sqlite` → `state_log` table | SQLite | `phone, flow, ts, diff_json` — never message bodies |
| Inbound messages | `inbox.ndjson` | NDJSON | `message_id, phone, ts, body_sha256, type, body_preview (≤200 chars), image_path?` |
| Outbound messages | `bridge.log` only | NDJSON | `to, message_id, ts, body_sha256` — no body |
| Abuse handoff | `orchestrator.log` + `bridge.log` | NDJSON | `phone, reason=abuse, ts, body_sha256, preview≤200` |
| LLM tool calls | `orchestrator.log` | NDJSON | `tool_name, args_redacted, latency_ms, model, tokens_in/out` |

**What is never logged:**
- Raw message bodies of `reason=abuse` handoffs (only a 200-char preview
  + SHA-256).
- LLM `messages[].content` arrays.
- `COMPOSIO_API_KEY`, `INFERENCE_API_KEY`, `QDRANT_API_KEY`,
  `WA_BRIDGE_TOKEN`, `WA_NOTIFY`, `ADMIN_CONTACT_NUMBER`.
- `auth_info/creds.json` or any Baileys session blob.

### 3.2 Redaction

Both loggers use the field name `redact`:

- **pino** (`wa-bridge/src/log.ts`):
  `redact: ['req.headers.authorization', 'env.COMPOSIO_API_KEY', ...]`
- **stdlib logging** (`orchestrator/src/main.py`): a
  `logging.Filter` that walks every `LogRecord` and replaces
  known-sensitive keys with `"[REDACTED]"`.

The keys live in a single module — `orchestrator/src/secret_keys.py`
and `wa-bridge/src/log.ts` — so adding a new env var means one edit
in two places.

### 3.3 Retention

| File / table | Retention | Tool | Notes |
|---|---|---|---|
| `/var/log/skill-chatbot/*.log` | 12 weeks, weekly rotation | `logrotate` | `copytruncate`, compress, postrotate `systemctl --user reload-or-restart` |
| `state.sqlite` | 30 days daily backups | `scripts/backup-state.sh` (out of scope v1) | encrypted at rest (§4) |
| `inbox.ndjson` | optional, 7 days | `logrotate` or manual | bigger files — keep only for replay |
| `RAG_PHOTOS_DIR/inbound/` | 30 days | `scripts/prune_inbound_photos.py` (runbook §14) | not in v1; flag for ops |
| `state_log` (table) | forever (insert-only) | n/a | audit trail; vacuum monthly |

After 12 weeks the rotated logs are deleted by `logrotate` and not
recoverable. If you need longer for an incident, copy the active file
to an incident folder under `chmod 700` before rotation.

---

## 4. Backups

`state.sqlite` is the only thing we back up off-host.

```
/var/backups/skill-chatbot/
├── state.sqlite.2026-06-24.enc   # age-encrypted (see below)
├── state.sqlite.2026-06-23.enc
└── …
```

- Daily cron at 02:00 SGT, owner = run-as user, mode `600`.
- Encryption: `age` (or `gpg --symmetric` if `age` is unavailable) with
  a passphrase stored in the operator's secret manager, not on the
  host.
- Restore: see runbook §9.
- **Never** back up `auth_info/` — it is the WhatsApp credential and
  moving it off-host expands the trust boundary to the backup medium.
  If you need disaster recovery for the session, the answer is "re-link
  on a new device" (runbook §3), not "restore from backup."

---

## 5. Secret rotation

| Secret | Where | Rotation cadence | How |
|---|---|---|---|
| `WA_BRIDGE_TOKEN` | `.env` | quarterly, or on personnel change | `openssl rand -hex 32`; restart both daemons |
| `INFERENCE_API_KEY` | `.env` | per provider policy (≥ 90d) | provider dashboard → revoke + reissue; restart orchestrator |
| `QDRANT_API_KEY` | `.env` | quarterly | provider dashboard; restart Qdrant client (orchestrator reads on every call) |
| `COMPOSIO_API_KEY` | `.env` | quarterly | provider dashboard; restart orchestrator |
| `COMPOSIO_CONNECTED_ACCOUNT_ID` / `COMPOSIO_ENTITY_ID` | `.env` | per account, on staff change | unlink the Outlook account in Composio, re-link as a new connected account |
| `WA_NOTIFY` | `.env` | on admin rotation change | edit `.env`, restart orchestrator |
| `ADMIN_CONTACT_NUMBER` | `.env` | when the published number changes | edit `.env`, restart orchestrator |
| Baileys `auth_info/creds.json` | `auth_info/` | never (it IS the session) | wipe + re-link (runbook §3) |
| Backup passphrase | operator secret manager | annually | rotate in secret manager; old backups remain decryptable until expiry |

`.env` permissions on every change:

```bash
chmod 600 "$REPO/.env"
git diff --exit-code "$REPO/.env"   # must be untracked; abort if not
```

`.env` is gitignored but operators have been known to copy values into
commits by accident. Run `git log -p -- .env` before any commit that
touches config files.

---

## 6. Personal data inventory

| Data | Where it lives | Retention | Erasure |
|---|---|---|---|
| Phone number (E.164) | `state.sqlite.phone`, `inbox.ndjson`, `bridge.log`, `orchestrator.log` | logs: 12 weeks; state: 30 days; `state_log`: forever (with phone) | `DELETE FROM phone_state WHERE phone='…'` + `VACUUM`; `truncate` rotated logs (already gone) |
| Message body | `inbox.ndjson.body_preview` (≤200 chars), `state.phone_state.history` (last 8 turns) | 7 / 30 days | drop row from `phone_state`; rotate inbox |
| Image attachments | `RAG_PHOTOS_DIR/inbound/<sha>.<ext>` | 30 days | runbook §14; `rm` + log to `audit.log` |
| Booking details (date, pax, contact name, email) | `state.phone_state.draft`, `state_log`, downstream Outlook event | 30 days; Outlook event lifetime | normal booking edit/cancel; or `DELETE FROM phone_state` + manual Outlook event deletion |
| Contact name + email | `state.phone_state.draft` | 30 days | as above |
| Conversation language | `state.phone_state.language` | 30 days | as above |

To honour a customer deletion request:

```bash
PHONE='+65XXXXXXXX'
REPO=/root/.openclaw/workspace/dev/projects/skill-chatbot
sqlite3 "$REPO/orchestrator/state.sqlite" <<SQL
DELETE FROM state_log      WHERE phone='$PHONE';
DELETE FROM phone_state    WHERE phone='$PHONE';
SQL
sqlite3 "$REPO/orchestrator/state.sqlite" 'VACUUM;'
find "$RAG_PHOTOS_DIR/inbound" -mtime +0 -delete   # if we hold any image for them
```

Document the request and the action in `docs/audit.log` (chmod `600`,
gitignored, not rotated). The orchestrator's next message from that
phone starts a fresh conversation.

---

## 7. Incident response (security)

| Event | First action | Then |
|---|---|---|
| `auth_info/` leaked | `rm -rf auth_info`; re-link (§3) | audit `bridge.log` for any send between leak and revoke |
| `.env` leaked | rotate **every** secret in §5; restart both daemons | review `orchestrator.log` and `bridge.log` for the window between leak and rotate |
| Customer PII leak in logs | identify the line; `logrotate -f` to ship the file off-host under a ticket reference | add the field to the redact list (§3.2); open a follow-up |
| Compromised subprocess (`booking_flow.py`) | `systemctl --user stop skill-chatbot-orchestrator` | revoke `COMPOSIO_API_KEY`; review `orchestrator.log` for unexpected `os.system` / network calls |
| WhatsApp account banned | runbook §11 (phone number changed) | notify customers via the secondary channel (`ADMIN_CONTACT_NUMBER`) |
| LLM produced a customer's PII to another customer | check `state.phone_state` for the leaked-from phone; check `state_log` for any cross-flow | if confirmed, escalate to the LLM provider; the router's tools are whitelisted and pii-redaction is on by default, so this should not happen in v1 |

---

## 8. See also

- [architecture.md](architecture.md) — topology and trust boundaries
- [ops.md](ops.md) — backups, systemd, smoke test
- [runbook.md](runbook.md) — incident playbook (operational side of the same boundary)
- [message-flows.md](message-flows.md) — what handoff reasons exist
- bootstrap plan §5 — risks that motivated this doc (Q11 run-as user, Q12 corpus, Q13 phone canonicalisation)
