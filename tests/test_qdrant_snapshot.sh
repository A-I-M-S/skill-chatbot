#!/usr/bin/env bash
# tests/test_qdrant_snapshot.sh — smoke tests for the Qdrant snapshot
# script (issue #34). Runs without root or a real Qdrant.
#
# Strategy: spin up a tiny Python http.server that pretends to be
# Qdrant, point the script at it via env, assert it creates + lists +
# prunes snapshots correctly. Then assert the systemd unit files parse.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/qdrant-snapshot.sh"
TIMER="$REPO_ROOT/systemd/skill-chatbot-qdrant-snapshot.timer"
SERVICE="$REPO_ROOT/systemd/skill-chatbot-qdrant-snapshot.service"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "  ok: $*"; }
note() { echo "  note: $*"; }

# ─── 1. syntax ──────────────────────────────────────────────────────────
bash -n "$SCRIPT" || fail "bash -n failed on $SCRIPT"
pass "syntax (bash -n)"

# ─── 2. systemd unit files parse ────────────────────────────────────────
command -v systemd-analyze >/dev/null 2>&1 && \
    systemd-analyze verify "$SERVICE" "$TIMER" >/dev/null 2>&1 && \
    pass "systemd-analyze verify" || \
    note "systemd-analyze not available (skipping unit parse check)"

# ─── 3. missing-env fails fast ──────────────────────────────────────────
out=$(env -i PATH=/usr/bin:/bin bash "$SCRIPT" 2>&1 || true)
if ! grep -q "QDRANT_URL is required" <<<"$out"; then
    fail "script should refuse without QDRANT_URL; got: $out"
fi
pass "missing env refused"

# ─── 4. end-to-end against a fake Qdrant ────────────────────────────────
# Tiny Python http.server that responds to:
#   POST /collections/<name>/snapshots     → {result:{name:"<now>"}}
#   GET  /collections/<name>/snapshots     → list of seed snapshots
#   DELETE /collections/<name>/snapshots/<name> → 200
FAKE_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
TMP_STATE=$(mktemp -d)
trap 'rm -rf "$TMP_STATE"; kill $FAKE_PID 2>/dev/null || true' EXIT

cat >"$TMP_STATE/fake_qdrant.py" <<'PY'
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE_FILE = os.environ["FAKE_STATE_FILE"]
PORT = int(os.environ["FAKE_PORT"])
COLLECTION = os.environ["QDRANT_COLLECTION"]

def load_state():
    if not os.path.exists(STATE_FILE):
        return []
    return json.loads(open(STATE_FILE).read())

def save_state(snaps):
    open(STATE_FILE, "w").write(json.dumps(snaps))

