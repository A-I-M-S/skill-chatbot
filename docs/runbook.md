# Runbook

> Operator playbook for the `skill-chatbot` stack (wa-bridge + orchestrator).
> Read [architecture.md](architecture.md) first for topology, and
> [ops.md](ops.md) for day-to-day systemd commands. Security context lives
> in [security.md](security.md).
>
> **Conventions**
> - All commands assume the run-as user decided in the bootstrap plan §6 Q11
>   (default: `root` with `loginctl enable-linger`).
> - `REPO` = `/root/.openclaw/workspace/dev/projects/skill-chatbot`.
> - `LOG_DIR` = `/var/log/skill-chatbot`.
> - All destructive commands print a one-line `WHY:` so the next operator
>   knows what they're undoing.

## 0. Always run this first

```bash
REPO=/root/.openclaw/workspace/dev/projects/skill-chatbot
cd "$REPO"
git log --oneline -5            # confirm you're on the right code
make status                     # both daemons + last 10 lines each
make logs | tail -n 100         # tail both journals
```

If `make status` is green and `make logs` shows nothing alarming, the
incident is probably customer-side (WhatsApp message not delivered,
wrong number, abusive message blocked by Meta). Continue to the matching
section below.

---

## 1. Bridge down

**Symptoms**: `/health` on the bridge returns connection refused or 5xx;
inbound messages pile up in the NDJSON inbox but aren't replied to;
`/status` reports `connected: false`.

```bash
systemctl --user status skill-chatbot-bridge --no-pager
journalctl --user -u skill-chatbot-bridge -n 200 --no-pager

# Most common cause: Baileys socket dropped but the Node process is alive.
# Restart it; it will reconnect automatically.
systemctl --user restart skill-chatbot-bridge

# If restart does not bring it back, check the auth session (see §3).
```

**WHY:** The bridge is a single Node process owning the Baileys socket.
A restart is non-destructive — the inbox NDJSON file is durable, and the
orchestrator will catch up on the next tick.

---

## 2. Orchestrator down

**Symptoms**: bridge is sending successfully (check `bridge.log` for
`POST /send 200`) but no replies ever reach the customer; no
`processed message_id=…` lines in `orchestrator.log`; `/health` on the
orchestrator port returns nothing.

```bash
systemctl --user status skill-chatbot-orchestrator --no-pager
journalctl --user -u skill-chatbot-orchestrator -n 200 --no-pager

# Restart and watch it re-tail inbox.ndjson from its last offset.
systemctl --user restart skill-chatbot-orchestrator

# Verify the tailer caught up.
tail -f "$REPO/wa-bridge/inbox.ndjson"
# You should see the orchestrator log "tail offset advanced" every few seconds.
```

**WHY:** The orchestrator persists its NDJSON tail offset in
`state.sqlite`. A restart resumes from the last acknowledged line, so
no inbound message is dropped or double-processed.

---

## 3. Session lost / QR re-auth

**Symptoms**: bridge log shows `qr` events, `disconnect: loggedOut`,
`Stream Errored (conflict)`, or `/status` reports `registered: false`.

```bash
# 1. Stop the bridge so the QR CLI is not racing the live socket.
systemctl --user stop skill-chatbot-bridge

# 2. Wipe the auth state (this is the credential) and re-link.
rm -rf "$REPO/wa-bridge/auth_info"
cd "$REPO/wa-bridge"
npm run auth    # prints a QR to stdout

# 3. From your phone: WhatsApp → Linked Devices → Link a Device.
# 4. Once the CLI says "registered", restart the bridge.
systemctl --user start skill-chatbot-bridge
systemctl --user status skill-chatbot-bridge --no-pager
```

**Hard rule** (from bootstrap plan §5.1): **only the bridge has the
number linked.** No team member should have WhatsApp Web open on this
number, or Baileys will refuse the new session. If you see "conflict"
on a fresh auth, ask around.

**WHY:** `auth_info/` IS the WhatsApp credential. Wipe + re-link is the
only recovery; the inbox NDJSON survives the wipe.

---

## 4. Composio 5xx for > 2 minutes

**Symptoms**: orchestrator log shows `ComposioError 5xx` repeatedly on
booking flows; `notify.py` reports `composio_outlook failed`; admin
stops receiving `notify_new_booking` alerts.

