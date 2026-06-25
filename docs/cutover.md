# Cutover — rag-qdrant + farm-tour-booking → skill-chatbot (issue #36)

The final step. Gated on issue #33 (smoke test) reporting GO.

**Read this end-to-end before you start.** The whole sequence is ~30 minutes of downtime in a low-traffic window.

## Pre-conditions (verify all before starting)

- [ ] Issue #33 smoke test **GO** verdict in `migration-baseline/smoke-test-report-<ts>.md`
- [ ] Issue #35 baseline captured today (`migration-baseline/<ts>.md` exists in the repo)
- [ ] Issue #34 nightly snapshot succeeded in the last 24h (`make snapshot-now` to force one)
- [ ] wa-bridge is connected to the test WhatsApp number (`journalctl -u skill-chatbot-wa-bridge --since "10 minutes ago" | grep -i auth`)
- [ ] Orchestrator `/health` returns 200
- [ ] baadminbot OpenClaw is running and can reach the orchestrator's admin API (curl test below)
- [ ] `/etc/skill-chatbot.env` is filled on the production box (QDRANT_*, INFERENCE_*, COMPOSIO_*, ADMIN_TELEGRAM_IDS, ADMIN_HTTP_TOKEN, WA_NOTIFY small list, WA_BRIDGE_TOKEN, BOOKING_RULES_PATH)
- [ ] baadminbot's `~/.openclaw/skills/skill-chatbot-admin/.env` is filled with matching `ADMIN_API_BASE`, `ADMIN_HTTP_TOKEN`, `ADMIN_TELEGRAM_IDS`
- [ ] All 7 migration issues (#30–#36) merged to `main`
- [ ] Window: low-traffic (SAAC FARM tour hours + a 30-min buffer)

## Cutover sequence

### T+0  Swap baadminbot's admin skill (1 min)

On baadminbot:

```bash
# Stop OpenClaw
sudo systemctl stop baadminbot          # or however you run it

# Disable rag-qdrant, enable skill-chatbot-admin
mv ~/.openclaw/skills/rag-qdrant ~/.openclaw/skills/rag-qdrant.disabled
ln -s /opt/skill-chatbot/admin-bot ~/.openclaw/skills/skill-chatbot-admin

# Restart OpenClaw — picks up new skill
sudo systemctl start baadminbot
```

### T+2  Smoke: admin TG still works (3 min)

DM baadminbot from your admin Telegram account:

```
/show access
```

Expect: ACL table renders, same shape as rag-qdrant produced.

### T+5  Smoke: admin TG can query bookings (3 min)

```
/bookings 2026-06-25
```

Expect: event list (or "No bookings on …").

### T+8  Announce to operators (1 min)

Send a one-line message in the team channel: **"Cutover live. Both channels up. /bookings on baadminbot, WhatsApp on the test number."**

### T+10  Customer-side validation (10 min)

From a second WA number (not the test number — to keep logs clean):

```bash
# 1. RAG query
send: "hi, what tours do you offer?"
expect: grounded reply within 5s

# 2. New booking
send: "can I book 12 pax this Saturday 14:00?"
follow-up with email
expect: event appears, deposit instructions returned

# 3. Edit
send: "actually make it Sunday 14:00"
expect: event moves to Sunday

# 4. Cancel
send: "we can't make it, cancel"
expect: event gone, friendly confirmation

# 5. Out-of-scope
send: "can I get a refund for last week?"
expect: refusal + escalation to admin phone (ADMIN_CONTACT_NUMBER), no hallucinated facts
```

### T+20  Retire the old skills (5 min)

On baadminbot:

```bash
bash /opt/skill-chatbot/scripts/retire-old-skills.sh
```

What this does (from `scripts/retire-old-skills.sh`):

- Removes `~/.openclaw/skills/rag-qdrant` if still present
- Quarantines any `farm-tour-booking-*` Skill Workshop proposals (`skill_workshop action=quarantine`)

### T+25  Final verify (5 min)

```bash
# Both daemons healthy
sudo systemctl status skill-chatbot-wa-bridge skill-chatbot-orchestrator --no-pager

# Orchestrator health
curl -sf http://127.0.0.1:7789/health

# Admin TG show
# (DM baadminbot: /show access)

# Watch customer-side traffic for 5 min
sudo journalctl -u skill-chatbot-orchestrator -u skill-chatbot-wa-bridge -f
```

### T+30  Done

Post in the team channel: **"Cutover complete. rag-qdrant retired. farm-tour-booking proposals quarantined. Monitoring for +24h."**

## Rollback (<15 min, zero data loss)

**Trigger if** any of:

- WA round-trip broken (customer message → no reply within 30s)
- RAG answers worse than rag-qdrant (hallucinations, missing chunks)
- Booking flow broken (no event created on confirm, edit doesn't move, cancel doesn't delete)
- Admin TG unreachable (baadminbot not responding to commands)

**Steps:**

1. On baadminbot:
   ```bash
   sudo systemctl stop baadminbot
   rm ~/.openclaw/skills/skill-chatbot-admin         # remove symlink
   mv ~/.openclaw/skills/rag-qdrant.disabled ~/.openclaw/skills/rag-qdrant
   sudo systemctl start baadminbot
   ```
2. On the orchestrator box:
   ```bash
   sudo systemctl stop skill-chatbot-wa-bridge skill-chatbot-orchestrator
   ```
3. Send a brief WhatsApp status message from the test number to any active customers: `"Temporarily unavailable. Back shortly."`
4. Restore `booking_rules.yaml` from the pre-cutover copy if it was changed (compare against `migration-baseline/<ts>.md` §1 captured state):
   ```bash
   sudo cp /var/backups/skill-chatbot/booking_rules.yaml.pre-cutover /etc/skill-chatbot/booking_rules.yaml
   sudo systemctl start skill-chatbot-orchestrator skill-chatbot-wa-bridge
   ```

**Data preserved:** Qdrant collection (unchanged — both rag-qdrant and skill-chatbot use the same collection name), Outlook calendar (unchanged — same Composio account), Baileys auth state in `/var/lib/skill-chatbot/wa-bridge/auth/` (unchanged). Rollback is safe to retry multiple times.

## Post-cutover monitoring

### +24h

- Skim `journalctl -u skill-chatbot-orchestrator --since "24 hours ago"` for 500s / unhandled exceptions
- Skim `journalctl -u skill-chatbot-wa-bridge --since "24 hours ago"` for QR prompts (means Baileys session dropped)
- Confirm bookings created today match the rate from the rag-qdrant era (rough sanity)
- Confirm no `401` / `403` in admin API access logs

### +7d

- Prune junk from the Qdrant collection if anything landed bad during migration
- Confirm nightly snapshots continue (`journalctl -u skill-chatbot-qdrant-snapshot --since "7 days ago"`)
- Update `MEMORY.md` / team runbook to reference the new admin-bot install (drop rag-qdrant references)

## Verification commands (one-liners)

```bash
# Health
sudo systemctl status skill-chatbot-{wa-bridge,orchestrator} --no-pager
curl -sf http://127.0.0.1:7789/health

# Admin auth
curl -s -H "X-Admin-Token: $ADMIN_HTTP_TOKEN" \
     -H "X-Admin-Telegram-Id: $ADMIN_TELEGRAM_ID_1" \
     http://127.0.0.1:7789/admin/show

# Bookings for a given day
curl -s -H "X-Admin-Token: $ADMIN_HTTP_TOKEN" \
     -H "X-Admin-Telegram-Id: $ADMIN_TELEGRAM_ID_1" \
     "http://127.0.0.1:7789/admin/bookings?date=$(date +%Y-%m-%d)"

# Skill state on baadminbot
ls ~/.openclaw/skills/
sudo systemctl status baadminbot
```

## Related

- Issue #33 — smoke test plan (gate to this runbook)
- Issue #34 — nightly Qdrant snapshot (keeps rollback data fresh)
- Issue #35 — pre-cutover baseline (the "before" snapshot)
- Issue #29 — migration epic (track all 7 issues from here)
