#!/usr/bin/env bash
# Convenience: start both daemons in the foreground, each in its own pane-less
# background job. Ctrl-C kills both.
#
# Use this for a local dev loop when you don't want to chase two terminals.
# Production runs them as systemd --user units; see `make install-svc`.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cleanup() {
  local code=$?
  trap - EXIT INT TERM
  if [[ -n "${bridge_pid:-}" ]] && kill -0 "${bridge_pid}" 2>/dev/null; then
    kill "${bridge_pid}" 2>/dev/null || true
  fi
  if [[ -n "${orch_pid:-}" ]] && kill -0 "${orch_pid}" 2>/dev/null; then
    kill "${orch_pid}" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  exit "${code}"
}
trap cleanup EXIT INT TERM

cd "${repo_root}"
make bridge-dev &
bridge_pid=$!
make orch-dev &
orch_pid=$!

echo "bridge pid=${bridge_pid}  orchestrator pid=${orch_pid}  (Ctrl-C to stop both)"
wait -n "${bridge_pid}" "${orch_pid}"