class H(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass
    def _body(self):
        n = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(n) if n else b""
    def _send(self, body, status=200):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_POST(self):
        if self.path == f"/collections/{COLLECTION}/snapshots":
            name = f"nightly-{int(time.time()*1000)}"
            snaps = load_state()
            snaps.append({"name": name, "creation_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            save_state(snaps)
            self._send({"result": {"name": name, "creation_time": snaps[-1]["creation_time"]}})
        else:
            self._send({"error": "not found"}, 404)
    def do_GET(self):
        if self.path == f"/collections/{COLLECTION}/snapshots":
            self._send({"result": load_state()})
        else:
            self._send({"error": "not found"}, 404)
    def do_DELETE(self):
        prefix = f"/collections/{COLLECTION}/snapshots/"
        if self.path.startswith(prefix):
            name = self.path[len(prefix):]
            snaps = [s for s in load_state() if s["name"] != name]
            save_state(snaps)
            self._send({"result": {"name": name}}, 200)
        else:
            self._send({"error": "not found"}, 404)

ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
PY

# Seed the fake Qdrant with two "old" snapshots and one "new" one.
NOW=$(date -u +%s)
OLD1_TS=$(date -u -d "-10 days" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-10d +%Y-%m-%dT%H:%M:%SZ)
OLD2_TS=$(date -u -d "-9 days" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-9d +%Y-%m-%dT%H:%M:%SZ)
NEW_TS=$(date -u -d "-1 day" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-1d +%Y-%m-%dT%H:%M:%SZ)
echo "[$(date -u +%H:%M:%S)] seeded" >&2
cat >"$TMP_STATE/seed.json" <<JSON
[
  {"name": "old-snap-1", "creation_time": "${OLD1_TS}"},
  {"name": "old-snap-2", "creation_time": "${OLD2_TS}"},
  {"name": "recent-snap", "creation_time": "${NEW_TS}"}
]
JSON
mv "$TMP_STATE/seed.json" "$TMP_STATE/snapshots.json"
echo "[$(date -u +%H:%M:%S)] seeded: $(cat $TMP_STATE/snapshots.json | python3 -c 'import json,sys; print([s["name"] for s in json.load(sys.stdin)])')" >&2

QDRANT_URL="http://127.0.0.1:${FAKE_PORT}" \
QDRANT_API_KEY="test-key" \
QDRANT_COLLECTION="test_coll" \
FAKE_STATE_FILE="$TMP_STATE/snapshots.json" \
FAKE_PORT="$FAKE_PORT" \
python3 "$TMP_STATE/fake_qdrant.py" &
FAKE_PID=$!

# Wait for the fake server to come up.
for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -s "http://127.0.0.1:${FAKE_PORT}/collections/test_coll/snapshots" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done
echo "[$(date -u +%H:%M:%S)] fake up" >&2

# Run the real script against the fake.
output=$(QDRANT_URL="http://127.0.0.1:${FAKE_PORT}" \
         QDRANT_API_KEY="test-key" \
         QDRANT_COLLECTION="test_coll" \
         RETENTION_DAYS=7 \
         bash "$SCRIPT" 2>&1) || fail "script exited non-zero: $output"
echo "---- script output ----"
echo "$output"
echo "-----------------------"

# Assertions.
echo "$output" | grep -q "creating snapshot" || fail "missing 'creating snapshot' log line"
echo "$output" | grep -q "prune complete: kept=2 pruned=2" || fail "expected kept=2 pruned=2 (new + recent kept, 2 old pruned)"
echo "$output" | grep -q "DONE" || fail "missing DONE line"

# Verify the fake's state: only the recent snapshot + the newly created one remain.
remaining=$(curl -s "http://127.0.0.1:${FAKE_PORT}/collections/test_coll/snapshots" \
            | python3 -c 'import json,sys; print(",".join(sorted(s["name"] for s in json.load(sys.stdin)["result"])))')
echo "remaining: $remaining"
case "$remaining" in
    *recent-snap*) ;;
    *) fail "recent-snap should still exist; got: $remaining" ;;
esac
case "$remaining" in
    *old-snap-1*|*old-snap-2*) fail "old snapshots should have been pruned; got: $remaining" ;;
esac
pass "create + prune end-to-end"

# ─── 5. DRY_RUN=1 doesn't actually delete ───────────────────────────────
# Reset state with two old snapshots.
echo "[$(date -u +%H:%M:%S)] dry-run reset" >&2
cat >"$TMP_STATE/snapshots.json" <<JSON
[
  {"name": "old-a", "creation_time": "${OLD1_TS}"},
  {"name": "old-b", "creation_time": "${OLD2_TS}"}
]
JSON

output=$(QDRANT_URL="http://127.0.0.1:${FAKE_PORT}" \
         QDRANT_API_KEY="test-key" \
         QDRANT_COLLECTION="test_coll" \
         RETENTION_DAYS=7 \
         DRY_RUN=1 \
         bash "$SCRIPT" 2>&1) || fail "dry-run exited non-zero"
echo "$output" | grep -q "would delete" || fail "DRY_RUN=1 should say 'would delete', not 'deleting'"
remaining=$(curl -s "http://127.0.0.1:${FAKE_PORT}/collections/test_coll/snapshots" \
            | python3 -c 'import json,sys; print(",".join(sorted(s["name"] for s in json.load(sys.stdin)["result"])))')
case "$remaining" in
    *old-a*old-b*) pass "DRY_RUN=1 did not delete" ;;
    *) fail "DRY_RUN=1 should leave snapshots in place; got: $remaining" ;;
esac

echo "ALL OK"
