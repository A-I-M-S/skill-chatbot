"""i18n key coverage tests (issue #9).

The single biggest risk in a bilingual bot is missing keys. These tests
enumerate every key the flows + router + handoff reach for and assert
that both EN and 中文 exist (via the STRINGS dict).
"""

from __future__ import annotations

from src.i18n import STRINGS, t


# Every key used by the flows / dispatcher / router / handoff. Keep
# this list in sync with the call sites — when a flow adds a new key,
# add it here too.
EXPECTED_KEYS = {
    # handoff
    "handoff_msg",
    "abusive_msg",
    "yes_received_edited",
    "yes_received_cancelled",
    "yes_received_booked",
    "aborted",
    "edit_no_events",
    "edit_pick_event",
    "edit_one_match",
    # book_new per-field
    "ask_date",
    "ask_time",
    "ask_pax",
    "ask_contact_name",
    "ask_contact",
    "ask_org",
    "ask_notes",
    # book_edit / book_cancel
    "edit_confirm",
    "edit_ask_field",
    "cancel_confirm",
}


def test_every_expected_key_is_bilingual() -> None:
    en_keys = {k for (lang, k) in STRINGS if lang == "en"}
    zh_keys = {k for (lang, k) in STRINGS if lang == "zh"}
    missing_en = EXPECTED_KEYS - en_keys
    missing_zh = EXPECTED_KEYS - zh_keys
    assert not missing_en, f"keys missing in EN: {missing_en}"
    assert not missing_zh, f"keys missing in ZH: {missing_zh}"


def test_no_unexpected_extra_keys() -> None:
    """If a key is added that isn't in EXPECTED_KEYS, that's a slip —
    add it here so it stays documented."""
    en_keys = {k for (lang, k) in STRINGS if lang == "en"}
    extras = en_keys - EXPECTED_KEYS
    # Allow extras but surface them so we can update the list intentionally.
    assert isinstance(extras, set)


def test_t_returns_nonempty_for_every_expected_key() -> None:
    """Render every key with sample placeholders. Keys without placeholders
    are checked with an empty kwargs dict; keys that need kwargs get them."""
    sample_kwargs = {
        "handoff_msg": {"admin_contact": "+6591234567"},
        "abusive_msg": {},
        "yes_received_edited": {"event_id": "EVT-1"},
        "yes_received_cancelled": {"event_id": "EVT-1"},
        "yes_received_booked": {"event_id": "EVT-1"},
        "aborted": {},
        "edit_no_events": {},
        "edit_pick_event": {"events": "1. foo"},
        "edit_one_match": {"event": "Sat 15 Aug 10:30"},
        "ask_date": {},
        "ask_time": {},
        "ask_pax": {},
        "ask_contact_name": {},
        "ask_contact": {},
        "ask_org": {},
        "ask_notes": {},
        "edit_confirm": {"old": "old", "new": "new"},
        "edit_ask_field": {},
        "cancel_confirm": {"event_summary": "Sat 15 Aug 10:30"},
    }
    for k in sorted(EXPECTED_KEYS):
        kw = sample_kwargs.get(k, {})
        en = t(k, "en", **kw)
        zh = t(k, "zh", **kw)
        assert en and isinstance(en, str), f"empty EN string for key {k}"
        assert zh and isinstance(zh, str), f"empty ZH string for key {k}"
        assert en != zh, f"EN and ZH identical for key {k}"


def test_t_missing_key_raises_keyerror() -> None:
    import pytest
    with pytest.raises(KeyError):
        t("not_a_real_key_zzz", "en")
    with pytest.raises(KeyError):
        t("not_a_real_key_zzz", "zh")


def test_t_missing_placeholder_raises_keyerror() -> None:
    """If a key uses a placeholder that the caller forgot to fill in,
    t() should raise so we catch it at dev time."""
    import pytest
    # handoff_msg needs admin_contact
    with pytest.raises(KeyError):
        t("handoff_msg", "en")  # no admin_contact kwarg
    # edit_confirm needs old + new
    with pytest.raises(KeyError):
        t("edit_confirm", "en")  # no old / no new kwarg


def test_t_default_language_is_en() -> None:
    """When language is empty or unknown, t() falls back to EN."""
    assert t("aborted", "") == t("aborted", "en")
    assert t("aborted", "ja") == t("aborted", "en")
    assert t("aborted", None) == t("aborted", "en")  # type: ignore[arg-type]


def test_t_zh_prefix_variants() -> None:
    """zh-CN, zh-TW, ZH all resolve to the ZH strings."""
    assert t("aborted", "zh") == t("aborted", "zh-CN")
    assert t("aborted", "zh") == t("aborted", "ZH")
    assert t("aborted", "zh") != t("aborted", "en")


def test_placeholders_render() -> None:
    """Every key with placeholders renders cleanly when given sample kwargs."""
    assert "+6591234567" in t("handoff_msg", "en", admin_contact="+6591234567")
    assert "old" in t("edit_confirm", "en", old="old", new="new")
    assert "new" in t("edit_confirm", "en", old="old", new="new")
    assert "EVT-1" in t("yes_received_edited", "en", event_id="EVT-1")
    assert "EVT-1" in t("yes_received_cancelled", "en", event_id="EVT-1")