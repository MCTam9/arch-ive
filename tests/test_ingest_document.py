"""Integration tests for classify_document.py, ingest_document.py and
build_structure.py against the REAL corpus (Excel/, PDF/, "Report -
Guidance/", "Table - PDF/").

Those folders are private source material and are never committed (see
CONTRACT.md), so a fresh clone of the public repo will not have them. The
whole module is skipped in that case rather than failing -- there is nothing
meaningful to assert without the files themselves. Where this checkout does
have the corpus (the normal case while building this pipeline), these tests
exercise the real functions against the real, differently-shaped documents
they were written against: idempotent upserts, keyed on sha256, so re-running
this file never duplicates rows.

Run directly:
    ./.venv/bin/python -m pytest tests/test_ingest_document.py -v
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
import pytest

from tools import db
from tools.build_structure import build_structure
from tools.classify_document import classify
from tools.ingest_document import extract_pages, register_document

REPO_ROOT = Path(__file__).resolve().parent.parent
CRIB_DIR = REPO_ROOT / "Table - PDF"
PDF_DIR = REPO_ROOT / "PDF"
GUIDANCE_DIR = REPO_ROOT / "Report - Guidance"
EXCEL_DIR = REPO_ROOT / "Excel"

CORPUS_PRESENT = all(d.is_dir() for d in (CRIB_DIR, PDF_DIR, GUIDANCE_DIR, EXCEL_DIR))

pytestmark = pytest.mark.skipif(
    not CORPUS_PRESENT,
    reason="real corpus not present in this checkout (Excel/PDF/Report - Guidance/Table - PDF are gitignored source material)",
)


# Real filenames are private source material and must never end up in a
# tracked file (see CONTRACT.md), so every document under test is located by
# its shape -- folder, extension, sheet names, classify()'s own verdict --
# never by name. Slugs are the one identifier this file is allowed to spell
# out, and they mirror private/documents.yaml.

def _discover_crib_sheet() -> Path:
    files = sorted(CRIB_DIR.glob("*.pdf"))
    if not files:
        pytest.skip("no crib sheet PDFs found under Table - PDF/")
    return files[0]


def _discover_spread_framework() -> Path:
    files = sorted(PDF_DIR.glob("*.pdf"))
    if not files:
        pytest.skip("no PDFs found under PDF/")
    return files[0]


def _discover_calculator_with_formulas() -> Path:
    """The xlsx workbook whose primary sheet is a fee calculation."""
    for f in sorted(EXCEL_DIR.glob("*.xlsx")):
        try:
            wb = openpyxl.load_workbook(f, read_only=True)
            names = wb.sheetnames
            wb.close()
        except Exception:
            continue
        if "Fee calculation" in names:
            return f
    pytest.skip("no xlsx workbook with a 'Fee calculation' sheet found under Excel/")


def _discover_guidance_by_shape() -> dict[str, Path]:
    """Bucket every PDF under Report - Guidance/ by classify()'s own verdict."""
    buckets: dict[str, Path] = {}
    for f in sorted(GUIDANCE_DIR.glob("*.pdf")):
        doc_kind, _ = classify(f)
        buckets.setdefault(doc_kind, f)
    return buckets


if CORPUS_PRESENT:
    _guidance = _discover_guidance_by_shape()
    # test-only slug: discovery picks whichever crib sheet sorts first, and
    # every crib sheet shares the same 2-page A3 shape these tests check, so
    # this deliberately doesn't claim to be any single real document's slug.
    CRIB = ("test-crib-sheet", _discover_crib_sheet())
    DECK = ("deck-early-stage-design", _guidance.get("deck"))
    CALC = ("calc-fees", _discover_calculator_with_formulas())
    GUIDELINE = ("typology-multifamily", _guidance.get("guideline_report"))
    SPREAD_FRAMEWORK = ("framework-vol-a10-smart-city", _discover_spread_framework())
    # the no-bookmark fallback exercises 'framework' specifically (the
    # implementation_plan sibling in this folder also has no bookmarks, but
    # only one document is needed to prove the fallback works)
    NO_BOOKMARK_FRAMEWORK = ("framework-vol-e1", _guidance.get("framework"))
    if any(p is None for _, p in (DECK, GUIDELINE, NO_BOOKMARK_FRAMEWORK)):
        pytestmark = pytest.mark.skip(
            reason="Report - Guidance/ doesn't contain one document of each expected shape"
        )
