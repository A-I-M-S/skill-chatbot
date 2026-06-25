"""Atomic ruamel.yaml round-trip edits for ``booking_rules.yaml``.

We deliberately use ruamel (not ``yaml.safe_load/dump``) so that
comments, key ordering, and the existing operator annotations survive
the patch. This matches the upstream ``booking_flow.py`` expectation that
``RULES_PATH`` is hand-edited YAML and we should not silently rewrite it.

Only the four TG-controllable fields are accepted; anything else raises
:class:`PatchError` with ``code="disk_only"`` so the API contract is
self-documenting.
"""

from __future__ import annotations

import contextlib
import copy
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

# TG-controllable fields per issue #30. The set is closed; any other key
# in the PATCH body is rejected with ``disk_only`` so the operator
# doesn't accidentally let a bot rewrite their UEN or timezone.
ALLOWED_PATCH_KEYS: frozenset[str] = frozenset(
    {
        "slot_duration_minutes",
        "max_capacity_per_slot",  # real YAML uses this name, NOT max_pax_per_slot
        "operating_hours_per_day",
        "blackout_dates",
    }
)

# Keys explicitly NOT settable via PATCH (the upstream skill reads these
# but the TG admin surface shouldn't reach them).
DISK_ONLY_KEYS: frozenset[str] = frozenset(
    {
        "location_default",
        "timezone",
        "pricing_tiers",
        "deposit_instructions",
        "outlook_calendar_id",
    }
)

_OPERATING_HOURS_PER_DAY_PATH = ("operating_hours_per_day",)
_OPERATING_HOURS_PATH = ("operating_hours",)
_BLACKOUT_DATES_PATH = ("blackout_dates",)
_DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class PatchError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _validate_operating_hours(value: Any) -> dict[str, list[str] | None]:
    """Normalise ``operating_hours_per_day`` value.

    Accepts either:

    - a mapping of weekday → ``["HH:MM", "HH:MM"]`` (open), or
    - weekday → ``null`` / ``None`` (closed).

    Days outside Mon-Sun are rejected with a clear error.
    """
    if not isinstance(value, dict):
        raise PatchError(
            "bad_value",
            "operating_hours_per_day must be a mapping of weekday -> [open, close] or null",
        )
    out: dict[str, list[str] | None] = {}
    for day, window in value.items():
        if day not in _DAY_KEYS:
            raise PatchError(
                "bad_value",
                f"operating_hours_per_day: unknown day {day!r} (expected one of {_DAY_KEYS})",
            )
        if window is None:
            out[day] = None
            continue
        if (
            not isinstance(window, list)
            or len(window) != 2
            or not all(isinstance(x, str) for x in window)
        ):
            raise PatchError(
                "bad_value",
                f"operating_hours_per_day[{day}] must be [open, close] or null",
            )
        for hhmm in window:
            if not re.match(r"^\d{2}:\d{2}$", hhmm):
                raise PatchError(
                    "bad_value",
                    f"operating_hours_per_day[{day}] entries must be HH:MM (got {hhmm!r})",
                )
        out[day] = [str(window[0]), str(window[1])]
    return out


