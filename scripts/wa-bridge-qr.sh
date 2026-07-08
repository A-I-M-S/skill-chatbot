#!/usr/bin/env bash
# scripts/wa-bridge-qr.sh — produce a scannable QR PNG for wa-bridge pairing.
#
# Why this exists
# ---------------
# wa-bridge's auth flow: when started WITHOUT an existing auth state
# (`/var/lib/skill-chatbot/wa-bridge/auth/`), Baileys emits a raw QR
# payload to stdout. With a TTY it renders as ASCII art; without one
# (e.g. under systemd → journald) it prints `qr=<payload>`.
#
# Systemd output is text-only and not directly scannable. This script
# runs wa-bridge briefly, captures the `qr=` line, renders it to a
# PNG you can scan from the WhatsApp app on your phone.
#
# Usage
# -----
#   sudo bash scripts/wa-bridge-qr.sh                    # writes /tmp/wa-bridge-qr.png
#   sudo bash scripts/wa-bridge-qr.sh /path/to/qr.png   # custom output path
#   sudo bash scripts/wa-bridge-qr.sh --pair-and-stay   # run forever after pairing (default: quit after first QR)
#
# Pairing + staying alive: after scan, the auth state is written to
# WA_AUTH_DIR and the service is ready to run under systemd.
#
# Run-time deps: node + npm (already on the wa-bridge host), plus the
# `qrcode` npm package (installed once via `npm i -g qrcode` or in a
# throwaway venv). Uses a tiny inline Node script for rendering so we
# don't take on Python + Pillow just for this.
set -euo pipefail

# Helpers (defined before first use — this script runs under `set -e`).
die()  { echo "error: $*" >&2; exit 1; }
note() { echo "[wa-bridge-qr] $*" >&2; }

OUT_PATH="${1:-/tmp/wa-bridge-qr.png}"
STAY_ALIVE=0
[[ "${2:-}" == "--pair-and-stay" || "${1:-}" == "--pair-and-stay" ]] && STAY_ALIVE=1

# Resolve install layout. Production install puts the repo at
# /opt/skill-chatbot; dev installs may live anywhere.
REPO_ROOT="${REPO_ROOT:-/opt/skill-chatbot}"
WA_BRIDGE_DIR="$REPO_ROOT/wa-bridge"
ENV_FILE="${ENV_FILE:-/etc/skill-chatbot.env}"

[[ -d "$WA_BRIDGE_DIR" ]] || die "wa-bridge not found at $WA_BRIDGE_DIR — set REPO_ROOT"
[[ -f "$ENV_FILE" ]]    || die "env file not found at $ENV_FILE — set ENV_FILE"

# Load env so we honour WA_AUTH_DIR / INBOX_PATH / etc.
set -a; . "$ENV_FILE"; set +a

# Refuse if auth is already present — otherwise we'd wipe a working
# session.
if [[ -d "${WA_AUTH_DIR:-/var/lib/skill-chatbot/wa-bridge/auth}" ]] \
   && [[ -f "${WA_AUTH_DIR}/creds.json" ]]; then
    die "auth state already present at ${WA_AUTH_DIR}/creds.json — nothing to pair. To re-link, use the pairing-code routine (\`npm run auth:code\`, see SKILL.md → Re-link WhatsApp) or remove the auth dir manually first."
fi

# Make sure the build is current.
( cd "$WA_BRIDGE_DIR" && npm ci --omit=dev >/dev/null 2>&1 ) || \
    ( cd "$WA_BRIDGE_DIR" && npm install --omit=dev >/dev/null 2>&1 )

LOG="/tmp/wa-bridge-qr.log"
rm -f "$LOG"
: > "$LOG"

# Start wa-bridge in the background, stdout+stderr → LOG.
( cd "$WA_BRIDGE_DIR" && node dist/src/index.js ) > "$LOG" 2>&1 &
WA_PID=$!

cleanup() {
    if kill -0 "$WA_PID" 2>/dev/null && [[ "$STAY_ALIVE" -eq 0 ]]; then
        kill "$WA_PID" 2>/dev/null || true
        wait "$WA_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

note "wa-bridge started (pid $WA_PID), waiting for QR (≤30s)…"

QR=""
for _ in $(seq 1 60); do
    QR=$(grep -oE '^qr=[A-Za-z0-9+/=._-]+' "$LOG" 2>/dev/null | head -1 | cut -d= -f2-)
    [[ -n "$QR" ]] && break
    sleep 0.5
done

if [[ -z "$QR" ]]; then
    echo "error: no QR emitted within 30s. Last 30 lines of log:" >&2
    tail -30 "$LOG" >&2 || true
    echo "" >&2
    echo "common causes:" >&2
    echo "  - this box's IP is on Meta's blocklist (datacenter / VPS IPs often are)" >&2
    echo "  - the @whiskeysockets/baileys version doesn't match what WhatsApp expects" >&2
    echo "  - outbound HTTPS to web.whatsapp.com is blocked by the host firewall" >&2
    exit 1
fi

note "QR captured (${#QR} chars). Rendering PNG to $OUT_PATH…"

# Render with a tiny inline Node script. qrcode is a small dep; install
# it once via `npm i -g qrcode` if missing. Falls back to npx.
QR_SCRIPT='
const qrcode = require("qrcode");
const qr = process.argv[1];
const out = process.argv[2];
qrcode.toFile(out, qr, { type: "png", margin: 2, scale: 8, color: { dark: "#000000", light: "#FFFFFF" } })
    .then(() => process.exit(0))
    .catch((e) => { console.error(e); process.exit(1); });
'
NODE_PATH="$(npm root -g 2>/dev/null || echo '')" \
    node -e "$QR_SCRIPT" "$QR" "$OUT_PATH"

ls -l "$OUT_PATH" >&2
echo "$OUT_PATH"

if [[ "$STAY_ALIVE" -eq 1 ]]; then
    note "STAY_ALIVE: leaving wa-bridge running. Auth state will be at ${WA_AUTH_DIR}/ after scan. systemd can now manage it."
    wait "$WA_PID"
fi
