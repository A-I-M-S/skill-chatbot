---
name: skill-chatbot-admin
description: TG-side admin skill for the SAAC FARM WhatsApp chatbot. Ingest FAQ files into Qdrant, grant/revoke per-source Telegram ACL, list bookings for a day, and patch booking_rules.yaml — over the orchestrator's /admin HTTP API.
user-invocable: true
metadata:
  openclaw:
    emoji: "🛠️"
    requires:
      bins: ["python3"]
      anyBins: []
      env: ["ADMIN_API_BASE", "ADMIN_HTTP_TOKEN", "ADMIN_TELEGRAM_IDS"]
    primaryEnv: "ADMIN_HTTP_TOKEN"
    install: []
---

# skill-chatbot-admin

Telegram-side admin companion for the **skill-chatbot** orchestrator
(issue #31). Aloy (and any other admin whose Telegram id is in
`ADMIN_TELEGRAM_IDS`) sends commands from the **baadminbot** Telegram
bot; this skill turns each command into a single call to the
orchestrator's `/admin/*` HTTP API (issue #30) and prints the reply
back to the chat.

The skill is **thin**: it does not touch Qdrant, Outlook, or
`booking_rules.yaml` directly. All state-changing work happens in the
orchestrator process; this skill only routes.

> **Install on baadminbot only.** This skill is intended to run as the
> admin Telegram bot. The customer-facing WhatsApp bot does not need it.

## Setup

1. Copy `.env.example` to `.env` and fill in:

   ```ini
   ADMIN_API_BASE=http://127.0.0.1:7789/admin        # orchestrator HTTP + /admin
   ADMIN_HTTP_TOKEN=                                # same as orchestrator's ADMIN_HTTP_TOKEN
   ADMIN_TELEGRAM_IDS=920567169                     # comma-separated Telegram user ids
   ```

   `ADMIN_API_BASE` must point at the **orchestrator's** HTTP server
   (`ORCHESTRATOR_PORT`, default `7789`). The orchestrator mounts the
   admin sub-app at `/admin/*`; the trailing `/admin` in the base URL
   lets this skill call routes as `${ADMIN_API_BASE}/ingest`,
   `${ADMIN_API_BASE}/grant`, etc.

2. Symlink the skill into the OpenClaw skills directory (one-time, on
   baadminbot):

   ```bash
   ln -s /opt/skill-chatbot/admin-bot \
         ~/.openclaw/skills/skill-chatbot-admin
   cp ~/.openclaw/skills/skill-chatbot-admin/.env.example \
      ~/.openclaw/skills/skill-chatbot-admin/.env
   # fill .env, then restart baadminbot's OpenClaw
   ```

   Replace `/opt/skill-chatbot/admin-bot` with the actual checkout
   path on baadminbot. A copy (not symlink) works equally well if the
   baadminbot filesystem doesn't span the checkout.

3. Restart baadminbot's OpenClaw so it picks up the new skill.

## Commands

| Command | What it does |
|---|---|
| `/ingest <path>` | Ingest a local file into Qdrant (admin-only ACL). `.md` and `.pdf` are auto-routed by extension; pass an explicit `md:` or `pdf:` prefix to force the source type. URL ingest is accepted by the parser but not yet wired server-side (returns 400). |
| `/grant <source> <id\|@user>` | Grant a Telegram user read access to every chunk of `<source>`. `<id>` is a numeric Telegram id; `@user` is resolved through the same local user cache `rag-qdrant` uses (the user must have messaged baadminbot at least once). |
| `/revoke <source> <id\|@user>` | Same shape as `/grant`. |
| `/show access` | List the ACL table — one row per source, plus the ids allowed to read it. |
| `/bookings [YYYY-MM-DD]` | List Outlook events for that day (Asia/Singapore). With no date, the skill replies `which date? (YYYY-MM-DD)` and waits for the next message; with a date, it returns the event list inline. |
| `/config <key> <value>` | Patch `booking_rules.yaml`. Allowed keys: `slot_duration_minutes`, `max_capacity_per_slot`, `operating_hours_per_day`, `blackout_dates`. Anything else returns a clear `disk_only` / `unknown_key` error from the API. |

All replies are **Telegram Markdown** (`*bold*`, `_italic_`, `` `code` ``,
triple-backtick blocks for JSON). The skill never prints more than
~30 lines per event row so the chat doesn't get truncated.

## Required env

| Var | Source | Purpose |
|---|---|---|
| `ADMIN_API_BASE` | `.env` | Orchestrator admin URL, ending in `/admin` |
| `ADMIN_HTTP_TOKEN` | `.env` | Shared secret; sent as `X-Admin-Token` |
| `ADMIN_TELEGRAM_IDS` | `.env` | Comma-separated Telegram ids allowed to run commands |

Defense-in-depth: the skill **refuses** any command whose sender id is
not in `ADMIN_TELEGRAM_IDS` *before* making the API call. The
orchestrator re-checks the same env on the server side (issue #30).
The two checks are independent — neither trusts the other.

## Dependencies

- Python 3.11+ stdlib (`json`, `urllib`, `dataclasses`, `datetime`)
- [`requests`](https://requests.readthedocs.io/) (`pip install requests`)
  — the only third-party library. Anything else needs justification.

The skill does **not** install anything via `pip install` on baadminbot.
Install `requests` once in the system Python or in a small venv that
OpenClaw's handler can resolve.

## How it works

Each `bin/<cmd>` script:

1. Reads the inbound Telegram message + the sender's `telegram_id`
   (passed in by the OpenClaw adapter).
2. Validates the sender against `ADMIN_TELEGRAM_IDS` locally — early
   refusal, no API call, no log noise.
3. Parses the command-specific args.
4. Calls the matching admin API route (`POST /admin/ingest`,
   `POST /admin/grant`, `GET /admin/show?source=…`,
   `GET /admin/bookings?date=…`, `PATCH /admin/config`,
   `POST /admin/revoke`) with `X-Admin-Token` + `X-Admin-Telegram-Id`
   headers from `.env`.
5. Pretty-prints the response as a Telegram Markdown reply.

The single in-process state lives in
`<tmpdir>/admin-bot-state-<pid>.json` and is keyed by sender
`telegram_id`. It is only used to remember that the previous message
was a `/bookings` waiting for a date. The file is created on demand,
written atomically, and never read by another process.

## Edge cases

- **Sender not in `ADMIN_TELEGRAM_IDS`** → `Refused: you're not an admin.`
  No API call, no log line beyond a `DEBUG` entry.
- **Orchestrator unreachable** → `Could not reach the orchestrator: <error>.`
  Try `make status` (on the orchestrator host) or check
  `ADMIN_API_BASE`.
- **API returns 401 / 403** → `Auth rejected by orchestrator: <message>.`
  Re-check `ADMIN_HTTP_TOKEN` matches the orchestrator's value.
- **API returns 4xx with `error` field** → `API error (<error>): <message>.`
- **`/bookings` with a malformed date** → `Bad date: expected YYYY-MM-DD.`
- **`/config` with an unknown key** → the API returns the allowlist;
  the skill echoes it verbatim.

## Reference

- `orchestrator/README.md` — full admin route table + curl examples
- `orchestrator/src/admin/__init__.py` — auth gate + dispatch
- `orchestrator/src/admin/handlers.py` — per-route business logic
- GitHub: https://github.com/A-I-M-S/skill-chatbot ·
  Issues: https://github.com/A-I-M-S/skill-chatbot/issues/31