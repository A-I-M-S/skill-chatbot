"""Thin subprocess wrapper around ``booking_flow.py``.

Runs the vendored booking CLI (``orchestrator/booking_cli/booking_flow.py``,
overridable via ``SKILL_FARM_TOUR_BOOKING_PATH``) as a subprocess rather than
importing it, so a crash in the CLI can't take down the orchestrator and its
Composio/Outlook state stays isolated. It runs under the orchestrator's own
interpreter (``sys.executable``) so the CLI's deps resolve, inheriting the
process env (``COMPOSIO_API_KEY`` etc.).

Returns the parsed JSON dict the CLI prints as a single line on stdout.
Raises :class:`BookingSubprocessError` on a non-zero exit or unparseable output.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Path to the booking CLI script. Defaults to the copy vendored into this
# repo (self-contained, no external openclaw skill required); an operator can
# override via SKILL_FARM_TOUR_BOOKING_PATH to point at a deployed skill.
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "booking_cli" / "booking_flow.py"


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
    # Run with the same interpreter as the orchestrator (its venv) so the
    # CLI's deps (pyyaml, python-dateutil, requests) resolve. A bare
    # "python3" could be a different interpreter without those installed.
    cmd = [sys.executable, str(script), *args]
    env = os.environ.copy()
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
    # CLI: `cancel --event-id <id> --confirm` (cancel requires --confirm to commit).
    return _run(["cancel", "--event-id", event_id, "--confirm"])


def edit(
    event_id: str,
    *,
    date: str | None = None,
    time: str | None = None,
    pax: int | None = None,
) -> dict[str, Any]:
    """Edit an event and commit it.

    The CLI's ``op_edit`` returns a draft without ``--confirm`` and commits
    with it. The edit flow only calls this at its confirm step (the user has
    already approved), so we always pass ``--confirm``."""
    args = ["edit", "--event-id", event_id]
    if date:
        args += ["--date", date]
    if time:
        args += ["--time", time]
    if pax is not None:
        args += ["--pax", str(pax)]
    args += ["--confirm"]
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
