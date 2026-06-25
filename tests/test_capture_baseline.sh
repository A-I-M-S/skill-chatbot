#!/usr/bin/env bash
# tests/test_capture_baseline.sh — smoke tests for the baseline
# capture script (issue #35). Runs without a real Qdrant or live
# OpenClaw: points the script at a temp env file, a temp Qdrant
# stub URL (we don't actually need Qdrant to respond — the script
# tolerates unreachable endpoints), and inspects the Markdown.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/capture-baseline.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "  ok: $*"; }

# ── 1. syntax ─────────────────────────────────────────────────────────────
bash -n "$SCRIPT" || fail "bash -n failed"
pass "syntax (bash -n)"

# ── 2. produces Markdown with the expected sections ──────────────────────
TMP_ENV=$(mktemp)
trap 'rm -f "$TMP_ENV" "$TMP_OUT"' EXIT
TMP_OUT=$(mktemp)

cat > "$TMP_ENV" <<EOF
# Test env file
ADMIN_TELEGRAM_IDS=920567169
QDRANT_URL=http://127.0.0.1:9999   # intentionally unreachable
QDRANT_API_KEY=test-key-do-not-use
QDRANT_COLLECTION=test_collection
INFERENCE_BASE_URL=http://127.0.0.1:9998
COMPOSIO_API_KEY=ak_test
WA_BRIDGE_TOKEN=test-token
ADMIN_CONTACT_NUMBER=+60123456789
WA_NOTIFY=+60123456789,+60198765432
EOF

# QDRANT_API_KEY sha256[0:8] = ?
EXPECTED_HASH=$(printf '%s' 'test-key-do-not-use' | sha256sum | cut -c1-8)

ENV_FILE="$TMP_ENV" OUT="$TMP_OUT" bash "$SCRIPT" >/dev/null

[[ -s "$TMP_OUT" ]] || fail "output is empty"
grep -q "^# Migration baseline — captured " "$TMP_OUT" || fail "missing title"
grep -q "^## 1. baadminbot state" "$TMP_OUT" || fail "missing §1"
grep -q "^## 2. Qdrant state" "$TMP_OUT" || fail "missing §2"
grep -q "^## 3. WhatsApp state" "$TMP_OUT" || fail "missing §3"
grep -q "^## 4. Env keys present" "$TMP_OUT" || fail "missing §4"
grep -q "^## 5. External services reachability" "$TMP_OUT" || fail "missing §5"
grep -q "^## 6. Notes" "$TMP_OUT" || fail "missing §6"
pass "all 6 sections present"

# ── 3. hashes secrets, never prints them ─────────────────────────────────
grep -q "$EXPECTED_HASH" "$TMP_OUT" || fail "expected sha256[0:8] not found"
grep -q "test-key-do-not-use" "$TMP_OUT" && fail "raw QDRANT_API_KEY leaked into output"
grep -q "test-token" "$TMP_OUT" && fail "raw WA_BRIDGE_TOKEN leaked into output"
pass "secrets hashed, not printed"

# ── 4. env keys captured as names only ───────────────────────────────────
grep -q "ADMIN_TELEGRAM_IDS" "$TMP_OUT" || fail "ADMIN_TELEGRAM_IDS not in env keys section"
grep -q "QDRANT_API_KEY" "$TMP_OUT" || fail "QDRANT_API_KEY not in env keys section"
if grep -qE "=920567169|=test-key-do-not-use|=test-token|=ak_test" "$TMP_OUT"; then
    fail "env key VALUES leaked into §4 — see: $(grep -nE '=920567169|=test-key-do-not-use|=test-token|=ak_test' $TMP_OUT)"
fi
pass "env key names captured, values redacted"

# ── 5. ADMIN_CONTACT_NUMBER is masked ────────────────────────────────────
grep -q "Admin contact" "$TMP_OUT" && \
    ! grep -q "+60123456789\` (masked)" "$TMP_OUT" || \
    fail "ADMIN_CONTACT_NUMBER not masked — full value visible"
pass "phone numbers masked"

# ── 6. unreachable Qdrant doesn't crash the script ─────────────────────
# (the script tolerates curl failures)
grep -q "unreachable" "$TMP_OUT" || grep -q "skipping live read" "$TMP_OUT" || \
    note "Qdrant was unreachable as expected; script handled gracefully"
pass "tolerates unreachable Qdrant"

echo "ALL OK"
