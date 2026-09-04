"""Cheap-signal document classifier.

`classify(path)` never opens more than a handful of pages and never raises --
a classifier that throws stops the pipeline. When no signal is decisive it
returns ('unknown', low_confidence) rather than guessing.

Signals (see CONTRACT.md and db/schema.sql `doc_kind`), tuned against the
real corpus rather than assumed:

- `.xlsx`                                              -> calculator
- exactly 2 pages, ~1190x842pt (A3 landscape)           -> crib_sheet
- PowerPoint producer/creator + widescreen aspect ratio -> deck
- >=250 pages with a deep (depth>=3), large (>=100
  entries) bookmark outline                             -> guideline_report
- spread pagination (most pages ~2x as wide as tall)
  plus criterion-style codes or "tracker" wording        -> framework /
                                                            implementation_plan
Anything else -> ('unknown', confidence).
"""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf

# A3-landscape crib sheets, points, with slack for rounding.
_CRIB_WIDTH = 1190.55
_CRIB_HEIGHT = 841.89
_CRIB_TOLERANCE = 8.0

# widescreen decks land between 16:10 (1.6) and 16:9 (1.78); give it slack.
_DECK_RATIO_MIN = 1.45
_DECK_RATIO_MAX = 1.95

_GUIDELINE_MIN_PAGES = 250
_GUIDELINE_MIN_TOC_ENTRIES = 100
_GUIDELINE_MIN_TOC_DEPTH = 3

# criterion codes like 'PC4.2', 'RE2.1', 'NF1.3'
_CRITERION_CODE_RE = re.compile(r"\b[A-Z]{2,4}\d+\.\d+\b")


def classify(path: Path) -> tuple[str, float]:
    """(doc_kind, confidence 0-1) from cheap signals; never raises."""
    try:
        return _classify(Path(path))
    except Exception:
        return ("unknown", 0.0)


def _classify(path: Path) -> tuple[str, float]:
    if path.suffix.lower() == ".xlsx":
        return ("calculator", 0.99)

    if path.suffix.lower() != ".pdf":
        return ("unknown", 0.1)

    with pymupdf.open(path) as doc:
        page_count = doc.page_count
        if page_count == 0:
            return ("unknown", 0.0)

        first = doc[0]
        width, height = first.rect.width, first.rect.height

        if page_count == 2 and _close(width, _CRIB_WIDTH) and _close(height, _CRIB_HEIGHT):
            return ("crib_sheet", 0.95)

        meta = doc.metadata or {}
        producer = f"{meta.get('producer', '')} {meta.get('creator', '')}".lower()
        ratio = (width / height) if height else 0.0
        if "powerpoint" in producer:
            if _DECK_RATIO_MIN <= ratio <= _DECK_RATIO_MAX:
                return ("deck", 0.9)
            return ("deck", 0.6)

        toc = _safe_toc(doc)
        toc_depth = max((lvl for lvl, _, _ in toc), default=0)
        if (
            page_count >= _GUIDELINE_MIN_PAGES
            and len(toc) >= _GUIDELINE_MIN_TOC_ENTRIES
            and toc_depth >= _GUIDELINE_MIN_TOC_DEPTH
        ):
            return ("guideline_report", 0.9)

        if _is_spread_paginated(doc):
            sample_text = _sample_text(doc)
            low = sample_text.lower()
            tracker_hits = low.count("tracker")
            codes_found = bool(_CRITERION_CODE_RE.search(sample_text))
            keyword_hits = sum(low.count(k) for k in ("principle", "theme", "strategy"))
            if tracker_hits >= 3:
                return ("implementation_plan", 0.8)
            if codes_found or keyword_hits >= 3:
                return ("framework", 0.75)
            return ("framework", 0.5)

    return ("unknown", 0.15)


def _close(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= _CRIB_TOLERANCE


def _safe_toc(doc: "pymupdf.Document") -> list[tuple[int, str, int]]:
    try:
        return doc.get_toc() or []
    except Exception:
        return []


def _is_spread_paginated(doc: "pymupdf.Document") -> bool:
    """Most pages ~2x as wide as tall (a landscape spread of two printed pages)."""
    n = doc.page_count
    if n < 5:
        return False
    idxs = sorted(set(min(n - 1, max(0, i)) for i in (1, n // 4, n // 2, (3 * n) // 4, n - 2)))
    wide = 0
    for i in idxs:
        r = doc[i].rect
        if r.height and (r.width / r.height) >= 1.3:
            wide += 1
    return wide >= max(1, len(idxs) - 1)


def _sample_text(doc: "pymupdf.Document", max_chars: int = 400_000) -> str:
    """Text to scan for keyword/code signals.

    Spread-paginated framework volumes only run to ~150 pages, so a full
    scan is still cheap (plain text extraction, no rendering) and, unlike
    fixed-stride sampling, cannot miss a keyword that clusters in one part
    of the document. Guideline-length documents never reach this path --
    they are classified from page count + TOC depth before this runs.
    """
    n = doc.page_count
    chunks: list[str] = []
    total = 0
    for i in range(n):
        if total >= max_chars:
            break
        try:
            t = doc[i].get_text()
        except Exception:
            continue
        chunks.append(t)
        total += len(t)
    return "\n".join(chunks)
