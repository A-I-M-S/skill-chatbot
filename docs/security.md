# Security

What data the chatbot handles, where it lives, who can read it, how secrets rotate. Pair with `docs/ops.md` and `docs/runbook.md`.

## Data we handle

| Class | What | Where | Retention |
|---|---|---|---|
| **Message content** | Customer text + captions + image paths | `wa-bridge/inbox.ndjson` (raw), `orchestrator/state.sqlite` (`processed_messages`, `last_image`, `state_log`) | Indefinite (operational; not auto-purged) |
| **Phone numbers** | WhatsApp caller IDs (digits only) | `state.sqlite` (`processed_messages.from`, `phone_state.phone`) | Indefinite |
| **Contact details** | Email / phone collected for bookings | Sent to Composio Outlook as event attendees; not stored in our DB beyond the booking draft | Until the event is deleted from Outlook |
| **Images** | Inbound photos from customers | `<RAG_PHOTOS_DIR>/inbound/<sha>.<ext>` (content-addressed) | Indefinite (no rotation in v1 — plan risk #12 calls for a weekly prune later) |
| **Admin DMs** | Outbound notifications to admins | `journalctl` (bridge + orchestrator logs) + WA message history on the admin phones | Indefinite |
| **Config / secrets** | Bridge token, inference API key, Composio key, Qdrant key, admin phone numbers | `.env` (gitignored, chmod 600), systemd `EnvironmentFile=` | Until manually rotated |
| **Audit log** | Every state transition per phone | `state.sqlite` (`state_log` table, insert-only) | Indefinite (append-only) |
| **Bridge session** | WhatsApp creds (noise key + signed identity) | `wa-bridge/auth_info/` (gitignored) | Until `npm run auth` re-links |

## Who can read what

| Data | Repo | Host filesystem | Logs | Composio |
|---|---|---|---|---|
| `state.sqlite` | n/a | root + systemd user | n/a (not in logs) | n/a |
| `auth_info/` | n/a | root + systemd user | n/a | n/a |
| `.env` | n/a | root + systemd user | n/a (tokens are redacted from logs) | n/a |
| `inbox.ndjson` | n/a | root + systemd user | n/a | n/a |
| `<RAG_PHOTOS_DIR>/inbound/` | n/a | root + systemd user | n/a | n/a |
| `journalctl` | n/a | n/a | root via `journalctl --user` | n/a |
| Outlook event attendees | n/a | n/a | n/a | Composio OAuth scope (per-account) |

Only the operator who runs the box (Boon or whoever has root + systemd linger) has filesystem access. There's no API for reading the data — it's all local. The Composio connection is the operator's OAuth-granted scope on their Outlook account.

## Logging policy

- **No message content in the orchestrator log** beyond the truncated first-line of the reply (the bridge already shows the full text in its NDJSON).
- **Phone numbers in the log** are digits-only (we strip the JID suffix at ingestion). Full international format is preserved for `+countrycode` (we just drop the `@s.whatsapp.net` suffix).
- **API keys are never logged** — the inference wrapper scrubs `Authorization` headers before any error path touches the logger.
- **Customer images are not logged** — only their `sha256` (so we can dedup) and the filename.

## Secret rotation

| Secret | How to rotate | Cadence |
|---|---|---|
| `WA_BRIDGE_TOKEN` | `openssl rand -hex 32`, update `.env`, restart bridge | Quarterly (or on operator change) |
| `INFERENCE_API_KEY` | Provider dashboard → rotate → update `.env`, restart orchestrator | Quarterly |
| `COMPOSIO_API_KEY` | Composio dashboard → regenerate → update `.env`, restart | Quarterly |
| `QDRANT_API_KEY` | Qdrant Cloud → rotate → update `.env`, restart | Quarterly |
| WhatsApp session | `rm -rf wa-bridge/auth_info && npm run auth` | On number change, or if creds leak |
| `.env` file permissions | `chmod 600 /root/.openclaw/workspace/dev/projects/skill-chatbot/.env` | After every edit |

## Threat model (v1)

The chatbot is a small, single-tenant service. The realistic threats:

- **Operator compromise** — root on the box = full access to everything. Mitigated by `.env` chmod 600 + (TODO) full-disk encryption at rest.
- **Network compromise** — the bridge talks to WhatsApp over WebSocket. Mitigated by Baileys' built-in E2E (WhatsApp protocol is encrypted end-to-end, so a network observer sees opaque frames).
- **Abuse spam** — a customer sending abusive messages. Mitigated by the `handoff(reason=abuse)` path + admin DM.
- **Data exfiltration via logs** — mitigated by the logging policy above.
- **Cross-customer data leak** — mitigated by per-phone state isolation (`phone_state` keyed by phone).

Not yet mitigated (planned for v2):

- **DDoS via inbound NDJSON flood** — no rate limit on the bridge's `/send` consumption rate.
- **Prompt injection via FAQ corpus** — a poisoned `faq.md` could trick the LLM into saying things it shouldn't.
- **GDPR right-to-be-forgotten** — we don't yet have a "delete this customer" operator command.

## What we don't log

- Customer email content (only the address)
- Customer image bytes (only the sha256 + filename)
- LLM prompt + completion (would leak customer PII into inference provider's logs; we don't enable that on the provider side)
- WA_BRIDGE_TOKEN, COMPOSIO_API_KEY, INFERENCE_API_KEY (all scrubbed at logger boundary)

## References

- `docs/ops.md` — architecture + systemd + recovery procedures
- `docs/runbook.md` — 13 incident-handling playbooks
- `docs/security.md` — this file
- `references/upstream/` — read-only snapshots of `rag-qdrant` + `farm-tour-booking`
- `src/notify.py` — never raises, all errors logged at WARN
- `src/router.py` — pydantic-validates tool args, falls back to `handoff` on schema mismatch
- `src/state.py` — `phone_state` per-phone isolation, `state_log` append-only