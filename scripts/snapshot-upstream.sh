#!/usr/bin/env bash
# Re-snapshot the upstream skill files into references/upstream/.
# Use when the upstream skills change and we want the plan agent to see
# the current interface. Safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO_ROOT/references/upstream"
mkdir -p "$DEST"

cp /root/.openclaw/skills/rag-qdrant/SKILL.md              "$DEST/rag-qdrant-SKILL.md"
cp /root/.openclaw/skills/rag-qdrant/README.md             "$DEST/rag-qdrant-README.md"
cp /root/.openclaw/skills/rag-qdrant/requirements.txt      "$DEST/rag-qdrant-requirements.txt"
cp /root/.openclaw/workspace/admin/skills/farm-tour-booking/SKILL.md        "$DEST/farm-tour-booking-SKILL.md"
cp /root/.openclaw/workspace/admin/skills/farm-tour-booking/scripts/booking_flow.py   "$DEST/booking_flow.py"
cp /root/.openclaw/workspace/admin/skills/farm-tour-booking/scripts/intent.py         "$DEST/intent.py"
cp /root/.openclaw/workspace/admin/skills/farm-tour-booking/scripts/composio_outlook.py "$DEST/composio_outlook.py"
cp /root/.openclaw/workspace/admin/skills/farm-tour-booking/config/booking_rules.yaml  "$DEST/booking_rules.yaml"

echo "Snapshotted $(ls "$DEST" | wc -l) files into $DEST"
