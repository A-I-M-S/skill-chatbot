"""Thin subprocess wrapper around ``booking_flow.py``.

Calls the upstream ``farm-tour-booking`` skill's CLI as a subprocess so the
state (event store, cache) stays where the skill expects it. We don't
import the skill directly because:

- The orchestrator may not have the skill's venv activated.
- The skill's modules assume ``COMPOSIO_API_KEY`` is in the env at import
  time; we set that explicitly here.
- A subprocess crash in the skill doesn't take down the orchestrator.

Returns the parsed JSON-ish dict the skill emits (it prints a single
JSON line on stdout). Raises :class:`BookingSubprocessError` on
non-zero exit codes or stderr output.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Path to the upstream skill's CLI script. Defaults to the snapshot path;
# operator can override via SKILL_FARM_TOUR_BOOKING_PATH.
_DEFAULT_PATH = Path(
    "/root/.openclaw/workspace/admin/skills/farm-tour-booking/scripts/booking_flow.py"
)


class BookingSubprocessError(RuntimeError):
    """Raised when the upstream booking CLI fails."""


def booking_cli_path() -> Path:
    override = os.environ.get("SKILL_FARM_TOUR_BOOKING_PATH")
    return Path(override) if override else _DEFAULT_PATH


def _run(args: list[str], *, timeout: float = 15.0) -> dict[str, Any]:
    """Run ``python3 booking_flow.py <args>`` and parse the JSON reply.

    The skill prints a single JSON object to stdout (per its SKILL.md
    contract). Anything on stderr is treated as a soft warning unless
    the exit code is non-zero.
    """
    script = booking_cli_path()
    if not script.exists():
        raise BookingSubprocessError(f"booking_flow.py not found at {script}")
    cmd = ["python3", str(script), *args]
    env = os.environ.copy()
    # Make sure the skill's required env is set; if not, the upstream
    # CLI will raise its own error and we surface it.
    if "COMPOSIO_API_KEY" not in env:
        env["COMPOSIO_API_KEY"] = env.get("COMPOSIO_API_KEY", "")
    logger.debug("booking subprocess: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        raise BookingSubprocessError(f"booking_flow.py timed out after {timeout}s") from e
    if proc.returncode != 0:
        raise BookingSubprocessError(
            f"booking_flow.py exited {proc.returncode}: {proc.stderr.strip()[:500]}"
        )
    out = proc.stdout.strip()
    if not out:
        raise BookingSubprocessError(
            f"booking_flow.py returned empty stdout (stderr={proc.stderr.strip()[:200]})"
        )
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise BookingSubprocessError(f"booking_flow.py output is not JSON: {out[:200]}") from e


# ──────────────────────────────────────────────────────────────────────
# Public API used by the flow modules
# ──────────────────────────────────────────────────────────────────────


def list_events(from_iso: str, to_iso: str) -> list[dict[str, Any]]:
    """List events in [from_iso, to_iso]. Returns parsed JSON array."""
    result = _run(["list", "--from", from_iso, "--to", to_iso])
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "events" in result:
        return list(result["events"])
    return []


def new_draft(
    *,
    date: str,
    time: str,
    pax: int,
    contact_name: str | None = None,
    contact_email: str | None = None,
    contact_phone: str | None = None,
    org: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Run ``op_new`` WITHOUT ``--confirm`` and return the draft."""
    args = [
        "new",
        "--date",
        date,
        "--time",
        time,
        "--pax",
        str(pax),
    ]
    if contact_name:
        args += ["--contact-name", contact_name]
    if contact_email:
        args += ["--contact-email", contact_email]
    if contact_phone:
        args += ["--contact-phone", contact_phone]
    if org:
        args += ["--org", org]
    if notes:
        args += ["--notes", notes]
    return _run(args)


def new_commit(
    *,
    date: str,
    time: str,
    pax: int,
    contact_name: str | None = None,
    contact_email: str | None = None,
    contact_phone: str | None = None,
    org: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Run ``op_new --confirm`` and commit the event."""
    args = [
        "new",
        "--date",
        date,
        "--time",
        time,
        "--pax",
        str(pax),
        "--confirm",
    ]
    if contact_name:
        args += ["--contact-name", contact_name]
    if contact_email:
        args += ["--contact-email", contact_email]
    if contact_phone:
        args += ["--contact-phone", contact_phone]
    if org:
        args += ["--org", org]
    if notes:
        args += ["--notes", notes]
    return _run(args)


def cancel(event_id: str) -> dict[str, Any]:
    return _run(["cancel", event_id, "--confirm"])


def edit(
    event_id: str,
    *,
    date: str | None = None,
    time: str | None = None,
    pax: int | None = None,
) -> dict[str, Any]:
    """Edit an event. The upstream CLI's ``op_edit`` returns a draft on
    the first call; commit on the second call with the same args plus
    ``--confirm``."""
    args = ["edit", "--event-id", event_id]
    if date:
        args += ["--date", date]
    if time:
        args += ["--time", time]
    if pax is not None:
        args += ["--pax", str(pax)]
    return _run(args)


__all__ = [
    "BookingSubprocessError",
    "booking_cli_path",
    "cancel",
    "edit",
    "list_events",
    "new_commit",
    "new_draft",
]