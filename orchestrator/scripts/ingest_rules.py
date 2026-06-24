"""One-shot ingest of ``booking_rules.yaml`` into the Qdrant collection.

Reads the YAML at ``$BOOKING_RULES_PATH`` (default
``orchestrator/data/booking_rules.yaml`` next to this script), formats each
top-level section as a markdown block, then calls
``rag_qdrant.ingest_text(markdown, source="booking_rules_v1")``.

Idempotent: ``rag_qdrant.ingest_text`` hashes the source string into a
deterministic point id, so re-running on the same source updates in place
rather than duplicating chunks.

Usage:

    python scripts/ingest_rules.py                  # uses BOOKING_RULES_PATH
    python scripts/ingest_rules.py /path/to/rules.yaml  # explicit override

Exit codes: 0 on success, 1 on any error (missing file, parse failure,
rag_qdrant import / call failure).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

import yaml

DEFAULT_SOURCE = "booking_rules_v1"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_RULES_PATH = SCRIPT_DIR.parent / "data" / "booking_rules.yaml"

DAY_LABELS = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}


def _fmt_window(value: object) -> str:
    """Render ``operating_hours`` day values (``["09:00", "17:00"]`` or ``null``)."""
    if value is None:
        return "Closed"
    if isinstance(value, list) and len(value) == 2:
        start, end = value
        return f"{start}–{end}"  # noqa: RUF001 (en-dash is intentional)
    return repr(value)


def _fmt_operating_hours(hours: Mapping[str, object]) -> str:
    lines = ["| Day | Hours |", "|---|---|"]
    for key in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
        if key in hours:
            lines.append(f"| {DAY_LABELS[key]} | {_fmt_window(hours[key])} |")
    return "\n".join(lines)


def _fmt_pricing(pricing: Mapping[str, object]) -> str:
    lines = ["| Segment | Per pax | Min pax | Currency |", "|---|---|---|---|"]
    for segment, fields in pricing.items():
        if not isinstance(fields, Mapping):
            continue
        per_pax = fields.get("per_pax", "—")
        min_pax = fields.get("min_pax", "—")
        currency = fields.get("currency", "—")
        lines.append(f"| {segment} | {per_pax} | {min_pax} | {currency} |")
    return "\n".join(lines)


def _fmt_blackout(dates: Iterable[object]) -> str:
    materialized = list(dates)
    if not materialized:
        return "_None._"
    return "\n".join(f"- `{d}`" for d in materialized)


def _fmt_deposit(deposit: Mapping[str, object]) -> str:
    bullets = []
    if "required" in deposit:
        bullets.append(f"- **Required:** `{deposit['required']}`")
    if "amount_sgd" in deposit:
        bullets.append(f"- **Amount:** SGD {deposit['amount_sgd']}")
    if "deadline_hours_before_slot" in deposit:
        bullets.append(f"- **Deadline:** {deposit['deadline_hours_before_slot']} hours before slot")
    if "instructions" in deposit:
        bullets.append(f"- **Instructions:** {deposit['instructions']}")
    return "\n".join(bullets) if bullets else "_Not set._"


def format_rules_as_markdown(rules: Mapping[str, object]) -> str:
    """Render a parsed ``booking_rules.yaml`` mapping as a markdown document.

    The order of sections is stable and matches the operator's mental model:
    location → timezone → operating hours → slot + capacity → pricing →
    blackout dates → deposit. Unknown keys are appended at the end as JSON.
    """
    sections: list[str] = []

    if "location_default" in rules:
        sections.append("## Location\n\n" + f"- **Default:** `{rules['location_default']}`")

    if "timezone" in rules:
        sections.append("## Timezone\n\n" + f"- `{rules['timezone']}`")

    if "operating_hours" in rules and isinstance(rules["operating_hours"], Mapping):
        sections.append("## Operating hours\n\n" + _fmt_operating_hours(rules["operating_hours"]))

    if "slot_duration_minutes" in rules or "max_capacity_per_slot" in rules:
        slot = rules.get("slot_duration_minutes", "—")
        cap = rules.get("max_capacity_per_slot", "—")
        sections.append(
            "## Slot & capacity\n\n"
            + f"- **Slot duration:** {slot} minutes\n"
            + f"- **Max capacity per slot:** {cap}"
        )

    if "pricing" in rules and isinstance(rules["pricing"], Mapping):
        sections.append("## Pricing\n\n" + _fmt_pricing(rules["pricing"]))

    if "blackout_dates" in rules:
        sections.append("## Blackout dates\n\n" + _fmt_blackout(rules["blackout_dates"]))

    if "deposit" in rules and isinstance(rules["deposit"], Mapping):
        sections.append("## Deposit\n\n" + _fmt_deposit(rules["deposit"]))

    known = {
        "location_default",
        "timezone",
        "operating_hours",
        "slot_duration_minutes",
        "max_capacity_per_slot",
        "pricing",
        "blackout_dates",
        "deposit",
    }
    extras = {k: v for k, v in rules.items() if k not in known}
    if extras:
        import json

        sections.append("## Other\n\n```json\n" + json.dumps(extras, indent=2) + "\n```")

    header = (
        "# Booking rules (SAAC FARM)\n\n"
        f"_Source: `{rules.get('location_default', 'n/a')}` · "
        f"Timezone: `{rules.get('timezone', 'n/a')}`_\n"
    )
    return header + "\n\n".join(sections) + "\n"


def load_rules(path: Path) -> Mapping[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"booking_rules.yaml not found at {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, Mapping):
        raise ValueError(
            f"booking_rules.yaml must contain a top-level mapping, got {type(data).__name__}"
        )
    return data


def resolve_rules_path(cli_path: str | None) -> Path:
    if cli_path:
        return Path(cli_path)
    env = os.environ.get("BOOKING_RULES_PATH")
    return Path(env) if env else DEFAULT_RULES_PATH


def ingest_rules(
    path: Path | None = None,
    source: str = DEFAULT_SOURCE,
    *,
    ingest_text=None,
) -> str:
    """Format + ingest. Returns the source id used.

    ``ingest_text`` is a DI seam so tests can monkeypatch the Qdrant call.
    """
    rules_path = path or resolve_rules_path(None)
    rules = load_rules(rules_path)
    markdown = format_rules_as_markdown(rules)

    if ingest_text is None:
        from rag_qdrant import ingest_text as _ingest
    else:
        _ingest = ingest_text

    _ingest(markdown, source=source)
    return source


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ingest booking_rules.yaml into Qdrant.")
    p.add_argument(
        "rules_path",
        nargs="?",
        default=None,
        help="Path to booking_rules.yaml (default: $BOOKING_RULES_PATH or "
        "orchestrator/data/booking_rules.yaml)",
    )
    p.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=f"Qdrant source id (default: {DEFAULT_SOURCE!r})",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    path = resolve_rules_path(args.rules_path)
    try:
        source = ingest_rules(path=path, source=args.source)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: ingest failed: {exc}", file=sys.stderr)
        return 1
    print(f"ingested booking rules into Qdrant as source={source!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
