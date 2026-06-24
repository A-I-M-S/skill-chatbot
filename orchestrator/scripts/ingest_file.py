"""One-shot ingest of an arbitrary file (``.md``/``.txt``/``.pdf``) into Qdrant.

Usage:

    python scripts/ingest_file.py orchestrator/data/faq.md
    python scripts/ingest_file.py /path/to/notes.pdf --source meeting-2026-06-05
    python scripts/ingest_file.py /path/to/notes.md --source notes-v1

If ``--source`` is omitted, the filename without its extension is used (e.g.
``faq.md`` → ``faq``). For PDFs we use ``pypdf`` to extract text; for ``.md``
and ``.txt`` we read as UTF-8. Anything else raises ``ValueError``.

Idempotent: ``rag_qdrant.ingest_text`` hashes the source string into a
deterministic point id, so re-running on the same source updates in place.

Exit codes: 0 on success, 1 on any error (missing file, unsupported
extension, rag_qdrant import / call failure).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SUPPORTED_SUFFIXES = {".md", ".txt", ".text", ".pdf"}


def default_source_from_path(path: Path) -> str:
    """Default source id: filename without its (last) extension."""
    return path.name.removesuffix("".join(path.suffixes)) or path.stem or path.name


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_pdf_file(path: Path) -> str:
    """Extract text from a PDF via pypdf. Returns the joined page text."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "pypdf is required to ingest PDFs; install with `pip install pypdf`"
        ) from exc

    reader = PdfReader(str(path))
    parts: list[str] = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text:
            parts.append(text)
        del i  # silence linters
    return "\n\n".join(parts)


def extract_text(path: Path) -> str:
    """Dispatch by suffix. Raises ``ValueError`` for unsupported types."""
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".text"}:
        return read_text_file(path)
    if suffix == ".pdf":
        return read_pdf_file(path)
    raise ValueError(f"unsupported file type {suffix!r}; supported: {sorted(SUPPORTED_SUFFIXES)}")


def ingest_file(
    path: Path,
    source: str | None = None,
    *,
    ingest_text=None,
) -> str:
    """Read + ingest. Returns the source id used.

    ``ingest_text`` is a DI seam so tests can monkeypatch the Qdrant call.
    """
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"unsupported file type {path.suffix!r}; supported: {sorted(SUPPORTED_SUFFIXES)}"
        )

    resolved_source = source or default_source_from_path(path)
    text = extract_text(path)

    if ingest_text is None:
        from rag_qdrant import ingest_text as _ingest
    else:
        _ingest = ingest_text

    _ingest(text, source=resolved_source)
    return resolved_source


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ingest a file into Qdrant.")
    p.add_argument("path", help="Path to a .md / .txt / .pdf file")
    p.add_argument(
        "--source",
        default=None,
        help="Qdrant source id (default: filename without extension)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    path = Path(args.path)
    try:
        source = ingest_file(path, source=args.source)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: ingest failed: {exc}", file=sys.stderr)
        return 1
    print(f"ingested {path.name} into Qdrant as source={source!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
