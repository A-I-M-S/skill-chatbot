# Upstream skill references (snapshots)

> These are **read-only snapshots** of the two existing skills this project depends on. They're committed to the repo so the opencode Plan agent can read them as in-project files (no external_directory permission needed).
>
> **Do not edit.** If the upstream skills change, re-snapshot with `scripts/snapshot-upstream.sh`.

| Snapshot | Source | Purpose |
|---|---|---|
| `rag-qdrant-SKILL.md` | `/root/.openclaw/skills/rag-qdrant/SKILL.md` | rag-qdrant agent adapter, ACL, caching, photo support |
| `rag-qdrant-README.md` | `/root/.openclaw/skills/rag-qdrant/README.md` | full API surface |
| `rag-qdrant-requirements.txt` | `/root/.openclaw/skills/rag-qdrant/requirements.txt` | pin compatible versions |
| `farm-tour-booking-SKILL.md` | `/root/.openclaw/workspace/admin/skills/farm-tour-booking/SKILL.md` | intent routing, edge cases, escalation |
| `booking_flow.py` | upstream scripts | CLI surface (op_new / op_list / op_edit / op_cancel) |
| `intent.py` | upstream scripts | rule-based intent classifier |
| `composio_outlook.py` | upstream scripts | Composio REST client |
| `booking_rules.yaml` | upstream config | location, hours, capacity, pricing, blackout, deposit |

## Refresh

```bash
./scripts/snapshot-upstream.sh
```
