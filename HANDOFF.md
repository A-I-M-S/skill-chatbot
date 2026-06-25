# Handoff — installing `skill-chatbot` from scratch

You are reading this because someone asked you (human or AI agent) to stand up the `skill-chatbot` stack from this repo. This is the **single starting document** — read it end to end, then follow the sequence in **§4**.

## 1. State of the repo (as of `b0169e2` on `main`)

All 7 migration issues are closed. Code, tests, docs, and the deploy installer are merged. The `rag-qdrant` skill on baadminbot and the `farm-tour-booking` Skill Workshop proposals are the **only** legacy artifacts still in place — `scripts/retire-old-skills.sh` removes them.

The system replaces two old skills with one stack:

- **Telegram (admin only)** — `admin-bot/` skill on baadminbot. Symlink + `.env` + restart.
- **WhatsApp (customers only)** — `wa-bridge` (Baileys, normal WhatsApp number). Runs under systemd.

Two system daemons (`skill-chatbot-wa-bridge.service`, `skill-chatbot-orchestrator.service`) + one nightly snapshot timer. No Docker. Hardened systemd (`ProtectSystem=strict`, dedicated system user, `EnvironmentFile=/etc/skill-chatbot.env`).

## 2. What you need before you start

Gather these before running anything — the installer will refuse to start if any are missing.

| What | Where it goes | Notes |
|---|---|---|
| `WA_NOTIFY` (E.164 small list) | `/etc/skill-chatbot.env` | Comma-separated, e.g. `+60123456789,+60198765432`. Default: admin + 1 backup. |
| `QDRANT_URL` + `QDRANT_API_KEY` | `/etc/skill-chatbot.env` | Existing Qdrant (probably shared with the current `rag-qdrant` skill). Collection name **must match** the existing one or ingestion will be a no-op. |
| `INFERENCE_BASE_URL` + `INFERENCE_API_KEY` + `INFERENCE_MODEL` | `/etc/skill-chatbot.env` | MiniMax endpoint. Same model the OpenClaw main session uses (`MiniMax-M3`). |
| `COMPOSIO_API_KEY` + `COMPOSIO_CONNECTED_ACCOUNT_ID` | `/etc/skill-chatbot.env` | SAAC Tour Outlook account (Composio-managed). |
| `ADMIN_TELEGRAM_IDS` | `/etc/skill-chatbot.env` AND `admin-bot/.env` | Comma-separated ints. Must match both sides. |
| `ADMIN_HTTP_TOKEN` | `/etc/skill-chatbot.env` AND `admin-bot/.env` | `openssl rand -hex 32`. Must match both sides. |
| `WA_BRIDGE_TOKEN` | `/etc/skill-chatbot.env` | `openssl rand -hex 32`. Used by `admin-bot` to call orchestrator-side admin endpoints if needed. |
| `BOOKING_RULES_PATH` | `/etc/skill-chatbot.env` | Absolute path to the ruamel-managed yaml. Default: `/etc/skill-chatbot/booking_rules.yaml`. |
| `RAG_PHOTOS_DIR` | `/etc/skill-chatbot.env` | Where ingested photos land. Pre-create the dir, owned by `skill-chatbot:skill-chatbot`. |
| WhatsApp test number | n/a | Already paired from issue #952. The wa-bridge auth state is what makes the WhatsApp side work. The same number is **prod** for v1. |

If you are an AI agent and any of these are missing, **stop and ask** — do not invent values.

## 3. Two boxes

The stack spans two hosts. Get them straight before you start.

### Box A — orchestrator + wa-bridge (production server)

- Debian 12+ / Ubuntu 22.04+
- `apt install python3 python3-venv python3-pip nodejs npm jq rsync` once
- `sudo` access required by `scripts/install-systemd-system.sh`
- Network: outbound HTTPS to Qdrant, MiniMax endpoint, Composio backend, Telegram API; inbound from baadminbot to port 7789 (orchestrator admin API) and 8080 (wa-bridge)
- This box runs `skill-chatbot-wa-bridge` + `skill-chatbot-orchestrator` + the nightly snapshot timer

### Box B — baadminbot (existing Telegram admin box)

- Runs the OpenClaw instance admins use today
- Already has `~/.openclaw/skills/rag-qdrant` installed
- The cutover swaps that for `~/.openclaw/skills/skill-chatbot-admin` (symlink into `admin-bot/`)
- Restart OpenClaw after the swap

If you only have SSH to Box A and Box B is a different physical/virtual host, you need SSH to both. If they're the same box, even simpler.

## 4. Sequence

Follow in order. Don't skip steps.

1. **Capture baseline** on Box A:

   ```bash
   git clone https://github.com/A-I-M-S/skill-chatbot /tmp/skill-chatbot
   cd /tmp/skill-chatbot
   make baseline-capture      # writes migration-baseline/<ts>.md
   git add migration-baseline && git commit -m "baseline: pre-cutover <date>"
   ```

   Records Qdrant state, env keys, OS version, etc. The cutover can use this for diffing.

2. **Install the stack** on Box A:

   ```bash
   sudo bash /tmp/skill-chatbot/scripts/install-systemd-system.sh
   ```

   Idempotent. Creates `skill-chatbot` system user, lays out `/opt/skill-chatbot`, creates the Python venv, builds wa-bridge, writes `/etc/skill-chatbot.env` from the template, installs + enables the units + timer. **Refuses to start the units until you fill the env** (by design).

