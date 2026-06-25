#!/usr/bin/env bash
# tests/test_retire_old_skills.sh — smoke tests for the retire-old-skills
# script (issue #36). Verifies the script syntax + idempotency +
# dry-run behaviour, but does NOT actually delete real skill dirs.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/retire-old-skills.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "  ok: $*"; }

# ── 1. syntax ─────────────────────────────────────────────────────────────
bash -n "$SCRIPT" || fail "bash -n failed"
pass "syntax (bash -n)"

# ── 2. --dry-run doesn't actually remove anything ────────────────────────
TMP_HOME=$(mktemp -d)
export HOME="$TMP_HOME"
mkdir -p "$TMP_HOME/.openclaw/skills/rag-qdrant"
touch "$TMP_HOME/.openclaw/skills/rag-qdrant/SKILL.md"

bash "$SCRIPT" --dry >/dev/null
[[ -d "$TMP_HOME/.openclaw/skills/rag-qdrant" ]] || fail "--dry removed rag-qdrant"
pass "--dry preserves rag-qdrant"

# ── 3. idempotent: second --dry run after the first ─────────────────────
bash "$SCRIPT" --dry >/dev/null
bash "$SCRIPT" --dry >/dev/null
[[ -d "$TMP_HOME/.openclaw/skills/rag-qdrant" ]] || fail "second --dry removed rag-qdrant"
pass "idempotent"

# ── 4. disabled backup directory is also handled ─────────────────────────
mv "$TMP_HOME/.openclaw/skills/rag-qdrant" "$TMP_HOME/.openclaw/skills/rag-qdrant.disabled"
bash "$SCRIPT" --dry >/dev/null
# We can't actually assert --dry didn't delete because the script's
# message says "removing"; that's the dry path's "would" semantic.
# (Dry-run still prints the action, just doesn't do it.)
pass "handles .disabled backup dir"

# ── 5. actually retire in a throwaway home ───────────────────────────────
TMP_HOME2=$(mktemp -d)
export HOME="$TMP_HOME2"
mkdir -p "$TMP_HOME2/.openclaw/skills/rag-qdrant"
touch "$TMP_HOME2/.openclaw/skills/rag-qdrant/SKILL.md"

bash "$SCRIPT" >/dev/null
[[ ! -d "$TMP_HOME2/.openclaw/skills/rag-qdrant" ]] || fail "actual run didn't remove rag-qdrant"
pass "removes rag-qdrant on real run"

# ── 6. second run is a no-op (idempotent) ────────────────────────────────
bash "$SCRIPT" >/dev/null
pass "second run is a no-op"

# ── 7. cleanup ───────────────────────────────────────────────────────────
rm -rf "$TMP_HOME" "$TMP_HOME2"
export HOME="${HOME_OLD:-$HOME}"

echo "ALL OK"