else:
    CRIB = DECK = CALC = GUIDELINE = SPREAD_FRAMEWORK = NO_BOOKMARK_FRAMEWORK = (None, None)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _register(slug: str, path: Path, doc_kind: str) -> str:
    """register_document + extract_pages, idempotent on the file's sha256."""
    with db.transaction() as conn:
        document_id = register_document(
            conn, path=path, sha256=_sha256(path), slug=slug, doc_kind=doc_kind,
            meta={"content_status": "real"},
        )
        extract_pages(conn, document_id, path)
    return document_id


# ── classification ───────────────────────────────────────────────────────


def test_classify_crib_sheet():
    doc_kind, confidence = classify(CRIB[1])
    assert doc_kind == "crib_sheet"
    assert confidence >= 0.8


def test_classify_deck():
    doc_kind, confidence = classify(DECK[1])
    assert doc_kind == "deck"
    assert confidence >= 0.6


def test_classify_calculator():
    doc_kind, confidence = classify(CALC[1])
    assert doc_kind == "calculator"
    assert confidence >= 0.9


def test_classify_guideline_report():
    doc_kind, confidence = classify(GUIDELINE[1])
    assert doc_kind == "guideline_report"
    assert confidence >= 0.7


def test_classify_spread_paginated_framework():
    doc_kind, confidence = classify(SPREAD_FRAMEWORK[1])
    assert doc_kind in ("framework", "implementation_plan")
    assert confidence >= 0.5


def test_classify_never_raises_on_garbage(tmp_path):
    junk = tmp_path / "not_a_real_document.pdf"
    junk.write_bytes(b"this is not a pdf")
    doc_kind, confidence = classify(junk)
    assert doc_kind == "unknown"
    assert confidence == 0.0


# ── register_document + extract_pages ────────────────────────────────────


def test_crib_sheet_page_count_and_shape():
    slug, path = CRIB
    document_id = _register(slug, path, "crib_sheet")
    with db.connect() as conn:
        pages = db.all_rows(
            conn, "SELECT page_index, width_pt, height_pt FROM source_page "
                  "WHERE document_id = %s ORDER BY page_index", (document_id,),
        )
    assert len(pages) == 2
    for p in pages:
        assert 1180 <= float(p["width_pt"]) <= 1200
        assert 830 <= float(p["height_pt"]) <= 850


def test_xlsx_keeps_formulas_and_infers_roles():
    slug, path = CALC
    document_id = _register(slug, path, "calculator")
    with db.connect() as conn:
        sheets = db.all_rows(
            conn, "SELECT id, name FROM spreadsheet_sheet WHERE document_id = %s ORDER BY ordinal",
            (document_id,),
        )
        assert any(s["name"] == "Fee calculation" for s in sheets)
        sheet_id = next(s["id"] for s in sheets if s["name"] == "Fee calculation")

        formula_cells = db.scalar(
            conn, "SELECT count(*) FROM spreadsheet_cell WHERE sheet_id = %s AND formula IS NOT NULL",
            (sheet_id,),
        )
        assert formula_cells > 0, "formulas must survive ingestion -- that's what makes this a template"

        roles = {r["role"] for r in db.all_rows(
            conn, "SELECT DISTINCT role FROM spreadsheet_cell WHERE sheet_id = %s", (sheet_id,),
        )}
        assert {"label", "input", "calc"} <= roles

        # a cell holding a formula always keeps the formula text alongside
        # whatever value it last resolved to
        row = db.one(
            conn, "SELECT formula, value_numeric FROM spreadsheet_cell "
                  "WHERE sheet_id = %s AND formula IS NOT NULL LIMIT 1", (sheet_id,),
        )
        assert row["formula"].startswith("=")


def test_spread_pagination_detected_on_framework_volume():
    """framework-vol-a10-smart-city renders two printed pages per PDF page."""
    slug, path = SPREAD_FRAMEWORK
    document_id = _register(slug, path, "framework")
    with db.connect() as conn:
        labeled = db.all_rows(
            conn, "SELECT printed_page_label FROM source_page "
                  "WHERE document_id = %s AND printed_page_label IS NOT NULL", (document_id,),
        )
    assert len(labeled) > 50, "most pages of this volume are printed spreads"
    for row in labeled:
        assert " / " in row["printed_page_label"]
        lo, hi = row["printed_page_label"].split(" / ")
        assert int(lo) < int(hi)


