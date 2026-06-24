# Ops

## systemd units (user-level)

`~/.config/systemd/user/skill-chatbot-bridge.service`

```ini
[Unit]
Description=skill-chatbot wa-bridge
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/.openclaw/workspace/dev/projects/skill-chatbot/wa-bridge
EnvironmentFile=/root/.openclaw/workspace/dev/projects/skill-chatbot/.env
ExecStart=/usr/bin/node dist/index.js
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/skill-chatbot/bridge.log
StandardError=append:/var/log/skill-chatbot/bridge.log

[Install]
WantedBy=default.target
```

`~/.config/systemd/user/skill-chatbot-orchestrator.service`

```ini
[Unit]
Description=skill-chatbot orchestrator
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/.openclaw/workspace/dev/projects/skill-chatbot/orchestrator
EnvironmentFile=/root/.openclaw/workspace/dev/projects/skill-chatbot/.env
ExecStart=/root/.openclaw/workspace/dev/projects/skill-chatbot/orchestrator/.venv/bin/python -m src.main
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/skill-chatbot/orchestrator.log
StandardError=append:/var/log/skill-chatbot/orchestrator.log

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now skill-chatbot-bridge skill-chatbot-orchestrator
loginctl enable-linger $USER   # survive logout
```

## Re-auth (Baileys session dropped)

```bash
systemctl --user stop skill-chatbot-bridge
cd /root/.openclaw/workspace/dev/projects/skill-chatbot/wa-bridge
npm run auth    # prints QR to stdout
# scan from WhatsApp → Linked Devices → Link a Device
systemctl --user start skill-chatbot-bridge
```

## Smoke test

```bash
# Replay recorded NDJSON (no real WA traffic)
python3 orchestrator/scripts/smoke.py

# End-to-end (creates real bookings in the Outlook calendar — use the test account only)
python3 orchestrator/scripts/smoke.py --live
```

## Logs

```bash
journalctl --user -u skill-chatbot-bridge -f
journalctl --user -u skill-chatbot-orchestrator -f
tail -f /var/log/skill-chatbot/*.log
```

## Backups

- `state.sqlite` — daily, retained 30d. Restore by `cp` + `chmod 600`.
- `auth_info/` — never back up off-host. Treat as a credential.
- `inbox.ndjson` — optional, useful for replay. Rotate weekly.

## Recovery procedures (issue #12 hardening)

The system is designed to recover automatically from transient failures. The runbook below covers the cases that need human intervention.

### Check the live state

```bash
# Are both daemons up?
systemctl --user status skill-chatbot-bridge skill-chatbot-orchestrator

# Bridge: is the session healthy? How many messages are queued?
curl -s http://127.0.0.1:7788/status | jq

# Orchestrator: is the DB open? What's the last processed message?
curl -s http://127.0.0.1:7789/health | jq

# How many messages are sitting in the inbound NDJSON tailer (not yet processed)?
wc -l wa-bridge/inbox.ndjson

# How many are queued for outbound (drained but failed)?
wc -l wa-bridge/queue/outbound.jsonl
```

### Replay stuck outbound messages

If `wa-bridge/queue/outbound.jsonl` is non-empty and the bridge is up, the drain loop will pick it up automatically (the bridge drains on startup and on every `/status` call). If the drain is failing on a specific message:

```bash
# Inspect the failing line
tail -1 wa-bridge/queue/outbound.jsonl | jq

# If the recipient is wrong or the text is bad, edit the line or drop it:
# (do NOT use rm on the file — append-only)
sed -i '$d' wa-bridge/queue/outbound.jsonl
```

### Bridge reconnect / give-up

The bridge auto-reconnects with exponential backoff (1s → 2s → 5s → 10s → 20s → 40s → 60s, capped at the configured max). After 4 successive QR-needs the bridge stops auto-reconnecting and logs `CRITICAL: too many QR cycles`. To relink:

```bash
systemctl --user stop skill-chatbot-bridge
cd wa-bridge && npm run auth    # scan QR from WhatsApp
systemctl --user start skill-chatbot-bridge
```

### Orchestrator: replay state_log

`state_log` is an append-only audit of every flow transition. To reconstruct a customer's last interactions:

```bash
# From the host:
sqlite3 orchestrator/state.sqlite "SELECT at, old_flow, new_flow FROM state_log WHERE phone = '6591234567' ORDER BY at DESC LIMIT 20"

# Or via a one-liner (using the orchestrator's Python):
cd orchestrator && . .venv/bin/activate && python -c "
from src.state import State
s = State('state.sqlite')
for row in s.get_state_log('6591234567', limit=20):
    print(f\"{row['at']:.0f}\t{row['old_flow']}\t→\t{row['new_flow']}\tdraft={row['new_draft']}\")
"
```

### State DB corruption

If `state.sqlite` is corrupted (e.g. `sqlite3` reports `database disk image is malformed`):

