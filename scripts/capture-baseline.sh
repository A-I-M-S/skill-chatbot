#!/usr/bin/env bash
# scripts/capture-baseline.sh — one-shot pre-cutover state capture
# (issue #35). Records the current state of baadminbot + Qdrant +
# WhatsApp + env key names into a single `migration-baseline.md` so the
# cutover (and any rollback) has a known starting point.
#
# Run once on baadminbot before the cutover. Output goes to stdout
# (Markdown); redirect to a file to commit.
#
# Does NOT print secret values — only env key names, plus a hash of
# QDRANT_API_KEY / INFERENCE_API_KEY / COMPOSIO_API_KEY / WA_BRIDGE_TOKEN
# so a reviewer can confirm "yes the same keys were active" without
# exposing them in the repo.
set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/skill-chatbot.env}"
OUT="${OUT:-/dev/stdout}"

# Source env if readable; never required (the script works without it).
if [[ -r "$ENV_FILE" ]]; then
    set -a; . "$ENV_FILE"; set +a
fi

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
hash8() { printf '%s' "${1:-}" | sha256sum | cut -c1-8; }

{
echo "# Migration baseline — captured $(ts)"
echo
echo "Source of truth for the day *before* the rag-qdrant + farm-tour-booking → skill-chatbot cutover. Committed to the repo at \`migration-baseline/\` for audit."
echo

# ── 1. baadminbot ─────────────────────────────────────────────────────────
echo "## 1. baadminbot state"
echo
echo "| Field | Value |"
echo "|---|---|"
echo "| Capture time (UTC) | $(ts) |"
echo "| Hostname | $(hostname 2>/dev/null || echo unknown) |"
echo "| OS | $(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-unknown}" || echo unknown) |"
echo "| Kernel | $(uname -r 2>/dev/null || echo unknown) |"
echo "| OpenClaw binary | $(command -v openclaw 2>/dev/null || echo not-in-path) |"
if command -v openclaw >/dev/null 2>&1; then
    echo "| OpenClaw version | $(openclaw --version 2>/dev/null || echo unknown) |"
fi
echo "| Env file | \`$ENV_FILE\` (readable: $([[ -r $ENV_FILE ]] && echo yes || echo no)) |"
echo "| ADMIN_TELEGRAM_IDS | \`${ADMIN_TELEGRAM_IDS:-unset}\` |"
echo "| Installed skills | \`$(ls ~/.openclaw/skills/ 2>/dev/null | tr '\n' ' ' || echo unknown)\` |"
echo "| rag-qdrant skill present | $([[ -d ~/.openclaw/skills/rag-qdrant ]] && echo yes || echo no) |"
echo
echo "### Currently-running OpenClaw Telegram bot"
echo
if command -v systemctl >/dev/null 2>&1; then
    systemctl --no-pager --plain list-units --type=service --state=running 2>/dev/null \
        | grep -iE 'openclaw|telegram|baadminbot' \
        | sed 's/^/| `/;s/$/` |/' \
        || echo "| (no matching running units) |"
else
    echo "| systemctl not available |"
fi
echo