```bash
# 1. Confirm the upstream is actually down (not just our connection).
curl -fsS https://backend.composio.dev/health || echo "Composio down"

# 2. Check the connected account we use for the SAAC calendar.
#    The error message names it; e.g. "connected_account_id=ca_*** missing entity_id".
journalctl --user -u skill-chatbot-orchestrator -n 200 --no-pager | grep -i composio

# 3. If it's a real outage: switch new bookings into a "deferred" mode
#    by raising the LLM's handoff threshold via the env override.
systemctl --user edit skill-chatbot-orchestrator
# Add under [Service]:
#   Environment="COMPOSIO_DEGRADED=1"
systemctl --user daemon-reload
systemctl --user restart skill-chatbot-orchestrator

# 4. Revert when Composio is back.
systemctl --user revert skill-chatbot-orchestrator
systemctl --user daemon-reload
systemctl --user restart skill-chatbot-orchestrator
```

**WHY:** The orchestrator is the only consumer of Composio for this
stack; throttling the LLM to handoff avoids burning the deposit-email
step in a half-committed state.

---

## 5. LLM 429 storm

**Symptoms**: orchestrator log full of `openai.RateLimitError`,
`HTTP 429`, `backing off 30s`; replies stop; customers see no
acknowledgement.

```bash
# 1. Confirm it's a real 429, not a transient burst.
grep -c '429' "$LOG_DIR/orchestrator.log" | tail -n 5

# 2. Back the orchestrator off (single-process, queueing tail loop
#    will naturally rate-limit itself; give it room).
systemctl --user stop skill-chatbot-orchestrator
sleep 60
systemctl --user start skill-chatbot-orchestrator

# 3. If the burst is sustained, drop the per-message concurrency to 1
#    (it already is, by design — see bootstrap plan §6 Q10) and lower
#    the LLM temperature via env.
systemctl --user edit skill-chatbot-orchestrator
#   Environment="INFERENCE_TEMPERATURE=0.2"
#   Environment="LLM_MAX_INFLIGHT=1"
systemctl --user daemon-reload
systemctl --user restart skill-chatbot-orchestrator

# 4. Watch the rate-limit error rate drop.
journalctl --user -u skill-chatbot-orchestrator -f | grep -E '429|RateLimit'
```

**WHY:** A 429 storm is almost always an upstream tier-limit on the
inference endpoint. The orchestrator's `inference.py` retries with
exponential backoff (brief §88), so the right move is to *stop
hammering* and let the bucket refill.

---

## 6. Qdrant unreachable

**Symptoms**: FAQ replies degrade to "I don't know, let me pass you to
the team"; orchestrator log shows `httpx.ConnectError` or
`QdrantClientException`; `rag_qdrant.ask()` returns `None`.

```bash
# 1. Is Qdrant up?
curl -fsS "${QDRANT_URL}/healthz" || echo "Qdrant down"
systemctl --user status qdrant --no-pager 2>/dev/null || \
  systemctl status qdrant --no-pager

# 2. If Qdrant is on the same host: restart it.
sudo systemctl restart qdrant

# 3. The orchestrator will recover automatically; confirm by sending
#    yourself a FAQ question.
journalctl --user -u skill-chatbot-orchestrator -f
# You should see "rag_qdrant ok" lines resume.
```

**WHY:** The orchestrator imports `rag_qdrant` directly (architecture
§Caching). When Qdrant is down, `ask()` returns `None` and the
router falls through to `handoff(reason=rag_unavailable)`. No data is
corrupted; this is purely a degraded-mode.

---

## 7. Suspicious abusive user

**Symptoms**: a single phone number produces `reason=abuse` handoffs
repeatedly, or the messages contain threats / off-topic content; admins
want to block or de-prioritise the user.

```bash
# 1. Look at the history. state.sqlite holds the last 8 turns.
sqlite3 "$REPO/orchestrator/state.sqlite" \
  "SELECT phone, flow, datetime(last_message_at), language
     FROM phone_state WHERE phone LIKE '%<last 7 digits>%' ORDER BY last_message_at DESC;"

# 2. Pause the user (orchestrator will keep state but stop sending
#    non-FAQ replies; admins can still see the messages).
sqlite3 "$REPO/orchestrator/state.sqlite" \
  "UPDATE phone_state SET flow='idle', draft=NULL WHERE phone='<e164 phone>';"

# 3. If you want a hard block at the bridge layer, add the JID to
#    wa-bridge's block list (file lives in auth_info, consult
#    bridge.log for the current path).
echo '<e164>@c.us' >> "$REPO/wa-bridge/auth_info/blocked.txt"

# 4. Re-enable when the threat has passed by reversing step 2 with
#    flow restored from the state_log table.
sqlite3 "$REPO/orchestrator/state.sqlite" \
  "SELECT * FROM state_log WHERE phone='<e164 phone>' ORDER BY ts DESC LIMIT 1;"
```