3. **Fill the env** on Box A:

   ```bash
   sudo -e /etc/skill-chatbot.env
   # fill all the keys from §2
   sudo bash /tmp/skill-chatbot/scripts/install-systemd-system.sh   # 2nd run, picks up env
   ```

4. **Pair WhatsApp** on Box A (one-time):

   ```bash
   sudo journalctl -u skill-chatbot-wa-bridge -f
   # scan the QR from the WhatsApp app on the test number
   ```

   The session persists at `/var/lib/skill-chatbot/wa-bridge/auth/`. It survives restarts.

5. **Smoke-test** against the test WhatsApp number — see [`docs/smoke-test.md`](docs/smoke-test.md) for the 14 cases. Send from a second WA number (not the test number). 7 customer-side cases + 7 admin-side cases. Fill in the per-case PASS/FAIL table in the report template.

   - **GO** → proceed to step 6.
   - **NO-GO** → open a new issue per failing case, do not cutover, fix and re-test.

6. **Cut over** — see [`docs/cutover.md`](docs/cutover.md). Minute-by-minute T+0 → T+30:

   - T+0  swap baadminbot's admin skill (rag-qdrant → admin-bot)
   - T+2  admin TG smoke (`/show access`)
   - T+5  admin TG smoke (`/bookings <date>`)
   - T+8  announce to operators
   - T+10 customer-side validation (RAG, booking, edit, cancel, OOS escalation)
   - T+20 `scripts/retire-old-skills.sh` on baadminbot
   - T+25 final verify
   - T+30 done

7. **Install the admin-bot skill on baadminbot** (Box B) — only if it wasn't done as part of the cutover swap:

   ```bash
   ln -s /opt/skill-chatbot/admin-bot ~/.openclaw/skills/skill-chatbot-admin
   cp ~/.openclaw/skills/skill-chatbot-admin/.env.example ~/.openclaw/skills/skill-chatbot-admin/.env
   # fill .env — must match ADMIN_HTTP_TOKEN and ADMIN_TELEGRAM_IDS from Box A
   # restart baadminbot's OpenClaw
   ```

## 5. Manual gates (these require human action)

The installer and tests are scripted. These are the points that need a human:

1. **QR scan** on first wa-bridge start (one-time, in the WhatsApp app on the test phone)
2. **Env file values** — every secret in `/etc/skill-chatbot.env` and `admin-bot/.env` must be hand-entered (the installer only writes the template)
3. **Skill swap on baadminbot** — the cutover step that physically moves `~/.openclaw/skills/rag-qdrant` aside and installs `admin-bot` in its place
4. **WA_NOTIFY numbers** — must come from you (the operator); the system can't infer which admin numbers you want alerted

If you are an AI agent, **stop and ask** at each of these.

## 6. Quick verify (post-install)

```bash
# Both daemons healthy
sudo systemctl status skill-chatbot-wa-bridge skill-chatbot-orchestrator --no-pager

# Orchestrator health
curl -sf http://127.0.0.1:7789/health

# Admin API round-trip (replace $TOKEN / $TID)
curl -s -H "X-Admin-Token: $TOKEN" \
     -H "X-Admin-Telegram-Id: $TID" \
     http://127.0.0.1:7789/admin/show

# Bookings for today
curl -s -H "X-Admin-Token: $TOKEN" \
     -H "X-Admin-Telegram-Id: $TID" \
     "http://127.0.0.1:7789/admin/bookings?date=$(date +%Y-%m-%d)"

# Nightly snapshot present
sudo systemctl list-timers skill-chatbot-qdrant-snapshot.timer

# baadminbot sees the new skill
ls ~/.openclaw/skills/
```

## 7. Rollback

`<15 min`, zero data loss. Steps in [`docs/cutover.md`](docs/cutover.md) § "Rollback". The TL;DR:

1. On baadminbot: stop OpenClaw, restore `~/.openclaw/skills/rag-qdrant` from the `.disabled` backup, remove `skill-chatbot-admin` symlink, restart.
2. On Box A: `sudo systemctl stop skill-chatbot-{wa-bridge,orchestrator}`.
3. Send a brief WhatsApp status message to active customers.
4. Restore `booking_rules.yaml` from the pre-cutover backup if it changed.
5. Restart both daemons.

Data preserved: Qdrant collection name unchanged, Composio account unchanged, Baileys auth state in `/var/lib/skill-chatbot/wa-bridge/auth/` unchanged.

## 8. Cross-references

- [`docs/cutover.md`](docs/cutover.md) — minute-by-minute cutover + rollback
- [`docs/smoke-test.md`](docs/smoke-test.md) — 14-case gate
- [`docs/ops.md`](docs/ops.md) — re-auth, restore from snapshot, ongoing ops
- [`admin-bot/SKILL.md`](admin-bot/SKILL.md) — baadminbot-side install + commands
- Migration epic — issue #29 on `A-I-M-S/skill-chatbot`
- `scripts/install-systemd-system.sh` — idempotent installer (source of truth for steps 2 + 3)
- `scripts/capture-baseline.sh` — pre-cutover state recorder (step 1)
- `scripts/retire-old-skills.sh` — idempotent cleanup (cutover T+20)
