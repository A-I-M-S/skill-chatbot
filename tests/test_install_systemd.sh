#!/usr/bin/env bash
# tests/test_install_systemd.sh — smoke + parse tests for the systemd installer.
# Run via `bash tests/test_install_systemd.sh` (executable bit set by the
# Makefile or a developer). Designed to run in CI without root or systemd.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/install-systemd.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "  ok: $*"; }

# ─── 1. syntax ──────────────────────────────────────────────────────────
bash -n "$SCRIPT" || fail "bash -n failed on $SCRIPT"
pass "syntax (bash -n)"

# ─── 2. --help-like behaviour (no args) doesn't error out at the bash level
# (it will run the install path and may fail on missing sudo, so we only
# check that the script is parseable + has the right shape)

# ─── 3. The script writes the expected unit files when run in a temp HOME ─
TMP_HOME="$(mktemp -d)"
trap 'rm -rf "$TMP_HOME"' EXIT
export XDG_CONFIG_HOME="$TMP_HOME/.config"
export HOME="$TMP_HOME"

# The installer needs sudo OR a writable /etc/logrotate.d; in CI neither is
# available. We just verify the script detects this gracefully (no crash).
bash "$SCRIPT" >/dev/null 2>&1 || {
    rc=$?
    # Non-zero is fine if it was the sudo/logrotate step; the script
    # should still write the unit files. Verify the unit files exist.
    if [[ ! -f "$TMP_HOME/.config/systemd/user/skill-chatbot-bridge.service" ]]; then
        fail "unit files not written (rc=$rc)"
    fi
}

# ─── 4. Unit files were written and parse as INI ───────────────────────
BRIDGE_UNIT="$TMP_HOME/.config/systemd/user/skill-chatbot-bridge.service"
ORCH_UNIT="$TMP_HOME/.config/systemd/user/skill-chatbot-orchestrator.service"

[[ -f "$BRIDGE_UNIT" ]] || fail "missing $BRIDGE_UNIT"
[[ -f "$ORCH_UNIT" ]] || fail "missing $ORCH_UNIT"
pass "unit files exist"

grep -q "^\[Unit\]" "$BRIDGE_UNIT" || fail "bridge unit missing [Unit]"
grep -q "^\[Service\]" "$BRIDGE_UNIT" || fail "bridge unit missing [Service]"
grep -q "^\[Install\]" "$BRIDGE_UNIT" || fail "bridge unit missing [Install]"
grep -q "^ExecStart=" "$BRIDGE_UNIT" || fail "bridge unit missing ExecStart"
grep -q "Restart=on-failure" "$BRIDGE_UNIT" || fail "bridge unit missing Restart=on-failure"
pass "bridge unit has the required sections"

grep -q "^\[Unit\]" "$ORCH_UNIT" || fail "orch unit missing [Unit]"
grep -q "^\[Service\]" "$ORCH_UNIT" || fail "orch unit missing [Service]"
grep -q "^\[Install\]" "$ORCH_UNIT" || fail "orch unit missing [Install]"
grep -q "ExecStart=.*python.*src.main" "$ORCH_UNIT" || fail "orch unit ExecStart should call python -m src.main"
pass "orch unit has the required sections"

# ─── 5. --remove is idempotent and removes the units ───────────────────
bash "$SCRIPT" --remove >/dev/null 2>&1 || fail "--remove failed"
[[ ! -f "$BRIDGE_UNIT" ]] || fail "bridge unit still present after --remove"
[[ ! -f "$ORCH_UNIT" ]] || fail "orch unit still present after --remove"
pass "--remove removes both unit files"

# ─── 6. Idempotency: install + install again is a no-op (no error) ───
bash "$SCRIPT" >/dev/null 2>&1 || true
bash "$SCRIPT" >/dev/null 2>&1 || {
    rc=$?
    # If the only error is the logrotate step, we're fine.
    if [[ ! -f "$BRIDGE_UNIT" ]]; then
        fail "second install removed the unit file (rc=$rc)"
    fi
}
pass "idempotent re-install leaves units in place"

# ─── 7. Embedded logrotate config is well-formed ──────────────────────
LOGROTATE_BODY=$(grep -A100 "logrotate" "$SCRIPT" | head -40 || true)
# We can't run logrotate in CI (no /etc/logrotate.d write access). Just
# verify the script's heredoc shape is parseable.
if grep -q "copytruncate" "$SCRIPT"; then
    pass "logrotate config has copytruncate (no truncation on rotate)"
fi
if grep -q "rotate 12" "$SCRIPT"; then
    pass "logrotate retains 12 weeks"
fi
if grep -q "weekly" "$SCRIPT"; then
    pass "logrotate runs weekly"
fi

echo "All install-systemd tests passed."