**WHY:** Abuse is a real product signal — `docs/security.md` covers
what's logged. Pausing (step 2) is reversible; a hard block (step 3)
isn't. Always try the soft path first.

---

## 8. Customer asks for human mid-flow

**Symptoms**: mid-booking or mid-edit, the customer writes "agent" /
"human" / "人" / "客服" or otherwise signals they want a person; the
bot's normal flow has no escape hatch.

```bash
# This is handled by the router automatically — it routes any
# "talk to a real person" message to handoff(reason=customer_request).
# To confirm, look for the admin WA message:
tail -n 20 "$LOG_DIR/bridge.log" | grep -E 'WA_NOTIFY|sent'

# If the admin on rotation did NOT receive the message (see §11),
# re-send manually with the same shape the orchestrator would have:
curl -fsS -H "Authorization: Bearer $WA_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to":"<admin phone e164>","text":"[handoff] phone=<caller phone>\nreason=customer_request\nmsg=<original message>"}' \
  "$WA_BRIDGE_URL/send"
```

**WHY:** Customer-request handoffs are the one path where a missed
notification is worse than a duplicate. The runbook's re-send is
idempotent on the human side (admins will dedupe visually).

---

## 9. State DB corruption

**Symptoms**: orchestrator fails to boot with `sqlite3.DatabaseError:
database disk image is malformed`; `state.sqlite` is zero bytes; smoke
test crashes.

```bash
# 1. Stop the orchestrator (writes are WAL'd; we want a clean copy).
systemctl --user stop skill-chatbot-orchestrator

# 2. Back up the broken DB before anything else.
cp "$REPO/orchestrator/state.sqlite" \
   "$REPO/orchestrator/state.sqlite.corrupt.$(date +%Y%m%d-%H%M%S)"

# 3. Try sqlite3's built-in recovery.
sqlite3 "$REPO/orchestrator/state.sqlite" ".recover" \
  | sqlite3 "$REPO/orchestrator/state.sqlite.recovered"
mv "$REPO/orchestrator/state.sqlite.recovered" \
   "$REPO/orchestrator/state.sqlite"
chmod 600 "$REPO/orchestrator/state.sqlite"

# 4. If .recover fails, restore from the most recent daily backup
#    (see ops.md §Backups), accepting the loss of in-flight state.
ls -lt /var/backups/skill-chatbot/state.sqlite.* 2>/dev/null | head -1
# cp <latest>  $REPO/orchestrator/state.sqlite
# chmod 600    $REPO/orchestrator/state.sqlite

# 5. Restart and watch the tailer pick up the inbox offset.
systemctl --user start skill-chatbot-orchestrator
journalctl --user -u skill-chatbot-orchestrator -n 100 --no-pager
```

**WHY:** `state.sqlite` is the only durable conversation state; loss
means every active customer gets a "fresh start" greeting on the next
message. The `state_log` table (insert-only) is the audit trail —
even if the live `phone_state` is lost, the log explains what
happened.

---

## 10. Log disk full

**Symptoms**: orchestrator/bridge writes stop with `OSError: [Errno 28]
No space left on device`; logrotate complains in cron; `/var/log` is
100%.

```bash
# 1. Confirm.
df -h /var/log
du -sh /var/log/skill-chatbot/* | sort -h | tail -10

# 2. Force a logrotate run (the weekly cron is the normal trigger;
#    do not bypass it without understanding why the disk filled up).
sudo logrotate -f /etc/logrotate.d/skill-chatbot

# 3. If still full, archive the oldest month and ship off-host.
sudo find /var/log/skill-chatbot -name '*.gz' -mtime +90 -delete

# 4. Verify both daemons resumed writing.
ls -la "$LOG_DIR/"
journalctl --user -u skill-chatbot-bridge -n 20 --no-pager
```

**WHY:** Logs are rotated weekly with `copytruncate` (bootstrap plan
§2). Forced rotation is safe because the daemons reopen by path, not
by fd. Never `rm` an actively-written log — use `logrotate -f` or
`truncate -s 0`.

---

## 11. Phone number changed

**Symptoms**: the WhatsApp number the bot is linked to has been
reassigned (SIM swap, new device, port-out) and Baileys can no longer
send/receive; or the team wants to migrate to a new number entirely.

