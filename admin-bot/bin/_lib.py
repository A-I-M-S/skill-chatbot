"""Shared helpers for admin-bot CLI scripts.

Importable as a Python module (for tests) and used by every `bin/<cmd>`
script. Loaded with ``sys.path.insert(0, str(Path(__file__).parent))``
so the scripts don't need a package install.

Public API:

- :func:`load_env` — read ``.env`` and return a typed settings bundle.
- :func:`require_admin` — refuse senders not in ``ADMIN_TELEGRAM_IDS``.
- :func:`api_call` — make one HTTP call to the orchestrator admin API
  with the right auth headers and JSON I/O.
- :func:`format_event_row` / :func:`format_event_list` / :func:`format_acl_table`
  — Telegram Markdown helpers.
- :func:`state_get` / :func:`state_set` / :func:`state_clear` — tiny
  per-sender in-process state file (only used to remember a pending
  ``/bookings`` date).
- :func:`die` — print to stderr and exit.
- :func:`parse_sender_id` — pull ``--sender-id <int>`` from argv.

Each bin script is a thin wrapper around these helpers.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    api_base: str          # e.g. "http://127.0.0.1:7789/admin"
    http_token: str        # value of ADMIN_HTTP_TOKEN
    admin_ids: tuple[int, ...]


def load_env(env_path: Path | None = None) -> Settings:
    """Read ``.env`` into a :class:`Settings`.

    Lookup order:

    1. ``env_path`` argument (used by tests + callers that want to
       point at a specific file).
    2. ``$ADMIN_BOT_ENV`` env var (a path override).
    3. ``<skill>/.env`` — i.e. ``admin-bot/.env`` next to this file's
       ``bin/`` directory. Production install layout.
    4. ``./.env`` in the current working directory. Works whether the
       skill is installed via symlink (step 3) or run in-place from a
       checkout (step 4).

    Implemented with stdlib only — no ``python-dotenv`` dep. Lines look
    like ``KEY=value``; comments and blank lines are skipped; values may
    be optionally quoted.
    """
    candidates: list[Path] = []
    if env_path is not None:
        candidates.append(env_path)
    override = os.environ.get("ADMIN_BOT_ENV")
    if override:
        candidates.append(Path(override))
    candidates.append(Path(__file__).resolve().parent.parent / ".env")
    candidates.append(Path.cwd() / ".env")

    raw: dict[str, str] = {}
    for cand in candidates:
        if cand.exists():
            for line in cand.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                raw[k] = v
            break  # first match wins

    # Allow real env to override (so an OpenClaw adapter can inject).
    for k in ("ADMIN_API_BASE", "ADMIN_HTTP_TOKEN", "ADMIN_TELEGRAM_IDS"):
        if k in os.environ:
            raw[k] = os.environ[k]

    api_base = raw.get("ADMIN_API_BASE", "").rstrip("/")
    if not api_base:
        die("ADMIN_API_BASE is not set (expected e.g. http://127.0.0.1:7789/admin)")

    admin_ids: tuple[int, ...] = tuple(
        int(x.strip()) for x in raw.get("ADMIN_TELEGRAM_IDS", "").split(",") if x.strip()
    )
    if not admin_ids:
        die("ADMIN_TELEGRAM_IDS is not set or empty")

    return Settings(
        api_base=api_base,
        http_token=raw.get("ADMIN_HTTP_TOKEN", ""),
        admin_ids=admin_ids,
    )


# ---------------------------------------------------------------------------
# Permission gate
# ---------------------------------------------------------------------------


def require_admin(sender_id: int, settings: Settings) -> None:
    """Refuse senders not in ``ADMIN_TELEGRAM_IDS``.

    Mirrors the orchestrator's defense-in-depth check on the server side.
    Exits with code ``2`` so the OpenClaw adapter can map it to a single
    user-facing refusal message.
    """
    if sender_id not in settings.admin_ids:
        die("Refused: you're not an admin.", code=2)


# ---------------------------------------------------------------------------
# API client (stdlib urllib — no requests dep at runtime)
# ---------------------------------------------------------------------------


def api_call(
    settings: Settings,
    sender_id: int,
    method: str,
    route: str,
    *,
    body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any] | str]:
    """Make one HTTP call to ``${ADMIN_API_BASE}/<route>``.

    Returns ``(status_code, response_body)``. ``response_body`` is the
    parsed JSON if the response is JSON, else the raw text.
    """
    url = f"{settings.api_base}/{route.lstrip('/')}"
    if query:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(query)}"

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Admin-Token": settings.http_token,
            "X-Admin-Telegram-Id": str(sender_id),
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return _parse(resp.status, raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        return _parse(exc.code, raw)
    except urllib.error.URLError as exc:
        die(f"Could not reach the orchestrator: {exc.reason}", code=3)


def _parse(status: int, raw: bytes) -> tuple[int, dict[str, Any] | str]:
    if not raw:
        return status, ""
    try:
        return status, json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return status, raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Reply formatters (Telegram Markdown)
# ---------------------------------------------------------------------------


def format_event_row(ev: dict[str, Any]) -> str:
    """One event → one Markdown row for the ``/bookings`` reply."""
    subject = ev.get("subject") or "(no subject)"
    start = ev.get("start") or "?"
    end = ev.get("end") or "?"
    location = ev.get("location") or ""
    eid = ev.get("id") or ""
    parts = [f"• *{_md_escape(subject)}*", f"  {start} → {end}"]
    if location:
        parts.append(f"  📍 {_md_escape(location)}")
    if eid:
        parts.append(f"  id: `{_md_escape(str(eid))}`")
    return "\n".join(parts)


def format_event_list(date_str: str, events: list[dict[str, Any]]) -> str:
    if not events:
        return f"No bookings on *{date_str}*."
    head = f"*Bookings on {date_str}* — {len(events)} event(s):\n"
    body = "\n\n".join(format_event_row(e) for e in events)
    return head + body


def format_acl_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "ACL table is empty."
    lines = ["*Access control* — one row per source:\n"]
    for r in rows:
        src = r.get("source", "?")
        ids = r.get("allowed_telegram_ids") or r.get("telegram_ids") or []
        public = bool(r.get("public"))
        if public:
            who = "_public_ (anyone)"
        elif ids:
            who = ", ".join(f"`{i}`" for i in ids)
        else:
            who = "_nobody_"
        lines.append(f"• `{_md_escape(str(src))}` → {who}")
    return "\n".join(lines)


def _md_escape(s: str) -> str:
    """Escape Telegram Markdown special chars in user-controlled text."""
    for ch in ("*", "_", "`", "["):
        s = s.replace(ch, f"\\{ch}")
    return s


# ---------------------------------------------------------------------------
# Per-sender state (only used by /bookings to wait for a date)
# ---------------------------------------------------------------------------


def _state_path() -> Path:
    return Path(tempfile.gettempdir()) / f"admin-bot-state-{os.getpid()}.json"


def state_get(sender_id: int) -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get(str(sender_id), {})
    except (ValueError, OSError):
        return {}


def state_set(sender_id: int, value: dict[str, Any]) -> None:
    p = _state_path()
    cur: dict[str, Any] = {}
    if p.exists():
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            cur = {}
    cur[str(sender_id)] = value
    # atomic write
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur), encoding="utf-8")
    tmp.replace(p)


def state_clear(sender_id: int) -> None:
    state_set(sender_id, {})


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def die(msg: str, code: int = 1) -> NoReturn:
    print(msg, file=sys.stderr)
    sys.exit(code)


def parse_sender_id(argv: list[str]) -> tuple[int, list[str]]:
    """Pull ``--sender-id <int>`` out of argv; return (sender_id, remaining).

    Raises ``SystemExit(2)`` if missing or not an int — the OpenClaw
    adapter should always pass this. No fallback to "default admin"
    because that would silently let anyone through.
    """
    if "--sender-id" not in argv:
        die("--sender-id <int> is required (Telegram user id of the sender)", code=2)
    i = argv.index("--sender-id")
    try:
        sender_id = int(argv[i + 1])
    except (IndexError, ValueError):
        die("--sender-id must be followed by an integer", code=2)
    remaining = argv[:i] + argv[i + 2:]
    return sender_id, remaining


__all__ = [
    "Settings",
    "load_env",
    "require_admin",
    "api_call",
    "format_event_row",
    "format_event_list",
    "format_acl_table",
    "state_get",
    "state_set",
    "state_clear",
    "die",
    "parse_sender_id",
]
