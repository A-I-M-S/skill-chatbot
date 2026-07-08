# booking_cli — vendored farm-tour booking CLI

`booking_flow.py` + `composio_outlook.py` are vendored from the upstream
`farm-tour-booking` skill (snapshot in `references/upstream/`) so the
orchestrator's booking path is self-contained and deterministic. The
orchestrator invokes `booking_flow.py` as a subprocess via
`src/booking_subprocess.py` (which defaults to this directory).

- **Do not hand-edit** — refresh from upstream with `scripts/snapshot-upstream.sh`
  then re-copy, so changes stay traceable to the source skill.
- Runtime deps: `pyyaml`, `python-dateutil`, `requests` (declared in
  `orchestrator/pyproject.toml`), plus `COMPOSIO_API_KEY` in the env.
- Reads booking rules from `BOOKING_RULES_PATH` (point it at
  `orchestrator/data/booking_rules.yaml`).