# ── 2. Qdrant state ───────────────────────────────────────────────────────
echo "## 2. Qdrant state"
echo
echo "| Field | Value |"
echo "|---|---|"
echo "| URL | \`${QDRANT_URL:-unset}\` |"
echo "| API key sha256[0:8] | \`$(hash8 "${QDRANT_API_KEY:-}")\` |"
echo "| Collection | \`${QDRANT_COLLECTION:-unset}\` |"
if [[ -n "${QDRANT_URL:-}" && -n "${QDRANT_API_KEY:-}" ]]; then
    info=$(curl -sf -H "api-key: ${QDRANT_API_KEY}" "${QDRANT_URL}/collections/${QDRANT_COLLECTION:-__missing__}" 2>/dev/null || echo '{"status":{"error":"unreachable"}}')
    vector_count=$(printf '%s' "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("result",{}).get("vectors_count","?"))' 2>/dev/null || echo "?")
    points_count=$(printf '%s' "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("result",{}).get("points_count","?"))' 2>/dev/null || echo "?")
    echo "| Vector count | $vector_count |"
    echo "| Points count | $points_count |"
    echo
    echo "### One-shot snapshot"
    echo
    snap=$(curl -sf -X POST -H "api-key: ${QDRANT_API_KEY}" "${QDRANT_URL}/collections/${QDRANT_COLLECTION}/snapshots" 2>/dev/null || echo "")
    if [[ -n "$snap" ]]; then
        snap_name=$(printf '%s' "$snap" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("result",{}).get("name","?"))' 2>/dev/null || echo "?")
        echo "Snapshot \`$snap_name\` created. Remains inside the Qdrant volume (use \`GET /collections/${QDRANT_COLLECTION}/snapshots\` to list)."
    else
        echo "Snapshot creation failed (Qdrant unreachable?) — record manually if needed."
    fi
else
    echo "| (QDRANT_URL / QDRANT_API_KEY not set; skipping live read) |"
fi
echo

# ── 3. WhatsApp state ────────────────────────────────────────────────────
echo "## 3. WhatsApp state"
echo
echo "| Field | Value |"
echo "|---|---|"
echo "| WA bridge auth dir | \`${STATE_DIR:-${WA_BRIDGE_AUTH_DIR:-/var/lib/skill-chatbot/wa-bridge/auth}}\` |"
echo "| WA bridge token sha256[0:8] | \`$(hash8 "${WA_BRIDGE_TOKEN:-}")\` |"
if [[ -d "${STATE_DIR:-/var/lib/skill-chatbot/wa-bridge/auth}" ]]; then
    files=$(ls -1 "${STATE_DIR:-/var/lib/skill-chatbot/wa-bridge/auth}" 2>/dev/null | wc -l)
    echo "| Auth dir files | $files |"
    newest=$(ls -1t "${STATE_DIR:-/var/lib/skill-chatbot/wa-bridge/auth}" 2>/dev/null | head -1 || echo "")
    [[ -n "$newest" ]] && echo "| Newest auth file | \`$newest\` (mtime: $(stat -c %y "${STATE_DIR:-/var/lib/skill-chatbot/wa-bridge/auth}/$newest" 2>/dev/null || echo unknown)) |"
fi
if [[ -n "${ADMIN_CONTACT_NUMBER:-}" ]]; then
    echo "| Admin contact (E.164) | \`${ADMIN_CONTACT_NUMBER:0:6}…\` (masked) |"
fi
echo "| WA_NOTIFY | \`${WA_NOTIFY:-unset}\` |"
echo

# ── 4. Env key shape (not values) ────────────────────────────────────────
echo "## 4. Env keys present (no values)"
echo
echo "Shape of the env so the new \`.env\` matches 1:1. Empty lines and comments stripped. Source: \`$ENV_FILE\`."
echo
if [[ -r "$ENV_FILE" ]]; then
    echo '```'
    grep -vE '^\s*(#|$)' "$ENV_FILE" | cut -d= -f1 | sort -u
    echo '```'
else
    echo "(\`$ENV_FILE\` not readable — recorded as unknown)"
fi
echo

# ── 5. Composio / Inference endpoint reachability ────────────────────────
echo "## 5. External services reachability"
echo
echo "| Service | Result |"
echo "|---|---|"
if [[ -n "${INFERENCE_BASE_URL:-}" ]]; then
    code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "${INFERENCE_BASE_URL}/models" 2>/dev/null || echo "fail")
    echo "| \`INFERENCE_BASE_URL\` (MiniMax endpoint) | HTTP ${code} |"
fi
if [[ -n "${COMPOSIO_API_KEY:-}" ]]; then
    code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 -H "X-API-KEY: ${COMPOSIO_API_KEY}" https://backend.composio.dev/api/v1/actions 2>/dev/null || echo "fail")
    echo "| \`COMPOSIO_API_KEY\` | HTTP ${code} (Composio backend) |"
fi
echo

# ── 6. Closing ────────────────────────────────────────────────────────────
echo "## 6. Notes"
echo
echo "- All secret values are SHA-256 truncated to 8 chars so a reviewer can confirm \"same key was active\" without exposing it."
echo "- Qdrant snapshot was created (if reachable) and stays inside the Qdrant volume; do NOT delete it until the cutover + 7-day soak."
echo "- Commit this file to the repo at \`migration-baseline/\`."
} > "$OUT"
