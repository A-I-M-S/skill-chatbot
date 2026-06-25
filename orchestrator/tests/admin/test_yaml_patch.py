"""Tests for the YAML round-trip patch in ``src.admin.yaml_patch``.

The TG admin surface should accept exactly 4 keys; everything else is
either ``disk_only`` (operator-only) or ``unknown_key``. Edits must
preserve comments + key order on the surrounding file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.admin.yaml_patch import (
    ALLOWED_PATCH_KEYS,
    DISK_ONLY_KEYS,
    PatchError,
    apply_patch,
    validate_patch,
)

SAMPLE_YAML = """\
# Booking rules for BlueAcres / SAAC FARM
# Edited manually (not via onboard.py).

location_default: "SAAC FARM, 1 Elliot Road, Singapore 458686"
timezone: "Asia/Singapore"

operating_hours:
  mon: ["09:00", "17:00"]
  tue: ["09:00", "17:00"]
  wed: ["09:00", "17:00"]
  thu: ["09:00", "17:00"]
  fri: ["09:00", "17:00"]
  sat: ["09:00", "17:00"]
  sun: ["09:00", "17:00"]

slot_duration_minutes: 60          # default 60-min slots
max_capacity_per_slot: 30         # TODO: confirm

blackout_dates: []
"""


@pytest.fixture
def rules_path(tmp_path: Path) -> Path:
    p = tmp_path / "booking_rules.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# validate_patch (pure validation, no I/O)
# ---------------------------------------------------------------------------


def test_validate_patch_accepts_slot_duration() -> None:
    out = validate_patch({"key": "slot_duration_minutes", "value": 45})
    assert out == {"key": "slot_duration_minutes", "value": 45}


def test_validate_patch_accepts_capacity() -> None:
    out = validate_patch({"key": "max_capacity_per_slot", "value": 25})
    assert out == {"key": "max_capacity_per_slot", "value": 25}


def test_validate_patch_accepts_blackout_list() -> None:
    out = validate_patch({"key": "blackout_dates", "value": ["2026-12-25", "2027-01-01"]})
    assert out == {"key": "blackout_dates", "value": ["2026-12-25", "2027-01-01"]}


def test_validate_patch_accepts_operating_hours_per_day() -> None:
    out = validate_patch(
        {
            "key": "operating_hours_per_day",
            "value": {"mon": ["08:00", "16:00"], "sun": None},
        }
    )
    assert out == {
        "key": "operating_hours_per_day",
        "value": {"mon": ["08:00", "16:00"], "sun": None},
    }


@pytest.mark.parametrize("bad_key", sorted(DISK_ONLY_KEYS))
def test_validate_patch_rejects_disk_only_keys(bad_key: str) -> None:
    with pytest.raises(PatchError) as exc_info:
        validate_patch({"key": bad_key, "value": "whatever"})
    assert exc_info.value.code == "disk_only"
    assert bad_key in exc_info.value.message


def test_validate_patch_rejects_unknown_key() -> None:
    with pytest.raises(PatchError) as exc_info:
        validate_patch({"key": "made_up_field", "value": 1})
    assert exc_info.value.code == "unknown_key"


def test_validate_patch_rejects_missing_key() -> None:
    with pytest.raises(PatchError) as exc_info:
        validate_patch({"value": 60})
    assert exc_info.value.code == "bad_request"


def test_validate_patch_rejects_non_int_for_int_field() -> None:
    with pytest.raises(PatchError) as exc_info:
        validate_patch({"key": "slot_duration_minutes", "value": "60"})
    assert exc_info.value.code == "bad_value"


def test_validate_patch_rejects_negative_int() -> None:
    with pytest.raises(PatchError) as exc_info:
        validate_patch({"key": "slot_duration_minutes", "value": -10})
    assert exc_info.value.code == "bad_value"


def test_validate_patch_rejects_bool_for_int_field() -> None:
    """In Python ``bool`` is a subclass of ``int`` — reject explicitly so
    ``{"value": true}`` doesn't silently pass as 1.
    """
    with pytest.raises(PatchError) as exc_info:
        validate_patch({"key": "slot_duration_minutes", "value": True})
    assert exc_info.value.code == "bad_value"


def test_validate_patch_rejects_bad_blackout_format() -> None:
    with pytest.raises(PatchError) as exc_info:
        validate_patch({"key": "blackout_dates", "value": ["25-12-2026"]})
    assert exc_info.value.code == "bad_value"


def test_validate_patch_rejects_bad_operating_hours_format() -> None:
    with pytest.raises(PatchError) as exc_info:
        validate_patch({"key": "operating_hours_per_day", "value": {"mon": "09:00-17:00"}})
    assert exc_info.value.code == "bad_value"


def test_validate_patch_rejects_unknown_weekday() -> None:
    with pytest.raises(PatchError) as exc_info:
        validate_patch({"key": "operating_hours_per_day", "value": {"funday": ["09:00", "17:00"]}})
    assert exc_info.value.code == "bad_value"


def test_allowed_keys_match_spec() -> None:
    """Spec lists exactly 4 TG-controllable keys. If you add a key here
    you must update the issue #30 spec and the ``/admin/config`` docs.
    """
    assert (
        frozenset(
            {
                "slot_duration_minutes",
                "max_capacity_per_slot",
                "operating_hours_per_day",
                "blackout_dates",
            }
        )
        == ALLOWED_PATCH_KEYS
    )


# ---------------------------------------------------------------------------
# apply_patch (atomic file write + ruamel round-trip)
# ---------------------------------------------------------------------------


def test_apply_patch_updates_slot_duration(rules_path: Path) -> None:
    result = apply_patch(rules_path, {"key": "slot_duration_minutes", "value": 90})
    assert result["ok"] is True
    assert result["previous"] == 60
    text = rules_path.read_text(encoding="utf-8")
    assert "slot_duration_minutes: 90" in text


def test_apply_patch_updates_max_capacity(rules_path: Path) -> None:
    result = apply_patch(rules_path, {"key": "max_capacity_per_slot", "value": 25})
    assert result["previous"] == 30
    assert "max_capacity_per_slot: 25" in rules_path.read_text(encoding="utf-8")


def test_apply_patch_updates_blackout_dates(rules_path: Path) -> None:
    apply_patch(rules_path, {"key": "blackout_dates", "value": ["2026-12-25"]})
    text = rules_path.read_text(encoding="utf-8")
    assert "2026-12-25" in text


def test_apply_patch_updates_operating_hours(rules_path: Path) -> None:
    apply_patch(
        rules_path,
        {
            "key": "operating_hours_per_day",
            "value": {"mon": ["08:00", "12:00"], "sun": None},
        },
    )
    text = rules_path.read_text(encoding="utf-8")
    # ruamel may render the inner list inline OR as a block sequence.
    # We accept either form, but the values must be present.
    assert "08:00" in text and "12:00" in text
    assert "mon:" in text  # the patched day is present
    assert "sun:" in text  # closed day still present
    # Days we did not list in the new value should be removed.
    assert "tue:" not in text
    assert "wed:" not in text


def test_apply_patch_preserves_comments(rules_path: Path) -> None:
    """ruamel must keep the leading comments + trailing comments."""
    apply_patch(rules_path, {"key": "slot_duration_minutes", "value": 45})
    text = rules_path.read_text(encoding="utf-8")
    # leading file comment
    assert "Booking rules for BlueAcres" in text
    # inline comment on max_capacity_per_slot
    assert "TODO: confirm" in text


def test_apply_patch_rejects_disk_only_keys(rules_path: Path) -> None:
    with pytest.raises(PatchError) as exc_info:
        apply_patch(rules_path, {"key": "location_default", "value": "Other"})
    assert exc_info.value.code == "disk_only"
    assert "SAAC FARM, 1 Elliot Road" in rules_path.read_text(encoding="utf-8")


def test_apply_patch_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        apply_patch(tmp_path / "missing.yaml", {"key": "slot_duration_minutes", "value": 60})


def test_apply_patch_is_atomic_no_tmp_leftover(rules_path: Path) -> None:
    """Atomic write should leave no ``.tmp`` siblings behind on success."""
    apply_patch(rules_path, {"key": "slot_duration_minutes", "value": 75})
    siblings = [p.name for p in rules_path.parent.iterdir() if p.name.endswith(".tmp")]
    assert siblings == []


def test_apply_patch_replaces_atomically_no_partial_write(
    rules_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If something raises mid-write the original file must remain intact."""
    original_text = rules_path.read_text(encoding="utf-8")

    from src.admin import yaml_patch as yp

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(yp, "_atomic_write", _boom)
    with pytest.raises(RuntimeError, match="simulated crash"):
        apply_patch(rules_path, {"key": "slot_duration_minutes", "value": 75})
    assert rules_path.read_text(encoding="utf-8") == original_text
    # No leftover tmp file either
    assert not any(p.name.endswith(".tmp") for p in rules_path.parent.iterdir())


