"""Tests for extractors/crib_sheet.py, extractors/compliance_table.py and
extractors/generic.py against the REAL corpus.

Extractors are pure (see tools/pipeline.py) so these tests need no database
and no other pipeline stage -- a DocumentContext is built directly from the
PDF with pymupdf, the same way the real ingest_document/build_structure
stages would populate it.

The source folders (Excel/, PDF/, "Report - Guidance/", "Table - PDF/") are
private and gitignored (see CONTRACT.md), so this whole module is skipped
when they are absent rather than failing a fresh public clone.

Documents are located by shape and by sniffing page text for a generic,
non-identifying marker -- never by their real filename -- matching the
convention in tests/test_ingest_document.py.

Run directly:
    ./.venv/bin/python -m pytest tests/test_grid_extractors.py -v
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf
import pytest

from tools.pipeline import DocumentContext
from extractors.crib_sheet import CRIB_SHEET
from extractors.compliance_table import COMPLIANCE_TABLE
from extractors.generic import GENERIC

REPO_ROOT = Path(__file__).resolve().parent.parent
CRIB_DIR = REPO_ROOT / "Table - PDF"
PDF_DIR = REPO_ROOT / "PDF"
GUIDANCE_DIR = REPO_ROOT / "Report - Guidance"
EXCEL_DIR = REPO_ROOT / "Excel"

CORPUS_PRESENT = all(d.is_dir() for d in (CRIB_DIR, PDF_DIR, GUIDANCE_DIR, EXCEL_DIR))

pytestmark = pytest.mark.skipif(
    not CORPUS_PRESENT,
    reason="real corpus not present in this checkout (Excel/PDF/Report - Guidance/"
           "Table - PDF are gitignored source material)",
)


def _page_text(path: Path, index: int = 0) -> str:
    doc = pymupdf.open(path)
    try:
        if index >= len(doc):
            return ""
        # these sheets wrap headings across lines mid-phrase ('Identification
        # of \nClimate Hazard and \nRisks'); flatten whitespace so a marker
        # search isn't defeated by where the PDF happened to wrap a line.
        return re.sub(r"\s+", " ", doc[index].get_text("text"))
    finally:
        doc.close()


def _find_by_marker(files: list[Path], marker: str, *, page: int = 0) -> Path | None:
    marker = re.sub(r"\s+", " ", marker).lower()
    for f in files:
        if marker in _page_text(f, page).lower():
            return f
    return None


def _find_by_marker_anywhere(files: list[Path], marker: str, *, max_pages: int = 120) -> Path | None:
    marker = re.sub(r"\s+", " ", marker).lower()
    for f in files:
        doc = pymupdf.open(f)
        try:
            for i in range(min(len(doc), max_pages)):
                if marker in re.sub(r"\s+", " ", doc[i].get_text("text")).lower():
                    return f
        finally:
            doc.close()
    return None


def _crib_files() -> list[Path]:
    files = sorted(CRIB_DIR.glob("*.pdf"))
    if not files:
        pytest.skip("no crib sheet PDFs found under Table - PDF/")
    return files


def _build_ctx(slug: str, path: Path, doc_kind: str) -> DocumentContext:
    doc = pymupdf.open(path)
    try:
        page_count = len(doc)
    finally:
        doc.close()
    return DocumentContext(document_id="test", slug=slug, path=path, doc_kind=doc_kind,
                            page_count=page_count, pages=[], meta={"content_status": "real"})


def _pdf_pages(path: Path) -> list[dict]:
    doc = pymupdf.open(path)
    try:
        return [{"page_index": i + 1, "text": p.get_text("text"), "content_status": "real",
                  "printed_page_label": None} for i, p in enumerate(doc)]
    finally:
        doc.close()


# ── crib_sheet.py ────────────────────────────────────────────────────────


def test_crib_sheet_runs_clean_on_every_sheet():
    for path in _crib_files():
        ctx = _build_ctx("test-crib", path, "crib_sheet")
        ext = CRIB_SHEET.extract(ctx)
        assert ext.criteria, f"no criteria extracted from {path.name}"
        assert len(ext.rating_levels) == 4, f"expected 4 rating levels, got {len(ext.rating_levels)}"
        levels_by_ordinal = {l["ordinal"]: l for l in ext.rating_levels}
        assert levels_by_ordinal[1]["name"] is None, (
            "LEVEL 1 should be unlabelled per the source sheets"
        )
        requirement_items = [it for it in ext.items if it.item_type == "requirement"]
        assert requirement_items, f"no requirement statements extracted from {path.name}"
        for it in requirement_items:
            assert it.payload["target_text"], "requirement.target_text must never be empty"
            assert it.citations, "every item needs a citation back to its page"


def test_crib_sheet_embodied_carbon_typology_benchmarks():
    """Known-good check: the embodied-carbon sheet's 2030 typology
    benchmarks include Flats 380, Office 550, Hotel 500, Culture &
    Entertainment 640 kgCO2e/m2GIA. If this drifts, the typology-table
    geometry recovery is broken."""
    path = _find_by_marker(_crib_files(), "embodied carbon")
    if path is None:
        pytest.skip("no crib sheet sniffed as the embodied-carbon sheet")
    ctx = _build_ctx("test-crib-embodied", path, "crib_sheet")
    ext = CRIB_SHEET.extract(ctx)

    by_use_year: dict[tuple[str, int], float] = {}
    for it in ext.items:
        if it.item_type != "benchmark":
            continue
        p = it.payload
        if p.get("target_year") and p.get("value_numeric") is not None:
            by_use_year[(p["building_use_id"], p["target_year"])] = p["value_numeric"]

    expected = {"flats": 380, "hotel": 500}
    for use_id, year_val in expected.items():
        match = next((v for (u, y), v in by_use_year.items() if use_id in u and y == 2030), None)
        assert match == year_val, f"{use_id} 2030 embodied-carbon target: expected {year_val}, got {match}"

    culture = next((v for (u, y), v in by_use_year.items()
                     if "culture" in u and "entertainment" in u and y == 2030), None)
    assert culture == 640, f"culture & entertainment 2030 target: expected 640, got {culture}"

    office = next((v for (u, y), v in by_use_year.items() if "office" in u and y == 2030), None)
    assert office == 550, f"office 2030 target: expected 550, got {office}"


def test_crib_sheet_climate_resilience_outlier_has_no_sub_criteria():
    """crib-climate-resilience is the documented outlier: different column
    geometry, no sub-criteria column. Every criterion should come out flat
    (no children) unlike the other five sheets."""
    path = _find_by_marker(_crib_files(), "Identification of Climate Hazard", page=1)
    if path is None:
        pytest.skip("no crib sheet sniffed as the climate-resilience sheet")
    ctx = _build_ctx("test-crib-climate", path, "crib_sheet")
    ext = CRIB_SHEET.extract(ctx)
    assert ext.criteria, "expected criteria on the climate-resilience sheet"
    assert all(c["parent_ref"] is None for c in ext.criteria), (
        "climate-resilience should have no sub-criteria (documented outlier)"
    )


def test_crib_sheet_never_drops_a_module_reference_silently():
    for path in _crib_files():
        ctx = _build_ctx("test-crib-refs", path, "crib_sheet")
        ext = CRIB_SHEET.extract(ctx)
        for ref in ext.references:
            assert ref.raw_text.strip()
            assert ref.ref_kind in ("module_chapter", "page")


# ── compliance_table.py ──────────────────────────────────────────────────


def _framework_files() -> list[Path]:
    files = sorted(GUIDANCE_DIR.glob("*.pdf")) + sorted(PDF_DIR.glob("*.pdf"))
    if not files:
        pytest.skip("no framework volume PDFs found")
    return files


def test_compliance_table_runs_clean_on_every_framework_volume():
    for path in _framework_files():
        # only the framework/implementation_plan-shaped volumes are this
        # extractor's business; skip the typology catalogue and the deck.
        text0 = _page_text(path, 0)
        if "sustainability" not in text0.lower() and "smart city" not in text0.lower():
            continue
        ctx = _build_ctx("test-framework", path, "framework")
        ext = COMPLIANCE_TABLE.extract(ctx)  # must not raise
        for it in ext.items:
            assert it.payload["target_text"] or it.payload["is_deliverable"], (
                "requirement.target_text is NOT NULL per the schema"
            )


def test_compliance_table_appendix_has_43_unique_strategy_codes():
    """The highest-value artefact in the corpus: a 43-row compliance
    appendix, reproduced several times across the volume. Codes must be
    deduplicated to one row each."""
    path = _find_by_marker_anywhere(_framework_files(), "compliance requirements checklist")
    if path is None:
        pytest.skip("no framework volume sniffed as carrying the compliance appendix")
    ctx = _build_ctx("test-framework-e2", path, "implementation_plan")
    ext = COMPLIANCE_TABLE.extract(ctx)

    requirement_items = [it for it in ext.items
                          if it.payload.get("requirement_kind") == "compliance"]
    codes = [it.payload["criterion_id"] for it in requirement_items]
    assert len(codes) == len(set(codes)), "compliance appendix codes must be deduplicated"
    assert len(codes) == 43, f"expected 43 unique strategy codes, got {len(codes)}"

    for it in requirement_items:
        target = it.payload["target_text"].strip().upper()
        if it.payload["is_deliverable"]:
            assert target == "Y"
            assert it.payload["deliverable_name"]
        else:
            assert target != ""


def test_compliance_table_never_fails_on_a_document_without_the_appendix():
    """framework-vol-a10-smart-city (and framework-vol-e1's narrative body)
    have no ruled compliance appendix -- the extractor must degrade to a
    warning, not an exception."""
    candidates = [p for p in _framework_files()
                  if _find_by_marker_anywhere([p], "compliance requirements checklist") is None]
    if not candidates:
        pytest.skip("every framework volume in this checkout has the appendix")
    ctx = _build_ctx("test-framework-no-appendix", candidates[0], "framework")
    ext = COMPLIANCE_TABLE.extract(ctx)  # must not raise
    assert isinstance(ext.items, list)


# ── generic.py ───────────────────────────────────────────────────────────


def test_generic_never_raises_on_degenerate_input():
    degenerate_page_sets = [
        [],
        [{"page_index": 1, "text": None, "content_status": "wip"}],
        [{"text": "no page_index at all"}],
        [{"page_index": 1, "text": "TEMPLATE ONLY", "content_status": "real"}],
    ]
    for pages in degenerate_page_sets:
        ctx = DocumentContext(document_id="test", slug="test-generic-degenerate",
                               path=Path("/does/not/exist.pdf"), doc_kind="unknown",
                               page_count=len(pages), pages=pages, meta={})
        ext = GENERIC.extract(ctx)  # must not raise
        assert isinstance(ext.items, list)


def test_generic_skips_non_real_and_placeholder_pages():
    files = sorted(GUIDANCE_DIR.glob("*.pdf"))
    if not files:
        pytest.skip("no guideline-report PDFs found under Report - Guidance/")
    # the large multi-hundred-page typology catalogue is the one with a
    # documented placeholder run; the short slide deck is the one with none.
    catalogue = max(files, key=lambda f: _doc_page_count(f))
    pages = _pdf_pages(catalogue)
    if len(pages) < 50:
        pytest.skip("no large guideline-report catalogue found to test placeholder skipping on")

    ctx = DocumentContext(document_id="test", slug="test-generic-catalogue", path=catalogue,
                           doc_kind="guideline_report", page_count=len(pages), pages=pages, meta={})
    ext = GENERIC.extract(ctx)
    n_guidance = sum(1 for it in ext.items if it.item_type == "guidance")
    assert n_guidance > 0
    assert n_guidance < len(pages), (
        "expected some pages to be skipped as placeholder/empty in the documented "
        "~24% placeholder catalogue, but every page produced an item"
    )
    for it in ext.items:
        assert it.content_status == "real"
        assert it.citations


def test_generic_marks_wip_page_as_skipped_when_upstream_missed_it():
    files = sorted(GUIDANCE_DIR.glob("*.pdf"))
    catalogue = max(files, key=lambda f: _doc_page_count(f)) if files else None
    if catalogue is None or _doc_page_count(catalogue) < 50:
        pytest.skip("no large guideline-report catalogue found")
    wip_page = _find_page_containing(catalogue, "WIP")
    if wip_page is None:
        pytest.skip("no WIP-stamped page found in this catalogue")
    pages = _pdf_pages(catalogue)
    # simulate upstream not having flagged it yet
    for p in pages:
        p["content_status"] = "real"
    ctx = DocumentContext(document_id="test", slug="test-generic-wip", path=catalogue,
                           doc_kind="guideline_report", page_count=len(pages), pages=pages, meta={})
    ext = GENERIC.extract(ctx)
    covered_pages = {c.page_index for it in ext.items for c in it.citations}
    assert wip_page not in covered_pages, (
        "a WIP-stamped page must not surface as a guidance item even if "
        "content_status upstream says 'real'"
    )


def _doc_page_count(path: Path) -> int:
    doc = pymupdf.open(path)
    try:
        return len(doc)
    finally:
        doc.close()


def _find_page_containing(path: Path, marker: str) -> int | None:
    doc = pymupdf.open(path)
    try:
        for i, p in enumerate(doc):
            if marker in p.get_text("text"):
                return i + 1
    finally:
        doc.close()
    return None
