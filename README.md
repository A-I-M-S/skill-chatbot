# skill-chatbot

> WhatsApp chatbot for **SAAC FARM** tour — answers FAQs via Qdrant RAG, takes tour bookings via Outlook calendar, escalates the rest to a human.

## What it does

| Function | Backing skill | What the user gets |
|---|---|---|
| **FAQ** | [`rag-qdrant`](https://github.com/A-I-M-S/skill-chatbot) (Qdrant + FastEmbed E5 multilingual) | Grounded answer from the knowledge base. EN / 中文 |
| **Booking — new / edit / cancel** | `farm-tour-booking` (Composio Outlook, `outlook_24E83AD2E7F5F77D@outlook.com`) | Multi-turn flow, draft → user confirms "YES" → commits |
| **Handoff** | `WA_NOTIFY` env + `ADMIN_CONTACT_NUMBER` env | Out-of-scope (refund, complaint, custom pricing) → notify admins, tell customer to call |

All outbound is in **replies only** (within the 24h customer-service window) — no Meta-approved message templates required.

## Architecture

```
       customers ─┐                                    ┌─ admins
                  ▼                                    ▼
            ┌────────────────┐                  ┌────────────┐
            │  wa-bridge     │  HTTP / NDJSON   │  wa-bridge │
            │  (Node,        │ ───────────────▶ │  (same)    │
            │   Baileys)     │                  └────────────┘
            └────────────────┘
                    ▲
                    │ replies
            ┌───────┴──────────┐
            │  orchestrator    │  Python, LLM router (tool-calling)
            │  - state (SQLite)│  per-phone conversation memory
            │  - i18n EN/中文   │  dedupe by message_id
            └────┬──────────┬──┘
                 │          │
        ┌────────▼─┐   ┌────▼─────────────┐
        │ rag-     │   │ farm-tour-       │──▶ Composio ▶ Outlook
        │ qdrant   │   │ booking          │     (SAAC Tour calendar)
        │ (.ask)   │   │ (booking_flow.py)│
        └──────────┘   └──────────────────┘
```

Two daemons on the same box as the existing skills, talking over localhost.

| Process | Language | Job |
|---|---|---|
| `wa-bridge` | Node.js + TypeScript | Baileys session; receive / send WhatsApp messages |
| `orchestrator` | Python 3.11 | LLM intent + entity extraction, flow state, replies |

## Conversation model

Free-form, LLM-driven. The orchestrator's system prompt gives the LLM five tool choices:

1. `faq(question)` → `rag_qdrant.ask(question)`
2. `book_new(fields)` → multi-turn gather → `booking_flow.py new --confirm`
3. `book_edit(fields)` → lookup by phone → user picks event → `booking_flow.py edit --confirm`
4. `book_cancel(fields)` → lookup by phone → user picks event → `booking_flow.py cancel --confirm`
5. `handoff(reason)` → notify `WA_NOTIFY` numbers, reply with `ADMIN_CONTACT_NUMBER`

Anything off-scope (refund dispute, complaint, custom pricing, abuse) → handoff. Bot never improvises.

Language is **auto-detected** from the inbound message. The LLM replies in the same language (EN or 中文).

Destructive ops (edit / cancel) **always** require a plain `YES` reply to a confirmation prompt. Any other reply = abort.

## Repo layout

```
skill-chatbot/
├── README.md             # this file
├── SKILL.md              # openclaw skill wrapper (status, logs, deploy, smoke)
├── Makefile              # root dispatch — `make help` for the full list
├── .env.example
├── .editorconfig
├── .gitattributes
├── .gitignore
├── docs/
│   ├── architecture.md
│   ├── ops.md
│   ├── message-flows.md
│   ├── runbook.md
│   ├── security.md
│   └── plans/phase-0-bootstrap.md
├── scripts/
│   ├── create-issues.sh
│   ├── snapshot-upstream.sh
│   ├── install-systemd.sh
│   └── dev-loop.sh
├── wa-bridge/            # Node.js + Baileys
│   ├── package.json
│   ├── tsconfig.json
│   ├── vitest.config.ts
│   ├── Makefile
│   ├── .env.example
│   ├── auth_info/        # runtime, gitignored
│   ├── queue/            # runtime, gitignored
│   ├── src/              # index, auth, socket, inbox, http, sender, log, env, image
│   ├── bin/auth.ts
│   └── tests/
└── orchestrator/         # Python 3.11
    ├── pyproject.toml
    ├── ruff.toml
    ├── Makefile
    ├── .env.example
    ├── state.sqlite      # runtime, gitignored
    ├── src/              # main, tail, state, http, router, inference, rag,
    │                     # booking_subprocess, i18n, language, notify, enums,
    │                     # admin/, prompts/, flows/
    ├── scripts/          # smoke, ingest_rules, ingest_file, reindex
    └── tests/
├── admin-bot/           # thin Telegram-side admin skill (issue #31)
│   ├── SKILL.md
│   ├── bin/             # /ingest /grant /revoke /show /bookings /config
│   ├── tests/
│   └── .env.example
```

## Reuses (do not reinvent)
## Data sources

The FAQ tool answers from content pushed into the existing Qdrant collection. Two operator-editable sources ship in the repo; re-ingest after any edit.

| Source | Path | Ingest command |
|---|---|---|
| Booking rules | `orchestrator/data/booking_rules.yaml` (copied from the upstream `farm-tour-booking` skill) | `make ingest-rules` |
| FAQ corpus | `orchestrator/data/faq.md` | `make ingest-file FILE=orchestrator/data/faq.md` |

Both go through `from rag_qdrant import ingest_text; ingest_text(markdown, source=…)` and are idempotent: re-running on the same `source` updates in place (point-id hashing).

Refresh the snapshots with `bash scripts/snapshot-upstream.sh`.


- **rag-qdrant** — `/root/.openclaw/skills/rag-qdrant/` · `from rag_qdrant import ask`
- **farm-tour-booking** — `/root/.openclaw/workspace/admin/skills/farm-tour-booking/` · invoked as `python3 scripts/booking_flow.py …`
- **booking_rules.yaml** — `config/booking_rules.yaml` in farm-tour-booking; source of truth for hours, capacity, pricing, blackout dates
- **admin-bot** (Telegram admin) — symlink `admin-bot/` into `~/.openclaw/skills/skill-chatbot-admin/` on baadminbot; admins run `/ingest`, `/grant`, `/revoke`, `/show`, `/bookings`, `/config` and the skill calls the orchestrator's `/admin/*` HTTP API (token + Telegram-id gated, defense-in-depth). See `admin-bot/SKILL.md`.

`booking_flow.py list` does **not** support phone filtering. Per design decision the orchestrator pulls events and filters in-process (the farm-tour skill may migrate into this repo later — no patch to the upstream skill in v1).

## Quick start

The dev loop is driven by the root `Makefile`. Every target maps to one job; run `make help` for the full list.

```bash
# 1. Clone + env
git clone https://github.com/A-I-M-S/skill-chatbot
cd skill-chatbot
cp .env.example .env          # then fill in the keys (see .env.example)
make help                      # print every documented target

# 2. Install both daemons
make bridge-install            # wa-bridge: npm ci
make orch-venv                 # orchestrator: python3.11 -m venv .venv
make orch-install              # orchestrator: pip install -e '.[dev]'

# 3. Pair WhatsApp (prints QR — scan from the WhatsApp app)
make bridge-auth

# 4. Run both daemons (foreground, two background jobs, Ctrl-C stops both)
make bridge-dev                # in one terminal
make orch-dev                  # in another
# or, in a single terminal:
bash scripts/dev-loop.sh

# 5. Tests + lint
make bridge-test
make bridge-lint
make orch-test
make orch-lint
```

See `docs/ops.md` for systemd units, log paths, restart, and the QR re-auth flow.

<<<<<<< HEAD
<<<<<<< HEAD
## Migration plan (rag-qdrant + farm-tour-booking → skill-chatbot)

Tracking issue is the epic — open it for the full plan and dependency graph. Two reference docs gate the cutover:

- [`docs/smoke-test.md`](docs/smoke-test.md) — 14-case end-to-end smoke (7 customer WA + 7 admin TG). GO verdict required before cutover.
- [`docs/cutover.md`](docs/cutover.md) — minute-by-minute cutover sequence (T+0 → T+30), rollback path (`<15 min`, zero data loss), +24h/+7d monitoring checklist.

Idempotent retire script: `scripts/retire-old-skills.sh` (removes the rag-qdrant skill dir and quarantines any leftover `farm-tour-booking-*` proposals).
=======
## Cutover smoke test

Before retiring `rag-qdrant` + `farm-tour-booking` (issue #36), run the end-to-end smoke test against the test WhatsApp number. The full 14-case plan + report template lives in [`docs/smoke-test.md`](docs/smoke-test.md). 7 customer-side cases (RAG query, new booking, edit, cancel, pricing, out-of-scope) + 7 admin-side cases (ACL show, bookings prompt + list, ingest, config patch + reject, non-admin refusal).
>>>>>>> c7e5ff8 (docs(smoke): 14-case cutover smoke test plan + report template (#33))
=======
## Production install (systemd)

No Docker. One box (Debian-family), one `sudo` invocation, two daemons + one nightly snapshot timer.

```bash
# On a clean Debian box (Debian 12+/Ubuntu 22.04+):
sudo apt-get install -y python3 python3-venv python3-pip nodejs npm jq rsync

# Clone the repo anywhere; the installer will rsync it to /opt/skill-chatbot.
git clone https://github.com/A-I-M-S/skill-chatbot /tmp/skill-chatbot

# Run the installer (creates skill-chatbot system user, lays out /opt,
# creates venv, builds wa-bridge, writes /etc/skill-chatbot.env,
# installs + enables both systemd units + the nightly snapshot timer).
sudo bash /tmp/skill-chatbot/scripts/install-systemd-system.sh

# Fill in the env file (the installer will refuse to start the units
# until you do):
sudo -e /etc/skill-chatbot.env
#   - QDRANT_URL, QDRANT_API_KEY
#   - INFERENCE_BASE_URL, INFERENCE_API_KEY  (MiniMax endpoint)
#   - COMPOSIO_API_KEY, COMPOSIO_CONNECTED_ACCOUNT_ID  (SAAC Tour Outlook)
#   - ADMIN_TELEGRAM_IDS                      (comma-separated ints)
#   - ADMIN_HTTP_TOKEN                         (openssl rand -hex 32)
#   - WA_NOTIFY                                (small list, e.g. +60123456789,+60198765432)
#   - WA_BRIDGE_TOKEN                          (openssl rand -hex 32)
#   - BOOKING_RULES_PATH                       (absolute path to booking_rules.yaml)

sudo bash /tmp/skill-chatbot/scripts/install-systemd-system.sh  # 2nd run picks up the env

# Pair WhatsApp (one-time, prints QR — scan from the WhatsApp app):
sudo journalctl -u skill-chatbot-wa-bridge -f
# (after pairing, the session lives in /var/lib/skill-chatbot/wa-bridge/auth/)

# Verify
systemctl status skill-chatbot-wa-bridge skill-chatbot-orchestrator
curl -sf http://127.0.0.1:7789/health   # → {"status":"ok",...}
```

To uninstall: `sudo bash /tmp/skill-chatbot/scripts/install-systemd-system.sh --remove`. Repo + env are left in place for safe rollback.

### Component layout

```
/opt/skill-chatbot/                      # the repo, owned by skill-chatbot user
  venv/                                  # Python venv (system-wide, not in repo)
  wa-bridge/, orchestrator/, admin-bot/
/etc/skill-chatbot.env                   # the one env file (mode 600)
/var/lib/skill-chatbot/wa-bridge/auth/   # Baileys session (survives restart)
/var/log/skill-chatbot/                  # journald-mirrored logs
```

### Manage

```bash
sudo systemctl restart skill-chatbot-wa-bridge
sudo systemctl restart skill-chatbot-orchestrator
sudo systemctl list-timers skill-chatbot-qdrant-snapshot.timer
sudo journalctl -u skill-chatbot-orchestrator -f
```

See `docs/ops.md` for the full ops runbook (re-auth, restore from snapshot, etc.).
>>>>>>> 4ca0167 (feat(deploy): system-level systemd units + installer (#32))

## Status

**v1 in development.** Tasks are tracked as GitHub Issues — see the [Issues](../../issues) tab. Opencode is driving implementation in a Plan → Build loop against the model `MiniMax-M3`.

## License

Proprietary — © A-I-M-S. Internal use only.