def test_apply_patch_returns_previous_value(rules_path: Path) -> None:
    result = apply_patch(rules_path, {"key": "slot_duration_minutes", "value": 120})
    assert result["previous"] == 60
    assert result["value"] == 120
    assert result["path"] == str(rules_path)
    assert result["key"] == "slot_duration_minutes"


# ---------------------------------------------------------------------------
# PATCH /admin/config end-to-end via the dispatcher
# ---------------------------------------------------------------------------


def test_dispatch_patch_happy_path(rules_path: Path, admin_cfg) -> None:
    from .conftest import auth_headers, call

    status, payload = call(
        "PATCH",
        "/admin/config",
        body={"key": "slot_duration_minutes", "value": 90},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 200
    assert payload["ok"] is True
    assert "slot_duration_minutes: 90" in rules_path.read_text(encoding="utf-8")


def test_dispatch_patch_disk_only_returns_400(rules_path: Path, admin_cfg) -> None:
    from .conftest import auth_headers, call

    status, payload = call(
        "PATCH",
        "/admin/config",
        body={"key": "timezone", "value": "Asia/Tokyo"},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 400
    assert payload["error"] == "disk_only"


def test_dispatch_patch_without_rules_path_returns_400(admin_cfg) -> None:
    from src.admin import AdminSettings

    from .conftest import auth_headers, call

    cfg = AdminSettings(
        admin_http_token=admin_cfg.admin_http_token,
        admin_telegram_ids=admin_cfg.admin_telegram_ids,
        booking_rules_path=None,
    )
    status, payload = call(
        "PATCH",
        "/admin/config",
        body={"key": "slot_duration_minutes", "value": 60},
        headers=auth_headers(),
        admin=cfg,
    )
    assert status == 400
    assert "BOOKING_RULES_PATH" in payload["message"]