def test_placeholder_pages_detected_in_typology_catalogue():
    """~24% of this 413-page catalogue is lorem/TEMPLATE ONLY filler or WIP
    stamps (private/documents.yaml notes p296-396 and the p109-140 prototype
    run); it must be flagged, never served as real guidance."""
    slug, path = GUIDELINE
    document_id = _register(slug, path, "guideline_report")
    with db.connect() as conn:
        page_count = db.scalar(conn, "SELECT page_count FROM source_document WHERE id = %s", (document_id,))
        by_status = {
            r["content_status"]: r["c"] for r in db.all_rows(
                conn, "SELECT content_status, count(*) c FROM source_page "
                      "WHERE document_id = %s GROUP BY content_status", (document_id,),
            )
        }
        doc_status = db.scalar(conn, "SELECT content_status FROM source_document WHERE id = %s", (document_id,))

    wip = by_status.get("wip", 0)
    non_real = by_status.get("wip", 0) + by_status.get("template", 0) + by_status.get("lorem", 0)

    assert 30 <= wip <= 45, f"expected ~37 WIP-stamped pages, got {wip}"
    assert 90 <= non_real <= 140, f"expected roughly 90-140 non-real pages, got {non_real}"
    assert doc_status == "mixed", "a document that's ~1/3 placeholder must be flagged 'mixed'"


def test_extract_pages_never_raises_and_is_idempotent():
    """Re-running extract_pages on the same document (same sha256) must not
    duplicate source_page rows -- (document_id, page_index) is unique."""
    slug, path = CRIB
    document_id = _register(slug, path, "crib_sheet")  # first run
    document_id_again = _register(slug, path, "crib_sheet")  # second run
    assert document_id == document_id_again
    with db.connect() as conn:
        count = db.scalar(conn, "SELECT count(*) FROM source_page WHERE document_id = %s", (document_id,))
    assert count == 2


# ── build_structure ───────────────────────────────────────────────────────


def test_bookmark_rich_document_produces_deep_tree():
    """typology-multifamily carries 261 PDF bookmarks, three levels deep."""
    slug, path = GUIDELINE
    document_id = _register(slug, path, "guideline_report")
    with db.transaction() as conn:
        node_count = build_structure(conn, document_id, path)
    with db.connect() as conn:
        max_depth = db.scalar(conn, "SELECT max(nlevel(path)) FROM doc_node WHERE document_id = %s", (document_id,))
        roots = db.scalar(conn, "SELECT count(*) FROM doc_node WHERE document_id = %s AND parent_id IS NULL", (document_id,))
    assert node_count == 261
    assert max_depth is not None and max_depth >= 3
    assert roots > 0


def test_no_bookmark_document_still_produces_a_sensible_tree():
    """framework-vol-e1 ships with zero PDF bookmarks; build_structure must
    fall back to font-size + section-code heading detection and still
    produce a non-trivial, page-anchored tree."""
    slug, path = NO_BOOKMARK_FRAMEWORK
    document_id = _register(slug, path, "framework")
    with db.transaction() as conn:
        node_count = build_structure(conn, document_id, path)
    with db.connect() as conn:
        nodes = db.all_rows(
            conn, "SELECT node_kind, code, page_from, path FROM doc_node WHERE document_id = %s",
            (document_id,),
        )

    assert node_count > 10, "a 100+ page document with real section codes should yield more than a handful of nodes"
    assert all(n["page_from"] is not None for n in nodes), "every heading-detected node must cite a page"
    # at least some of the criterion codes (e.g. 'NF1.1', 'PC4.2') this
    # volume uses should have been picked up as node codes
    assert any(n["code"] for n in nodes)
    paths = [n["path"] for n in nodes]
    assert len(paths) == len(set(paths)), "ltree paths must be unique per document"


def test_deck_produces_one_slide_node_per_slide():
    slug, path = DECK
    document_id = _register(slug, path, "deck")
    with db.transaction() as conn:
        node_count = build_structure(conn, document_id, path)
    with db.connect() as conn:
        kinds = {r["node_kind"] for r in db.all_rows(
            conn, "SELECT DISTINCT node_kind FROM doc_node WHERE document_id = %s", (document_id,),
        )}
    assert kinds == {"slide"}
    assert node_count > 0


def test_xlsx_produces_one_node_per_sheet():
    slug, path = CALC
    document_id = _register(slug, path, "calculator")
    with db.transaction() as conn:
        node_count = build_structure(conn, document_id, path)
    with db.connect() as conn:
        sheet_names = {r["code"] for r in db.all_rows(
            conn, "SELECT code FROM doc_node WHERE document_id = %s AND node_kind = 'sheet'", (document_id,),
        )}
    assert node_count == 2
    assert "Fee calculation" in sheet_names


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
