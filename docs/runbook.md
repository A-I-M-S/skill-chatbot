# Runbook

Copy-pasteable commands for the 12 most common incidents. Pair with `docs/ops.md` (architecture, systemd, recovery procedures) and `docs/security.md` (data handling, secret rotation).

## 0. Sanity checks (run first, every time)

```bash
# Are both daemons up?
systemctl --user status skill-chatbot-bridge skill-chatbot-orchestrator

# Bridge health + queue depth
curl -s http://127.0.0.1:7788/status | jq

# Orchestrator health + last processed message
curl -s http://127.0.0.1:7789/health | jq

# Tail the latest logs
journalctl --user -u skill-chatbot-bridge -u skill-chatbot-orchestrator -n 200 --no-pager

# Last 50 inbound NDJSON lines
tail -50 wa-bridge/inbox.ndjson | jq

# Last 50 outbound queue lines (anything stuck here didn't deliver)
wc -l wa-bridge/queue/outbound.jsonl 2>/dev/null
tail -5 wa-bridge/queue/outbound.jsonl 2>/dev/null | jq
```

## 1. Bridge down

```bash
systemctl --user restart skill-chatbot-bridge
journalctl --user -u skill-chatbot-bridge -n 100 --no-pager
```

If it crashes immediately, check the auth state:

```bash
ls -la wa-bridge/auth_info/   # should have creds.json + a folder
# Missing creds → relink:
systemctl --user stop skill-chatbot-bridge
cd wa-bridge && npm run auth
systemctl --user start skill-chatbot-bridge
```

## 2. Orchestrator down

```bash
systemctl --user restart skill-chatbot-orchestrator
journalctl --user -u skill-chatbot-orchestrator -n 100 --no-pager
```

If the lock file is stuck (StateLockedError):

```bash
# Confirm the lock
ls -la orchestrator/state.sqlite.lock
# Another orchestrator process holding it?
lsof orchestrator/state.sqlite.lock
# If nothing's holding it, remove the lock
sudo rm orchestrator/state.sqlite.lock
systemctl --user start skill-chatbot-orchestrator
```

## 3. Session lost / QR re-auth

The bridge auto-reconnects (1s → 60s backoff). After 4 QR cycles it logs CRITICAL and stops trying.

```bash
curl -s http://127.0.0.1:7788/status | jq   # confirm session + qr_needed_count
journalctl --user -u skill-chatbot-bridge | grep -i "qr\|relink\|give up" | tail -20
```

To relink (pairing code, no QR):

```bash
sudo systemctl stop skill-chatbot-wa-bridge
cd /opt/skill-chatbot/wa-bridge && sudo -E npm run auth:code   # uses WA_PAIR_NUMBER, or pass -- +65…
# phone: Linked Devices → Link a Device → "Link with phone number instead" → enter the code
sudo systemctl start skill-chatbot-wa-bridge
```

## 4. Composio 5xx for >2 min

```bash
journalctl --user -u skill-chatbot-bridge -u skill-chatbot-orchestrator --since '2 min ago' | grep -i composio | tail -20
```

If it's a transient blip, wait. If >10 min, the booking calendar entity may be misconfigured — check Composio dashboard and re-link.

## 5. LLM 429 storm

```bash
journalctl --user -u skill-chatbot-orchestrator --since '5 min ago' | grep -c "429"
```

Backoff is automatic (1s → 2s → 4s). If it persists, the rate-limit error is at the inference provider's side — wait 5–10 minutes, or temporarily lower the model temperature / reduce parallel inbox processing.

## 6. Qdrant unreachable

```bash
journalctl --user -u skill-chatbot-orchestrator | grep -i qdrant | tail -20
```

When Qdrant is down, every FAQ call fails. The orchestrator falls back to `handoff(reason=other)` for every question, which means admins get pinged for every message. If this lasts more than 15 min, pause the orchestrator:

```bash
systemctl --user stop skill-chatbot-orchestrator
```

Customers will see the bridge queue outbound messages until you restart.

## 7. Suspicious abusive user

