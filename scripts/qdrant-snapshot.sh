#!/usr/bin/env bash
# Nightly Qdrant snapshot (issue #34).
#
# - Reads QDRANT_URL / QDRANT_API_KEY / QDRANT_COLLECTION from env (or
#   /etc/skill-chatbot.env when run as a systemd service).
# - POSTs {QDRANT_URL}/collections/{QDRANT_COLLECTION}/snapshots.
# - Lists existing snapshots and deletes any older than --retention-days
#   (default 7).
# - Logs every step to stderr (so journald captures it via the unit).
# - Exits non-zero on any failure so the systemd unit surfaces it via
#   `systemctl status`.
#
# Requires: bash 4+, curl, jq. jq is used only for the prune step
# (parsing ISO-8601 timestamps) — the create step works without it.
set -euo pipefail

RETENTION_DAYS="${RETENTION_DAYS:-7}"
SNAPSHOT_BASE_NAME="${SNAPSHOT_BASE_NAME:-nightly}"
DRY_RUN="${DRY_RUN:-0}"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

: "${QDRANT_URL:?QDRANT_URL is required}"
: "${QDRANT_API_KEY:?QDRANT_API_KEY is required}"
: "${QDRANT_COLLECTION:?QDRANT_COLLECTION is required}"

auth_args=(-H "api-key: ${QDRANT_API_KEY}")

# ---------------------------------------------------------------------------
# 1. Create snapshot
# ---------------------------------------------------------------------------
log "creating snapshot for collection '${QDRANT_COLLECTION}' at ${QDRANT_URL}"
create_resp=$(curl --fail-with-body --silent --show-error \
    -X POST "${auth_args[@]}" \
    "${QDRANT_URL}/collections/${QDRANT_COLLECTION}/snapshots" \
    -w '\n%{http_code}' \
    || die "snapshot create failed: ${create_resp:-<no response>}")
create_body=$(printf '%s\n' "$create_resp" | sed '$d')
create_status=$(printf '%s\n' "$create_resp" | tail -n1)
if [[ "$create_status" != "200" && "$create_status" != "201" && "$create_status" != "202" ]]; then
    die "snapshot create returned HTTP ${create_status}: ${create_body}"
fi

if ! command -v jq >/dev/null 2>&1; then
    log "snapshot created (no jq installed — skipping prune step)"
    log "DONE"
    exit 0
fi

snapshot_name=$(printf '%s' "$create_body" | jq -r '.result.name // empty')
if [[ -z "$snapshot_name" ]]; then
    die "snapshot create response missing .result.name: ${create_body}"
fi
log "created snapshot: ${snapshot_name}"

# ---------------------------------------------------------------------------
# 2. List snapshots
# ---------------------------------------------------------------------------
list_resp=$(curl --fail-with-body --silent --show-error \
    "${auth_args[@]}" \
    "${QDRANT_URL}/collections/${QDRANT_COLLECTION}/snapshots" \
    -w '\n%{http_code}')
list_body=$(printf '%s\n' "$list_resp" | sed '$d')
list_status=$(printf '%s\n' "$list_resp" | tail -n1)
if [[ "$list_status" != "200" ]]; then
    die "snapshot list returned HTTP ${list_status}: ${list_body}"
fi

# ---------------------------------------------------------------------------
# 3. Prune snapshots older than RETENTION_DAYS
# ---------------------------------------------------------------------------
cutoff_epoch=$(date -u -d "-${RETENTION_DAYS} days" +%s 2>/dev/null || \
               date -u -v "-${RETENTION_DAYS}d" +%s)
log "pruning snapshots older than ${RETENTION_DAYS} days (epoch ${cutoff_epoch})"

pruned=0
kept=0
while IFS=$'\t' read -r name created_at; do
    [[ -z "$name" ]] && continue
    # Qdrant returns timestamps as ISO-8601 (e.g. "2026-06-24T18:00:00Z").
    # date -d accepts that on GNU; on macOS we'd need -j -f. We run on
    # Linux only — the systemd unit guarantees it.
    snap_epoch=$(date -u -d "$created_at" +%s 2>/dev/null || echo 0)
    if [[ "$snap_epoch" -lt "$cutoff_epoch" ]]; then
        if [[ "$DRY_RUN" == "1" ]]; then
            log "would delete: ${name} (${created_at})"
        else
            log "deleting: ${name} (${created_at})"
            del_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
                -X DELETE "${auth_args[@]}" \
                "${QDRANT_URL}/collections/${QDRANT_COLLECTION}/snapshots/${name}")
            if [[ "$del_status" == "200" || "$del_status" == "204" ]]; then
                pruned=$((pruned + 1))
            else
                log "WARNING: delete ${name} returned HTTP ${del_status}"
            fi
        fi
    else
        kept=$((kept + 1))
    fi
done < <(printf '%s' "$list_body" | jq -r '.result[] | [.name, .creation_time // .created_at // ""] | @tsv')

log "prune complete: kept=${kept} pruned=${pruned}"
log "DONE"