```bash
# 1. Stop the orchestrator
systemctl --user stop skill-chatbot-orchestrator

# 2. Back up the corrupt DB
cp orchestrator/state.sqlite orchestrator/state.sqlite.corrupt.$(date +%s)

# 3. Re-create the schema (loses dedupe + last_image + state_log; bridge queue is untouched so unsent messages will still be sent)
cd orchestrator && . .venv/bin/activate
python -c "from src.state import State; State('state.sqlite')"  # creates empty schema
# OR just: rm orchestrator/state.sqlite && systemctl --user start skill-chatbot-orchestrator
# (the orchestrator re-creates the schema on first boot)

# 4. Note: replays are *not* possible from the inbox.ndjson tail because
# the offset file is per-DB. The next inbound message will start a fresh
# offset. To re-process the inbox from scratch:
rm orchestrator/state.sqlite.*offset
systemctl --user start skill-chatbot-orchestrator
```

### Dedupe: same message_id delivered twice

The `processed_messages` table records every processed `message_id`. If Baileys re-delivers (e.g. on reconnect), the second delivery is dropped before the LLM is called. To manually re-process a message:

```bash
sqlite3 orchestrator/state.sqlite "DELETE FROM processed_messages WHERE message_id = 'XXX';"
# Replay the NDJSON line: re-append it to the inbox, the tailer will pick it up.
```


## First-time setup

```bash
# 1. Clone + env
git clone https://github.com/A-I-M-S/skill-chatbot ~/projects/skill-chatbot
cd ~/projects/skill-chatbot
cp .env.example .env
# Fill in: QDRANT_*, INFERENCE_*, COMPOSIO_*, WA_BRIDGE_TOKEN, WA_NOTIFY, ADMIN_CONTACT_NUMBER, RAG_PHOTOS_DIR
chmod 600 .env

# 2. wa-bridge (Node)
cd wa-bridge
npm ci
npm run build
cd ..

# 3. orchestrator (Python)
cd orchestrator
python3.11 -m venv .venv  # or python3.13 — whatever's on the box; requires-python = ">=3.11"
. .venv/bin/activate
pip install -e '.[dev]'
cd ..

# 4. Auth the bridge (scan QR from your WhatsApp app)
cd wa-bridge
npm run auth    # prints QR — scan from WhatsApp → Linked Devices → Link a Device
cd ..

# 5. Install the systemd --user units + logrotate (writes to /etc/logrotate.d/, needs sudo once)
make install-svc

# 6. Verify
make status
curl -s http://127.0.0.1:7788/status | jq
curl -s http://127.0.0.1:7789/health | jq

# 7. (Optional) Ingest the FAQ sources into the existing Qdrant collection
make ingest-rules
make ingest-file FILE=orchestrator/data/faq.md
```

> **Survive logout:** `loginctl enable-linger $USER` — without it, the user units stop when you log out.

## Common incidents

Twelve common incidents, each with the runbook command. (Detailed recovery procedures are in the previous section.)

| # | Symptom | Quick check | Fix |
|---|---|---|---|
| 1 | Bridge down | `systemctl --user status skill-chatbot-bridge` | `systemctl --user restart skill-chatbot-bridge` |
| 2 | Orchestrator down | `systemctl --user status skill-chatbot-orchestrator` | `systemctl --user restart skill-chatbot-orchestrator` |
| 3 | Session lost / QR re-auth | `curl -s :7788/status` → `session: "qr_needed"`, `qr_needed_count >= 4` | `make bridge-auth` |
| 4 | Composio 5xx for >2 min | `journalctl --user -u skill-chatbot-bridge --since '2 min ago' | grep -i composio` | Wait + retry; if >10 min, notify Boon (calendar entity drift) |
| 5 | LLM 429 storm | `journalctl --user -u skill-chatbot-orchestrator | grep 429` | Backoff is automatic (1s/2s/4s); if it persists, lower the LLM-side rate limit |
| 6 | Qdrant unreachable | `journalctl --user -u skill-chatbot-orchestrator | grep -i qdrant` | All FAQ queries fail; orchestrator hands off to admins via WA_NOTIFY |
| 7 | Suspicious abusive user | check `wa-bridge/inbox.ndjson` for the sender | `python3 $SKILL/control.py block <phone>` (TODO #15) |
| 8 | Customer asks for human mid-flow | (happen during book_new/edit/cancel) | `python3 $SKILL/control.py takeover <phone>` (TODO) — v1: tell user to call +65… via ADMIN_CONTACT_NUMBER |
| 9 | State DB corruption | `sqlite3 orchestrator/state.sqlite ".schema"` errors | Stop orchestrator → `cp state.sqlite state.sqlite.corrupt.$(date +%s)` → `rm state.sqlite*` → restart |
| 10 | Log disk full | `df -h /var/log/skill-chatbot` | `sudo find /var/log/skill-chatbot -name '*.gz' -mtime +90 -delete` |
| 11 | Phone number changed (WhatsApp) | WhatsApp itself was changed | Stop bridge → `rm -rf wa-bridge/auth_info` → `make bridge-auth` with the new number |
| 12 | Admin off-rotation (WA_NOTIFY empty) | `.env` check | Edit `.env` `WA_NOTIFY=...`, `systemctl --user restart skill-chatbot-orchestrator` |
| 13 | Outbound queue stuck | `wc -l wa-bridge/queue/outbound.jsonl` | Inspect the failing line, `sed -i '$d' wa-bridge/queue/outbound.jsonl` to drop the bad entry, restart bridge |