The handoff flow tags abusive messages with `reason=abuse` and DM's admins.

```bash
# Find recent abuse handoffs in the orchestrator log
journalctl --user -u skill-chatbot-orchestrator --since '1 hour ago' | grep -i "reason.*abuse" | tail -20

# Block a number (TODO: #15 will add a blocklist helper; for now edit WA_BRIDGE_TOKEN if needed)
# v1 fallback: the operator calls the abuser directly via WhatsApp and asks them to stop,
# or blocks via the WhatsApp app's "Block" feature.
```

## 8. Customer asks for human mid-flow

The router handles this when it detects `handoff(reason=...)`. If a customer asks "can I talk to a person?" mid-booking, the router should call `handoff(reason=other)` and tell them to call `ADMIN_CONTACT_NUMBER`.

If it's not happening, check the inbox:

```bash
tail -50 wa-bridge/inbox.ndjson | jq 'select(.text | test("human|person|call|talk"; "i"))'
```

## 9. State DB corruption

```bash
sqlite3 orchestrator/state.sqlite ".schema"
# If you see "database disk image is malformed":
systemctl --user stop skill-chatbot-orchestrator
cp orchestrator/state.sqlite orchestrator/state.sqlite.corrupt.$(date +%s)
rm orchestrator/state.sqlite orchestrator/state.sqlite.offset
# (state_log is lost; bridge queue is intact so undelivered messages still go out)
systemctl --user start skill-chatbot-orchestrator
```

To replay from scratch (rare):

```bash
# Replay the inbox — note this RE-PROCESSES every inbound message,
# which may double-fire handoff notifications to admins.
mv wa-bridge/inbox.ndjson wa-bridge/inbox.ndjson.$(date +%s).bak
# Then read the original into a fresh inbox if you actually want replay.
```

## 10. Log disk full

```bash
df -h /var/log/skill-chatbot
sudo find /var/log/skill-chatbot -name '*.gz' -mtime +90 -delete
sudo logrotate -f /etc/logrotate.d/skill-chatbot
```

## 11. Phone number changed (WhatsApp)

If the WhatsApp number itself was migrated (e.g. SIM swap), the old auth state is dead.

```bash
sudo systemctl stop skill-chatbot-wa-bridge
rm -rf "$WA_AUTH_DIR"                                           # /var/lib/skill-chatbot/wa-bridge/auth
# update WA_PAIR_NUMBER in /etc/skill-chatbot.env to the new number
cd /opt/skill-chatbot/wa-bridge && sudo -E npm run auth:code    # pairing code for the new phone
sudo systemctl start skill-chatbot-wa-bridge
```

If the actual *business phone* changed (i.e. customers should reach a new number), update `.env` `WA_BRIDGE_URL` doesn't change but you need to: edit any docs/README/SKILL.md mentioning the old number, and re-run `make ingest-rules` if the FAQ answers reference it.

## 12. Admin off-rotation (WA_NOTIFY empty)

```bash
# Edit .env to add the new admin's phone number(s)
vim /root/.openclaw/workspace/dev/projects/skill-chatbot/.env
# (set WA_NOTIFY=+65xx1,+65xx2)
chmod 600 /root/.openclaw/workspace/dev/projects/skill-chatbot/.env
systemctl --user restart skill-chatbot-orchestrator
```

## 13. Outbound queue stuck

```bash
wc -l wa-bridge/queue/outbound.jsonl
tail -3 wa-bridge/queue/outbound.jsonl | jq
```

If the last line is malformed JSON:

```bash
# Drop just the bad line (preserves the rest of the queue)
head -n -1 wa-bridge/queue/outbound.jsonl > wa-bridge/queue/outbound.jsonl.tmp
mv wa-bridge/queue/outbound.jsonl.tmp wa-bridge/queue/outbound.jsonl
systemctl --user restart skill-chatbot-bridge
```

If the bridge is healthy but the queue is growing, check `/status`:

```bash
curl -s http://127.0.0.1:7788/status | jq
# queued_send is the depth — should drop to 0 within a minute if Baileys is connected
```