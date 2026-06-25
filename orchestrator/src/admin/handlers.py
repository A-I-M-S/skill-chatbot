"""Admin route handlers.

Each handler returns ``(status_code, body_dict)``. The dispatcher in
:mod:`src.admin` does auth + JSON parsing + error mapping; handlers
focus on the business logic and raise the bare minimum of typed
exceptions (:class:`ValueError`, :class:`PatchError`).

Imports of the optional upstream libraries (``rag_qdrant``,
``composio_outlook``) are lazy so unit tests can monkeypatch the
``src.admin.handlers`` module-level helpers without needing the
packages installed.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .yaml_patch import apply_patch

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /admin/ingest
# ---------------------------------------------------------------------------


_INGEST_SOURCES = ("path", "url", "pdf", "md")


def handle_ingest(body: dict[str, Any], *, admin: Any) -> tuple[int, dict[str, Any]]:
    """Ingest a document into Qdrant with per-chunk ACL.

    Body::

        {
          "source_type": "path" | "url" | "pdf" | "md",
          "target": "<absolute path or URL>",
          "telegram_id_acl": <int> | "public"
        }

    Default ACL = ``admin.admin_telegram_ids`` (i.e. only admins can read).
    Override with ``telegram_id_acl``: an int grants that one user,
    ``"public"`` makes the chunks readable by anyone.

    For v0 we support the ``"path"`` source_type only (calls
    ``rag_qdrant.ingest_file``). The other three return 501 with a clear
    message — issue #30 says ingest a single local file is enough for the
    admin sub-app MVP.
    """
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    source_type = str(body.get("source_type", "")).strip()
    if source_type not in _INGEST_SOURCES:
        raise ValueError(f"source_type must be one of {_INGEST_SOURCES}, got {source_type!r}")
    target = body.get("target")
    if not target or not isinstance(target, str):
        raise ValueError("target (string) is required")

    public, ids = _resolve_acl(body.get("telegram_id_acl"), admin)

    if source_type == "path":
        return _ingest_path(Path(target), public=public, ids=ids)
    if source_type == "md":
        return _ingest_path(Path(target), public=public, ids=ids, force_suffix=".md")
    if source_type == "pdf":
        return _ingest_path(Path(target), public=public, ids=ids, force_suffix=".pdf")
    # source_type == "url" not implemented in v0
    raise ValueError(f"source_type {source_type!r} not implemented yet (path/md/pdf only)")


def _resolve_acl(raw: Any, admin: Any) -> tuple[bool, list[int]]:
    """Return ``(public, telegram_ids)`` for the ingest payload.

    Rules:

    - ``None`` or missing → admin-only (default).
    - ``"public"`` → public=True, ids=[].
    - int → public=False, ids=[int].
    - anything else → ValueError.
    """
    if raw is None:
        return False, list(admin.admin_telegram_ids)
    if isinstance(raw, str):
        if raw.strip().lower() == "public":
            return True, []
        try:
            return False, [int(raw)]
        except ValueError as exc:
            raise ValueError(
                f"telegram_id_acl must be an int or 'public', got string {raw!r}"
            ) from exc
    if isinstance(raw, bool):
        raise ValueError("telegram_id_acl must be int or 'public' (not a bool)")
    if isinstance(raw, int):
        return False, [int(raw)]
    raise ValueError(f"telegram_id_acl must be int or 'public', got {raw!r}")


def _ingest_path(
    path: Path, *, public: bool, ids: list[int], force_suffix: str | None = None
) -> tuple[int, dict[str, Any]]:
    if not path.exists():
        raise ValueError(f"file not found: {path}")
    if force_suffix is not None and path.suffix.lower() != force_suffix:
        raise ValueError(
            f"{force_suffix} source requires a {force_suffix} file (got {path.suffix!r})"
        )
    try:
        from rag_qdrant import ingest_file as _rag_ingest_file
    except ImportError as exc:  # pragma: no cover - only hits when rag_qdrant missing
        raise ValueError(f"rag_qdrant package is not installed; cannot ingest. ({exc})") from exc

    allowed = [] if public else [int(x) for x in ids]
    chunks = _rag_ingest_file(path, source=path.stem, allowed_telegram_ids=allowed)
    return 200, {
        "ok": True,
        "source": path.stem,
        "chunks": int(chunks),
        "acl": {"public": public, "telegram_ids": list(ids)},
    }


# ---------------------------------------------------------------------------
# /admin/grant  +  /admin/revoke
# ---------------------------------------------------------------------------


def handle_grant(body: dict[str, Any], *, admin: Any) -> tuple[int, dict[str, Any]]:
    return _grant_or_revoke(body, admin, grant=True)


def handle_revoke(body: dict[str, Any], *, admin: Any) -> tuple[int, dict[str, Any]]:
    return _grant_or_revoke(body, admin, grant=False)


def _grant_or_revoke(
    body: dict[str, Any], admin: Any, *, grant: bool
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    source = body.get("source")
    if not source or not isinstance(source, str):
        raise ValueError("source (string) is required")
    telegram_id = _coerce_telegram_id(body)
    try:
        from rag_qdrant import grant_access, revoke_access
    except ImportError as exc:  # pragma: no cover
        raise ValueError(f"rag_qdrant package is not installed ({exc})") from exc
    result = grant_access(source, telegram_id) if grant else revoke_access(source, telegram_id)
    return 200, {"ok": True, "action": "grant" if grant else "revoke", **result}


def _coerce_telegram_id(body: dict[str, Any]) -> int:
    """Extract ``telegram_id`` (int) or resolve ``username`` (str) → int.

    The spec accepts both shapes. For v0 we don't yet have a username →
    telegram id resolver wired in (that's a follow-up). We accept only
    the integer form; ``username`` falls through with a clear 400.
    """
    if "telegram_id" in body:
        tid = body["telegram_id"]
        if isinstance(tid, bool) or not isinstance(tid, int):
            raise ValueError("telegram_id must be an integer")
        return int(tid)
    if "username" in body:
        raise ValueError(
            "username-based grant/revoke is not yet implemented; pass telegram_id (int) for now"
        )
    raise ValueError("body must include telegram_id (int)")


# ---------------------------------------------------------------------------
# /admin/show
# ---------------------------------------------------------------------------


def handle_show(query: dict[str, str], *, admin: Any) -> tuple[int, dict[str, Any]]:
    """List the current ACL table.

    Optional ``?source=...`` filters to a single source. With no filter
    we return one row per distinct source we've ever ingested (best
    effort — driven by Qdrant scroll; bounded to 1000 for v0).
    """
    try:
        from rag_qdrant import show_access
    except ImportError as exc:  # pragma: no cover
        raise ValueError(f"rag_qdrant package is not installed ({exc})") from exc
    source_filter = query.get("source")
    if source_filter:
        return 200, {"ok": True, "rows": [show_access(source_filter)]}

    try:
        from rag_qdrant import get_qdrant_client
        from rag_qdrant.config import settings as rag_settings
    except ImportError as exc:  # pragma: no cover
        raise ValueError(f"rag_qdrant package is not installed ({exc})") from exc

    client = get_qdrant_client()
    sources: set[str] = set()
    next_offset = None
    while True:
        result = client.scroll(
            collection_name=rag_settings.qdrant_collection,
            with_payload=True,
            with_vectors=False,
            limit=512,
            offset=next_offset,
        )
        points, next_offset = result
        for pt in points or []:
            src = (pt.payload or {}).get("source")
            if isinstance(src, str):
                sources.add(src)
        if not points or next_offset is None:
            break
    rows = [show_access(src) for src in sorted(sources)]
    return 200, {"ok": True, "rows": rows}


# ---------------------------------------------------------------------------
# /admin/bookings
# ---------------------------------------------------------------------------


def handle_bookings(query: dict[str, str], *, admin: Any) -> tuple[int, dict[str, Any]]:
    """List Outlook events for a single date via Composio.

    Query: ``?date=YYYY-MM-DD`` (required). Defaults to SGT timezone
    since the booking skill runs there.
    """
    raw_date = query.get("date")
    if not raw_date:
        raise ValueError("?date=YYYY-MM-DD query parameter is required")
    try:
        d = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ValueError(f"date must be YYYY-MM-DD (got {raw_date!r}): {exc}") from exc

    tz_name = query.get("tz", "Asia/Singapore")
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:
        raise ValueError(f"unknown timezone {tz_name!r}: {exc}") from exc

    start = datetime.combine(d, time.min, tzinfo=tz)
    end = datetime.combine(d, time.max, tzinfo=tz)
    try:
        from composio_outlook import list_events  # type: ignore[import-not-found]
    except ImportError:
        # Fall back to the orchestrator's subprocess wrapper so the
        # admin sub-app works whether Composio is installed in-process
        # or only via the upstream skill's venv.

        return _bookings_via_subprocess(start, end, tz, raw_date)

    events = list_events(start.astimezone(UTC), end.astimezone(UTC), top=200)
    return 200, {
        "ok": True,
        "date": raw_date,
        "tz": tz_name,
        "events": _normalise_events(events, tz),
    }


def _bookings_via_subprocess(
    start: datetime, end: datetime, tz: ZoneInfo, raw_date: str
) -> tuple[int, dict[str, Any]]:
    from .. import booking_subprocess

    try:
        events = booking_subprocess.list_events(
            start.isoformat(timespec="seconds"),
            end.isoformat(timespec="seconds"),
        )
    except booking_subprocess.BookingSubprocessError as exc:
        raise ValueError(f"booking lookup failed: {exc}") from exc
    return 200, {
        "ok": True,
        "date": raw_date,
        "tz": str(tz),
        "events": _normalise_events(events, tz),
    }


def _normalise_events(events: list[Any], tz: ZoneInfo) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        start_obj = e.get("start") or {}
        end_obj = e.get("end") or {}
        out.append(
            {
                "id": e.get("id"),
                "subject": e.get("subject"),
                "start": start_obj.get("dateTime"),
                "end": end_obj.get("dateTime"),
                "tz": start_obj.get("timeZone") or str(tz),
                "location": (e.get("location") or {}).get("displayName")
                if isinstance(e.get("location"), dict)
                else None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# /admin/config
# ---------------------------------------------------------------------------


def handle_config_patch(body: dict[str, Any], *, admin: Any) -> tuple[int, dict[str, Any]]:
    """Apply a YAML patch to ``booking_rules.yaml``."""
    if not admin.booking_rules_path:
        raise ValueError("BOOKING_RULES_PATH is not configured; cannot patch booking_rules.yaml")
    result = apply_patch(Path(admin.booking_rules_path), body)
    return 200, result


__all__ = [
    "handle_bookings",
    "handle_config_patch",
    "handle_grant",
    "handle_ingest",
    "handle_revoke",
    "handle_show",
]
