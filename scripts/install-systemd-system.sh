#!/usr/bin/env bash
# install-systemd-system.sh — install (or remove) the wa-bridge +
# orchestrator systemd **system** units (issue #32, production deploy).
#
# Idempotent. Creates the `skill-chatbot` user/group if missing, lays
# out the repo at /opt/skill-chatbot, sets up the env file, enables
# + starts both daemons, and (on --remove) does the inverse.
#
# Usage:
#   sudo bash scripts/install-systemd-system.sh           # install
#   sudo bash scripts/install-systemd-system.sh --remove  # uninstall
#
# Companion unit files live in systemd/ alongside this script.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/opt/skill-chatbot"
UNIT_SRC_DIR="$REPO_ROOT/systemd"
UNIT_DST_DIR="/etc/systemd/system"
UNIT_BRIDGE="skill-chatbot-wa-bridge.service"
UNIT_ORCH="skill-chatbot-orchestrator.service"
UNIT_TIMER="skill-chatbot-qdrant-snapshot.timer"
UNIT_SNAPSHOT="skill-chatbot-qdrant-snapshot.service"
ENV_FILE="/etc/skill-chatbot.env"
RUN_USER="skill-chatbot"
RUN_GROUP="skill-chatbot"

REMOVE=0
if [[ "${1:-}" == "--remove" ]]; then
    REMOVE=1
fi

# ── helpers ────────────────────────────────────────────────────────────
die() { echo "error: $*" >&2; exit 1; }
note() { echo "  $*" >&2; }

systemctl_safe() {
    if command -v systemctl >/dev/null 2>&1; then
        systemctl "$@" || die "systemctl $* failed"
    fi
}

require_root() {
    [[ $EUID -eq 0 ]] || die "must run as root (use sudo)"
}

ensure_user() {
    if ! id "$RUN_USER" >/dev/null 2>&1; then
        note "creating system user $RUN_USER"
        useradd --system --no-create-home --shell /usr/sbin/nologin "$RUN_USER"
    fi
}

# ── remove path ────────────────────────────────────────────────────────
if [[ "$REMOVE" -eq 1 ]]; then
    require_root
    echo "Removing skill-chatbot systemd system units..."
    systemctl_safe stop "$UNIT_BRIDGE" "$UNIT_ORCH" "$UNIT_TIMER" || true
    systemctl_safe disable "$UNIT_BRIDGE" "$UNIT_ORCH" "$UNIT_TIMER" || true
    rm -f "$UNIT_DST_DIR/$UNIT_BRIDGE" \
          "$UNIT_DST_DIR/$UNIT_ORCH" \
          "$UNIT_DST_DIR/$UNIT_TIMER" \
          "$UNIT_DST_DIR/$UNIT_SNAPSHOT"
    systemctl_safe daemon-reload
    echo "Done. Repo at $INSTALL_DIR and env at $ENV_FILE were NOT removed."
    echo "To fully uninstall: sudo rm -rf $INSTALL_DIR $ENV_FILE"
    exit 0
fi

# ── install path ───────────────────────────────────────────────────────
require_root
[[ -d "$REPO_ROOT/wa-bridge" ]] || die "$REPO_ROOT does not look like the skill-chatbot checkout"
[[ -d "$REPO_ROOT/orchestrator" ]] || die "$REPO_ROOT does not look like the skill-chatbot checkout"

ensure_user

# Lay out the repo at /opt/skill-chatbot.
if [[ "$REPO_ROOT" != "$INSTALL_DIR" ]]; then
    note "copying repo to $INSTALL_DIR (rsync preserves git history + permissions)"
    mkdir -p "$INSTALL_DIR"
    rsync -a --delete --exclude='.venv' --exclude='node_modules' --exclude='__pycache__' \
        "$REPO_ROOT/" "$INSTALL_DIR/"
fi
chown -R "$RUN_USER:$RUN_GROUP" "$INSTALL_DIR"

# Create the Python venv at /opt/skill-chatbot/venv (separate from any
# dev venv inside the repo).
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    note "creating Python venv at $INSTALL_DIR/venv"
    sudo -u "$RUN_USER" python3 -m venv "$INSTALL_DIR/venv"
    sudo -u "$RUN_USER" "$INSTALL_DIR/venv/bin/pip" install -U pip
    sudo -u "$RUN_USER" "$INSTALL_DIR/venv/bin/pip" install -e "$INSTALL_DIR/orchestrator[dev]"
fi

# Build wa-bridge if not already built.
if [[ ! -f "$INSTALL_DIR/wa-bridge/dist/src/index.js" ]]; then
    note "building wa-bridge (npm ci + tsc)"
    sudo -u "$RUN_USER" bash -c "cd '$INSTALL_DIR/wa-bridge' && npm ci && npm run build"
fi

# Env file.
if [[ ! -f "$ENV_FILE" ]]; then
    note "creating $ENV_FILE from .env.example"
    cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    chown root:"$RUN_GROUP" "$ENV_FILE"
    echo
    echo "*** ACTION REQUIRED ***"
    echo "    Edit $ENV_FILE and fill in:"
    echo "      QDRANT_URL, QDRANT_API_KEY, INFERENCE_BASE_URL, INFERENCE_API_KEY,"
    echo "      COMPOSIO_API_KEY, COMPOSIO_CONNECTED_ACCOUNT_ID, ADMIN_TELEGRAM_IDS,"
    echo "      ADMIN_HTTP_TOKEN (random 32-byte hex), WA_NOTIFY (small list, comma-separated)."
    echo "    Then re-run this script to enable + start the units."
    exit 1
fi
chmod 600 "$ENV_FILE"

# Install units.
note "installing systemd units to $UNIT_DST_DIR"
install -m 0644 "$UNIT_SRC_DIR/$UNIT_BRIDGE" "$UNIT_DST_DIR/$UNIT_BRIDGE"
install -m 0644 "$UNIT_SRC_DIR/$UNIT_ORCH" "$UNIT_DST_DIR/$UNIT_ORCH"
if [[ -f "$UNIT_SRC_DIR/$UNIT_TIMER" ]]; then
    install -m 0644 "$UNIT_SRC_DIR/$UNIT_TIMER" "$UNIT_DST_DIR/$UNIT_TIMER"
    install -m 0644 "$UNIT_SRC_DIR/$UNIT_SNAPSHOT" "$UNIT_DST_DIR/$UNIT_SNAPSHOT"
fi

systemctl_safe daemon-reload
systemctl_safe enable "$UNIT_BRIDGE" "$UNIT_ORCH"
systemctl_safe restart "$UNIT_BRIDGE" "$UNIT_ORCH"
if [[ -f "$UNIT_DST_DIR/$UNIT_TIMER" ]]; then
    systemctl_safe enable --now "$UNIT_TIMER"
fi

echo
echo "OK — both units enabled and started."
echo "  sudo systemctl status $UNIT_BRIDGE"
echo "  sudo systemctl status $UNIT_ORCH"
echo "  sudo journalctl -u $UNIT_BRIDGE -u $UNIT_ORCH -f"