def _validate_blackout_dates(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise PatchError("bad_value", "blackout_dates must be a list of YYYY-MM-DD strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", item):
            raise PatchError(
                "bad_value", f"blackout_dates entries must be YYYY-MM-DD (got {item!r})"
            )
        out.append(item)
    return out


def _validate_int(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PatchError("bad_value", f"{key} must be an integer (got {value!r})")
    if value < 0:
        raise PatchError("bad_value", f"{key} must be non-negative (got {value})")
    return int(value)


def validate_patch(body: dict[str, Any]) -> dict[str, Any]:
    """Validate a PATCH body against the whitelist.

    Returns the validated/normalized payload ready to be written to the
    YAML. Raises :class:`PatchError` on any problem.
    """
    if not isinstance(body, dict):
        raise PatchError("bad_request", "PATCH body must be a JSON object with {key, value}")
    if "key" not in body:
        raise PatchError("bad_request", "PATCH body must include 'key'")
    key = str(body["key"])
    if key in DISK_ONLY_KEYS:
        raise PatchError(
            "disk_only",
            f"{key!r} is not settable via PATCH /admin/config (operator-only field)",
        )
    if key not in ALLOWED_PATCH_KEYS:
        raise PatchError(
            "unknown_key",
            f"unknown key {key!r}; allowed: {sorted(ALLOWED_PATCH_KEYS)}",
        )
    if "value" not in body:
        raise PatchError("bad_request", "PATCH body must include 'value'")
    raw = body["value"]
    if key == "slot_duration_minutes":
        norm: Any = _validate_int(raw, key)
    elif key == "max_capacity_per_slot":
        norm = _validate_int(raw, key)
    elif key == "operating_hours_per_day":
        norm = _validate_operating_hours(raw)
    elif key == "blackout_dates":
        norm = _validate_blackout_dates(raw)
    else:  # pragma: no cover - defensive (covered by the unknown_key branch)
        raise PatchError("unknown_key", f"unknown key {key!r}")
    return {"key": key, "value": norm}


def apply_patch(path: Path, body: dict[str, Any]) -> dict[str, Any]:
    """Apply a validated PATCH to ``path`` atomically.

    Returns ``{"path": str, "key": str, "value": ..., "previous": ...}`` so
    the caller can echo the change. Raises :class:`PatchError` on bad
    input or read-only fields; raises :class:`FileNotFoundError` if the
    file is missing.
    """
    validated = validate_patch(body)
    key = validated["key"]
    new_value = validated["value"]

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)

    if not path.exists():
        raise FileNotFoundError(f"booking_rules.yaml not found at {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.load(f) or {}

    previous = copy.deepcopy(_read_previous(data, key))

    if key == "operating_hours_per_day":
        _apply_operating_hours(data, new_value)
    elif key == "blackout_dates":
        if "blackout_dates" not in data:
            data["blackout_dates"] = []
        data["blackout_dates"] = new_value
    else:  # slot_duration_minutes / max_capacity_per_slot
        data[key] = new_value

    _atomic_write(path, yaml, data)
    logger.info("admin config patched key=%s new_value=%r", key, new_value)
    return {"ok": True, "path": str(path), "key": key, "value": new_value, "previous": previous}


def _read_previous(data: Any, key: str) -> Any:
    if key == "operating_hours_per_day":
        hours = data.get("operating_hours") or {}
        return {
            day: list(hours[day]) if hours.get(day) else None for day in _DAY_KEYS if day in hours
        }
    if key == "blackout_dates":
        return list(data.get("blackout_dates", []) or [])
    return data.get(key)


def _apply_operating_hours(data: dict[str, Any], new_value: dict[str, list[str] | None]) -> None:
    hours = data.get("operating_hours")
    if hours is None:
        # ruamel round-trip; create mapping as CommentedMap so we stay
        # in the same family as the loaded data.
        from ruamel.yaml.comments import CommentedMap

        hours = CommentedMap()
        data["operating_hours"] = hours
    # Replace every day explicitly so days the operator removed are not
    # left dangling. We mutate in place to keep the existing section
    # comment if present.
    for day in _DAY_KEYS:
        if day in new_value:
            hours[day] = new_value[day]
        elif day in hours:
            del hours[day]


def _atomic_write(path: Path, yaml: YAML, data: Any) -> None:
    """Write ``data`` to ``path`` via tmp-file + ``os.replace``.

    ruamel can't stream to a NamedTemporaryFile that we then rename
    without breaking its round-trip behaviour, so we round-trip through
    a string buffer instead and write atomically. ``os.replace`` is
    atomic on POSIX when the source and destination are on the same
    filesystem.
    """
    import io

    buf = io.StringIO()
    yaml.dump(data, buf)
    payload = buf.getvalue()
    fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise


__all__ = [
    "ALLOWED_PATCH_KEYS",
    "DISK_ONLY_KEYS",
    "PatchError",
    "apply_patch",
    "validate_patch",
]
