#!/usr/bin/env bash
# tests/test_install_systemd_system.sh — smoke tests for the
# system-level systemd installer (issue #32). Verifies syntax of the
# unit files + the installer script, but does NOT actually install
# anything (that requires root and a real checkout layout).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$REPO_ROOT/scripts/install-systemd-system.sh"
BRIDGE="$REPO_ROOT/systemd/skill-chatbot-wa-bridge.service"
ORCH="$REPO_ROOT/systemd/skill-chatbot-orchestrator.service"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "  ok: $*"; }

# ── 1. syntax ─────────────────────────────────────────────────────────────
bash -n "$INSTALLER" || fail "bash -n installer"
pass "installer syntax"

# ── 2. systemd unit files parse ──────────────────────────────────────────
if command -v systemd-analyze >/dev/null 2>&1; then
    # The Python venv + /opt/skill-chatbot paths don't exist in CI; that's
    # expected. We only check unit-file syntax.
    out_b=$(systemd-analyze verify --no-pager "$BRIDGE" 2>&1 || true)
    out_o=$(systemd-analyze verify --no-pager "$ORCH" 2>&1 || true)
    # Filter out runtime warnings (paths that don't exist yet on a dev box).
    fatal_b=$(echo "$out_b" | grep -vE 'is not executable' | grep -vE '^$' || true)
    fatal_o=$(echo "$out_o" | grep -vE 'is not executable' | grep -vE '^$' || true)
    if [[ -n "$fatal_b" || -n "$fatal_o" ]]; then
        echo "bridge verify output: $out_b"
        echo "orch verify output:   $out_o"
        fail "systemd-analyze verify flagged issues"
    fi
    pass "systemd-analyze verify (runtime warnings ignored)"
else
    # Fall back to a basic INI sanity check.
    for f in "$BRIDGE" "$ORCH"; do
        grep -q "^\[Unit\]$" "$f" || fail "$f missing [Unit]"
        grep -q "^\[Service\]$" "$f" || fail "$f missing [Service]"
        grep -q "^\[Install\]$" "$f" || fail "$f missing [Install]"
        grep -q "^EnvironmentFile=/etc/skill-chatbot.env$" "$f" || fail "$f missing env file"
        grep -q "^Restart=always$" "$f" || fail "$f missing Restart=always"
    done
    pass "basic unit-file structure"
fi

# ── 3. installer requires root (source check) ─────────────────────────────
grep -q 'require_root()' "$INSTALLER" || fail "missing require_root() function"
grep -q 'must run as root' "$INSTALLER" || fail "missing 'must run as root' message"
pass "root-required gate present"

# ── 4. --remove path is symmetric ─────────────────────────────────────
grep -q '\-\-remove' "$INSTALLER" || fail "missing --remove path"
grep -q 'REMOVE=1' "$INSTALLER" || fail "missing REMOVE=1 handling"
pass "--remove path present"

# ── 5. install path requires repo layout ────────────────────────────────
grep -q 'does not look like the skill-chatbot checkout' "$INSTALLER" \
    || fail "missing repo-layout guard"
pass "repo-layout guard present"

echo "ALL OK"
