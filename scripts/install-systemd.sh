#!/usr/bin/env bash
# install-systemd.sh — install (or remove) the wa-bridge + orchestrator systemd --user units.
#
# Idempotent: re-running with the same arguments is safe. The script writes the
# unit files to ~/.config/systemd/user/ and (on install) enables + starts them.
#
# Usage:
#   bash scripts/install-systemd.sh           # install + enable + start
#   bash scripts/install-systemd.sh --remove  # stop + disable + remove

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
BRIDGE_UNIT="skill-chatbot-bridge.service"
ORCH_UNIT="skill-chatbot-orchestrator.service"
LOG_DIR="/var/log/skill-chatbot"
RUN_AS="${SUDO_USER:-${USER}}"

# Resolve repo paths relative to the actual checkout. The units must run from
# the same path the user (and opencode) used to develop the code, so we embed
# the absolute path of REPO_ROOT.
WA_BRIDGE_DIR="$REPO_ROOT/wa-bridge"
ORCH_DIR="$REPO_ROOT/orchestrator"
ENV_FILE="$REPO_ROOT/.env"
WA_AUTH_DIR="${RAG_PHOTOS_DIR_PARENT:-$HOME/.openclaw/skill-chatbot}/auth"
LOGROTATE_FILE="/etc/logrotate.d/skill-chatbot"

REMOVE=0
if [[ "${1:-}" == "--remove" ]]; then
    REMOVE=1
fi

run_unit_action() {
    local unit="$1" action="$2"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user "$action" "$unit" || {
            echo "warning: systemctl --user $action $unit failed (continuing)" >&2
        }
    fi
}

# ─── Remove path ───────────────────────────────────────────────────────
if [[ "$REMOVE" -eq 1 ]]; then
    echo "Removing skill-chatbot systemd --user units..."
    run_unit_action "$BRIDGE_UNIT" stop
    run_unit_action "$ORCH_UNIT" stop
    run_unit_action "$BRIDGE_UNIT" disable
    run_unit_action "$ORCH_UNIT" disable
    rm -f "$USER_UNIT_DIR/$BRIDGE_UNIT" "$USER_UNIT_DIR/$ORCH_UNIT"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload
    fi
    echo "Done. Log files at $LOG_DIR were not removed (operator may want to keep them)."
    if [[ -f "$LOGROTATE_FILE" ]]; then
        if [[ -w "$LOGROTATE_FILE" ]] || command -v sudo >/dev/null 2>&1; then
            sudo rm -f "$LOGROTATE_FILE" && echo "Removed $LOGROTATE_FILE (logrotate config)."
        else
            echo "warning: could not remove $LOGROTATE_FILE (no sudo). Remove it manually." >&2
        fi
    fi
    exit 0
fi

# ─── Install path ──────────────────────────────────────────────────────
echo "Installing skill-chatbot systemd --user units..."

if [[ ! -d "$WA_BRIDGE_DIR" ]] || [[ ! -d "$ORCH_DIR" ]]; then
    echo "error: $REPO_ROOT does not look like the skill-chatbot checkout (wa-bridge/ or orchestrator/ missing)" >&2
    exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
    echo "warning: $ENV_FILE not found. The units will fail to start until you create it (see .env.example)." >&2
fi

# Ensure log dir exists; if not writable as the user, try via sudo.
mkdir -p "$LOG_DIR" 2>/dev/null || {
    if command -v sudo >/dev/null 2>&1; then
        sudo mkdir -p "$LOG_DIR"
        sudo chown "$RUN_AS:$RUN_AS" "$LOG_DIR" 2>/dev/null || true
    else
        echo "error: $LOG_DIR not writable and sudo unavailable" >&2
        exit 1
    fi
}

mkdir -p "$USER_UNIT_DIR"

cat > "$USER_UNIT_DIR/$BRIDGE_UNIT" <<EOF
[Unit]
Description=skill-chatbot wa-bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$WA_BRIDGE_DIR
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/node $WA_BRIDGE_DIR/dist/src/index.js
Restart=on-failure
RestartSec=5
StandardOutput=append:$LOG_DIR/bridge.log
StandardError=append:$LOG_DIR/bridge.log

[Install]
WantedBy=default.target
EOF

cat > "$USER_UNIT_DIR/$ORCH_UNIT" <<EOF
[Unit]
Description=skill-chatbot orchestrator
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ORCH_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$ORCH_DIR/.venv/bin/python -m src.main
Restart=on-failure
RestartSec=5
StandardOutput=append:$LOG_DIR/orchestrator.log
StandardError=append:$LOG_DIR/orchestrator.log

[Install]
WantedBy=default.target
EOF

# logrotate (weekly, 12 retained, copytruncate-safe)
LOGROTATE_CONTENT="\
$LOG_DIR/bridge.log $LOG_DIR/orchestrator.log {
    weekly
    rotate 12
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    create 0640 $RUN_AS $RUN_AS
}
"
if [[ -w /etc/logrotate.d ]] || command -v sudo >/dev/null 2>&1; then
    if [[ -w /etc/logrotate.d ]]; then
        printf '%s' "$LOGROTATE_CONTENT" > "$LOGROTATE_FILE"
    else
        printf '%s' "$LOGROTATE_CONTENT" | sudo tee "$LOGROTATE_FILE" >/dev/null
    fi
    echo "Wrote $LOGROTATE_FILE"
else
    echo "warning: could not write $LOGROTATE_FILE (no sudo, /etc/logrotate.d not writable). Add it manually." >&2
fi

# Reload + enable + start
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
    run_unit_action "$BRIDGE_UNIT" enable
    run_unit_action "$ORCH_UNIT" enable
    run_unit_action "$BRIDGE_UNIT" start
    run_unit_action "$ORCH_UNIT" start
    echo "Enabled + started both units. Tail logs with:"
    echo "  journalctl --user -u skill-chatbot-bridge -u skill-chatbot-orchestrator -f"
else
    echo "warning: systemctl not found. The unit files are installed but not enabled." >&2
fi

echo "Done."
