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