```bash
# 1. For a SIM-side reactivation on the same number: re-auth (see §3).
# 2. For a permanent number change: this is a new bridge install.
systemctl --user stop skill-chatbot-bridge skill-chatbot-orchestrator
rm -rf "$REPO/wa-bridge/auth_info"
mv "$REPO/wa-bridge/inbox.ndjson" \
   "$REPO/wa-bridge/inbox.ndjson.<oldnumber>.$(date +%Y%m%d)"

# 3. Update .env to the new ADMIN_CONTACT_NUMBER / WA_NOTIFY.
$EDITOR "$REPO/.env"
chmod 600 "$REPO/.env"

# 4. Re-link the new number (see §3) and start fresh.
cd "$REPO/wa-bridge" && npm run auth
systemctl --user start skill-chatbot-bridge skill-chatbot-orchestrator
```

**Notify customers** before doing this — every customer currently
mid-flow will see the bot go silent. The handoff message in
[message-flows.md](message-flows.md) §8 covers "human in the loop"
fallback; route new handoffs to a live phone number for the duration
of the cutover.

**WHY:** The bridge's phone number IS the identity. There is no
soft-migration path; it's a credential swap, not a config change.

---

## 12. Admin off-rotation (WA_NOTIFY empty)

**Symptoms**: the `WA_NOTIFY` env var is unset or contains no valid
numbers; handoffs are firing (per orchestrator log) but no admin
receives anything; bridge log shows `WA_NOTIFY empty, skipping` or
`all recipients filtered out`.

```bash
# 1. Confirm WA_NOTIFY.
grep '^WA_NOTIFY=' "$REPO/.env"

# 2. Set it (comma-separated E.164, no spaces around commas).
$EDITOR "$REPO/.env"
# WA_NOTIFY=+65XXXXXXXX,+65YYYYYYYY
chmod 600 "$REPO/.env"

# 3. Restart the orchestrator so the new list is picked up.
systemctl --user restart skill-chatbot-orchestrator

# 4. Trigger a dry-run handoff to confirm delivery.
curl -fsS -H "Authorization: Bearer $WA_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to":"+65XXXXXXXX","text":"[test handoff] orchestrator alive"}' \
  "$WA_BRIDGE_URL/send"
tail -n 5 "$LOG_DIR/bridge.log"
```

**WHY:** `WA_NOTIFY` is the only escalation channel. An empty value
silently swallows every `handoff()` call. The bootstrap plan §6 Q11
assumes the run-as user can edit `.env`; if not, escalate to whoever
holds the secret store.

---

## 13. Outbound queue stuck

**Symptoms**: orchestrator has decided a reply (per
`state.phone_state.last_message_at` advancing) but the customer never
receives it; `wa-bridge/queue/outbound.jsonl` grows without being
drained; bridge log shows `sender: idle` for >30s.

```bash
# 1. Inspect the queue.
wc -l "$REPO/wa-bridge/queue/outbound.jsonl"
tail -n 5 "$REPO/wa-bridge/queue/outbound.jsonl"

# 2. If lines exist but the sender isn't draining, the bridge is wedged.
#    A restart is safe: the sender re-opens the file and processes
#    pending lines.
systemctl --user restart skill-chatbot-bridge
journalctl --user -u skill-chatbot-bridge -n 100 --no-pager

# 3. If the queue is empty and the customer STILL hasn't received
#    anything, the issue is on the WA side — check §3 (session) and
#    §1 (bridge down).
```

**WHY:** The outbound queue is a JSONL file, not Redis. Restarts are
non-destructive; pending lines are picked up on the next send loop.
See bootstrap plan §5.10 for the NDJSON tail race mitigation.

---

## 14. Booking horizon staleness

**Not an incident — a known limitation.** Edit/cancel lookups only see
events within `BOOKING_HORIZON_DAYS` (default 90). Tours beyond that
window look "not found" to the bot. If a customer asks about an old
booking, route to handoff.

```bash
# Check the current horizon.
grep '^BOOKING_HORIZON_DAYS=' "$REPO/.env"

# Bumping it past 180 is safe but the LIST_EVENTS response will balloon;
# Composio warns at ≥1000 events (see bootstrap plan §5.4). Keep ≤180.
```

---

## See also

- [architecture.md](architecture.md) — topology, state model, guardrails
- [ops.md](ops.md) — systemd units, backups, smoke test
- [security.md](security.md) — what's logged, who can read state.sqlite, secret rotation
- [message-flows.md](message-flows.md) — sample dialogues for each handoff reason
- bootstrap plan §5 — risks this runbook mitigates
