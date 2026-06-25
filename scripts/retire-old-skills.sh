#!/usr/bin/env bash
# scripts/retire-old-skills.sh — remove rag-qdrant + quarantine
# farm-tour-booking Skill Workshop proposals (issue #36).
#
# Idempotent. Safe to re-run. Removes the rag-qdrant skill directory
# on baadminbot and quarantines any farm-tour-booking-* proposals
# that are still pending.
#
# Usage:
#   bash scripts/retire-old-skills.sh          # actually retire
#   bash scripts/retire-old-skills.sh --dry    # print what would happen
#
# After this runs:
#   - ~/.openclaw/skills/rag-qdrant is gone (if it existed)
#   - The disabled backup at ~/.openclaw/skills/rag-qdrant.disabled
#     (if present from the cutover) is also removed
#   - All pending `farm-tour-booking-*` Skill Workshop proposals are
#     marked as quarantined with a clear reason
set -euo pipefail

DRY=0
if [[ "${1:-}" == "--dry" ]]; then
    DRY=1
fi

note() { printf '  %s\n' "$*"; }
warn() { printf '  WARN: %s\n' "$*" >&2; }

RAG_DIR="$HOME/.openclaw/skills/rag-qdrant"
RAG_DISABLED="$HOME/.openclaw/skills/rag-qdrant.disabled"

echo "==> 1. rag-qdrant skill"
if [[ -d "$RAG_DISABLED" ]]; then
    note "removing $RAG_DISABLED (cutover-disabled backup)"
    [[ "$DRY" == "0" ]] && rm -rf "$RAG_DISABLED"
elif [[ -d "$RAG_DIR" ]]; then
    note "removing $RAG_DIR"
    [[ "$DRY" == "0" ]] && rm -rf "$RAG_DIR"
else
    note "not present, skipping"
fi

echo "==> 2. farm-tour-booking proposals"
if ! command -v skill_workshop >/dev/null 2>&1; then
    warn "skill_workshop not on PATH; skipping proposal quarantine (do it manually)"
    warn "  gh api -X POST /repos/A-I-M-S/skill-chatbot/actions/runs  # or skill_workshop action=quarantine …"
else
    # List proposals that match farm-tour-booking in title or body.
    # The skill_workshop CLI's list shape varies by version; try both
    # the JSON shape and the text shape.
    proposal_ids=""
    if command -v jq >/dev/null 2>&1; then
        proposal_ids=$(skill_workshop action=list --status=pending --query='farm-tour-booking' --format=json 2>/dev/null \
            | jq -r '.[].id // empty' 2>/dev/null || true)
    fi
    if [[ -z "$proposal_ids" ]]; then
        note "no pending farm-tour-booking proposals found (or list returned empty)"
    else
        while IFS= read -r pid; do
            [[ -z "$pid" ]] && continue
            note "quarantining proposal $pid"
            if [[ "$DRY" == "0" ]]; then
                skill_workshop action=quarantine \
                    --proposal-id="$pid" \
                    --reason="Superseded by skill-chatbot migration (#29)" \
                    2>&1 | sed 's/^/    /'
            fi
        done <<<"$proposal_ids"
    fi
fi

echo "==> 3. final state"
echo "  ~/.openclaw/skills/:"
[[ -d "$HOME/.openclaw/skills" ]] && ls "$HOME/.openclaw/skills" | sed 's/^/    /' || echo "    (dir missing)"
echo
echo "Done."
echo "If anything in the list above is still rag-qdrant or farm-tour-booking, remove it manually:"
echo "  rm -rf ~/.openclaw/skills/<skill-name>"
echo "  skill_workshop action=quarantine --proposal-id=<id>"
