"""Tests for the ingest helpers.

Covers:
- ``ingest_rules.format_rules_as_markdown`` produces the expected sections
  in order, with the operator's field names rendered correctly.
- ``ingest_file.extract_text`` / ``ingest_file.ingest_file`` dispatches by
  suffix (.md/.txt/.pdf) and defaults ``source`` to the filename stem.
- Both helpers wire through to ``rag_qdrant.ingest_text`` with the right
  ``source`` (mocked so we never hit a real Qdrant instance).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ingest_file  # noqa: E402  (sys.path mutation above is intentional)
import ingest_rules  # noqa: E402


@pytest.fixture
def booking_rules_fixture(tmp_path: Path) -> Path:
    """Drop a representative booking_rules.yaml fixture on disk."""
    yaml_path = tmp_path / "booking_rules.yaml"
    yaml_path.write_text(
        """
location_default: "SAAC FARM, 1 Elliot Road, Singapore 458686"
timezone: "Asia/Singapore"

operating_hours:
  mon: ["09:00", "17:00"]
  tue: ["09:00", "17:00"]
  wed: ["09:00", "17:00"]
  thu: ["09:00", "17:00"]
  fri: ["09:00", "17:00"]
  sat: ["10:00", "16:00"]
  sun: null

slot_duration_minutes: 60
max_capacity_per_slot: 30

pricing:
  school:
    per_pax: 8
    currency: "SGD"
    min_pax: 15
  public:
    per_pax: 12
    currency: "SGD"
    min_pax: 1

blackout_dates:
  - "2026-12-25"
  - "2026-12-26"

deposit:
  required: true
  amount_sgd: 50
  deadline_hours_before_slot: 48
  instructions: "PayNow to UEN TODO_REAL_UEN."
""",
        encoding="utf-8",
    )
    return yaml_path


def test_yaml_to_markdown_contains_all_sections(booking_rules_fixture: Path) -> None:
    rules = ingest_rules.load_rules(booking_rules_fixture)
    md = ingest_rules.format_rules_as_markdown(rules)

    assert md.startswith("# Booking rules")
    # section order
    section_idx = {
        h: md.index(h)
        for h in [
            "## Location",
            "## Timezone",
            "## Operating hours",
            "## Slot & capacity",
            "## Pricing",
            "## Blackout dates",
            "## Deposit",
        ]
    }
    ordered = sorted(section_idx.values())
    assert ordered == sorted(set(ordered)) and ordered == list(sorted(ordered))  # all unique
    # and they appear in the documented order
    assert list(section_idx.values()) == sorted(section_idx.values())


def test_yaml_to_markdown_renders_hours_table(booking_rules_fixture: Path) -> None:
    rules = ingest_rules.load_rules(booking_rules_fixture)
    md = ingest_rules.format_rules_as_markdown(rules)

    assert "| Day | Hours |" in md
    assert "| Monday | 09:00–17:00 |" in md  # noqa: RUF001 (en-dash is intentional)
    assert "| Saturday | 10:00–16:00 |" in md  # noqa: RUF001 (en-dash is intentional)
    assert "| Sunday | Closed |" in md


def test_yaml_to_markdown_renders_pricing_table(booking_rules_fixture: Path) -> None:
    rules = ingest_rules.load_rules(booking_rules_fixture)
    md = ingest_rules.format_rules_as_markdown(rules)

    assert "| Segment | Per pax | Min pax | Currency |" in md
    assert "| school | 8 | 15 | SGD |" in md
    assert "| public | 12 | 1 | SGD |" in md


def test_yaml_to_markdown_renders_blackout_and_deposit(
    booking_rules_fixture: Path,
) -> None:
    rules = ingest_rules.load_rules(booking_rules_fixture)
    md = ingest_rules.format_rules_as_markdown(rules)

    assert "- `2026-12-25`" in md
    assert "- `2026-12-26`" in md
    assert "**Required:** `True`" in md
    assert "**Amount:** SGD 50" in md
    assert "**Deadline:** 48 hours before slot" in md
    assert "PayNow to UEN TODO_REAL_UEN." in md


def test_yaml_to_markdown_empty_blackout_renders_text(
    booking_rules_fixture: Path,
) -> None:
    rules = ingest_rules.load_rules(booking_rules_fixture)
    rules["blackout_dates"] = []
    md = ingest_rules.format_rules_as_markdown(rules)
    assert "## Blackout dates" in md
    assert "_None._" in md


def test_load_rules_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    bad = tmp_path / "rules.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="top-level mapping"):
        ingest_rules.load_rules(bad)


def test_load_rules_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ingest_rules.load_rules(tmp_path / "nope.yaml")


def test_ingest_rules_calls_rag_qdrant_ingest_text(
    booking_rules_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ingest_rules`` must call ``rag_qdrant.ingest_text(md, source='booking_rules_v1')``."""
    captured: list[tuple[str, str]] = []

    def _fake_ingest(text: str, *, source: str) -> None:
        captured.append((text, source))

    monkeypatch.setattr(ingest_rules, "_ingest", _fake_ingest, raising=False)
    # The function looks up `ingest_text` from rag_qdrant at call time;
    # since rag_qdrant is not installed we monkeypatch the import too.
    import types

    fake_mod = types.ModuleType("rag_qdrant")
    fake_mod.ingest_text = _fake_ingest
    monkeypatch.setitem(sys.modules, "rag_qdrant", fake_mod)

    source = ingest_rules.ingest_rules(path=booking_rules_fixture, ingest_text=_fake_ingest)

    assert source == "booking_rules_v1"
    assert len(captured) == 1
    md, used_source = captured[0]
    assert used_source == "booking_rules_v1"
    assert md.startswith("# Booking rules")
    assert "## Operating hours" in md


def test_ingest_rules_respects_bookkeeping_path_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, booking_rules_fixture: Path
) -> None:
    monkeypatch.setenv("BOOKING_RULES_PATH", str(booking_rules_fixture))
    resolved = ingest_rules.resolve_rules_path(None)
    assert resolved == booking_rules_fixture


# ---------------------------------------------------------------------------
# ingest_file
# ---------------------------------------------------------------------------


def test_default_source_from_path() -> None:
    assert ingest_file.default_source_from_path(Path("faq.md")) == "faq"
    assert ingest_file.default_source_from_path(Path("/abs/path/notes.txt")) == "notes"
    assert ingest_file.default_source_from_path(Path("/abs/2026-06-05.pdf")) == "2026-06-05"


def test_extract_text_reads_markdown(tmp_path: Path) -> None:
    md = tmp_path / "notes.md"
    md.write_text("# Hello\nWorld", encoding="utf-8")
    assert ingest_file.extract_text(md) == "# Hello\nWorld"


def test_extract_text_reads_txt(tmp_path: Path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("plain text", encoding="utf-8")
    assert ingest_file.extract_text(txt) == "plain text"


def test_extract_text_rejects_unsupported_extension(tmp_path: Path) -> None:
    csv = tmp_path / "data.csv"
    csv.write_text("a,b,c", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported file type"):
        ingest_file.extract_text(csv)


def test_extract_text_reads_pdf(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """PDF path is exercised without a real PDF: we patch ``pypdf.PdfReader``."""
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-fake")

    class _FakePage:
        def extract_text(self) -> str:
            return "page text"

    class _FakeReader:
        def __init__(self, path: str) -> None:
            self.pages = [_FakePage(), _FakePage()]

    import pypdf

    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)

    assert ingest_file.extract_text(pdf) == "page text\n\npage text"


def test_ingest_file_default_source_dispatches_by_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, str]] = []

    def _fake_ingest(text: str, *, source: str) -> None:
        captured.append((text, source))

    md = tmp_path / "faq.md"
    md.write_text("# FAQ\n\nanswer here", encoding="utf-8")

    source = ingest_file.ingest_file(md, ingest_text=_fake_ingest)
    assert source == "faq"
    assert captured == [("# FAQ\n\nanswer here", "faq")]


def test_ingest_file_explicit_source_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, str]] = []

    def _fake_ingest(text: str, *, source: str) -> None:
        captured.append((text, source))

    md = tmp_path / "faq.md"
    md.write_text("body", encoding="utf-8")
    source = ingest_file.ingest_file(md, source="faq-v1-2026-06-24", ingest_text=_fake_ingest)
    assert source == "faq-v1-2026-06-24"
    assert captured == [("body", "faq-v1-2026-06-24")]


def test_ingest_file_txt_dispatch(tmp_path: Path) -> None:
    captured: list[tuple[str, str]] = []

    def _fake_ingest(text: str, *, source: str) -> None:
        captured.append((text, source))

    txt = tmp_path / "notes.txt"
    txt.write_text("raw text", encoding="utf-8")
    source = ingest_file.ingest_file(txt, ingest_text=_fake_ingest)
    assert source == "notes"
    assert captured == [("raw text", "notes")]


def test_ingest_file_pdf_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: list[tuple[str, str]] = []

    def _fake_ingest(text: str, *, source: str) -> None:
        captured.append((text, source))

    class _FakePage:
        def extract_text(self) -> str:
            return "p1"

    class _FakeReader:
        def __init__(self, path: str) -> None:
            self.pages = [_FakePage()]

    import pypdf

    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)

    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-fake")
    source = ingest_file.ingest_file(pdf, ingest_text=_fake_ingest)
    assert source == "report"
    assert captured == [("p1", "report")]


def test_ingest_file_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ingest_file.ingest_file(tmp_path / "nope.md")


def test_ingest_file_unsupported_extension_raises(tmp_path: Path) -> None:
    bad = tmp_path / "a.xlsx"
    bad.write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported file type"):
        ingest_file.ingest_file(bad)
