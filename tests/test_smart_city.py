"""Tests for extractors/smart_city.py.

Extractors are pure (CONTRACT.md): no database, no network. Most of these
tests build a small synthetic PDF with pymupdf and feed it straight to the
extractor, the way tests/test_catalogue_extractors.py does for the other
extractors this corpus family shares a testing style with -- entirely
synthetic content, since the real document names a client and a place the
public repo must never carry (CONTRACT.md's ground rule; see the module
docstring in extractors/smart_city.py for why the real anchors it searches
for are themselves written to avoid that).

One test (test_write_extraction_roundtrip) exercises the full write path
against the throwaway test database tests/conftest.py points DATABASE_URL
at. Its fixture slug is prefixed 'g-sc-' per the task brief, keeping it
clearly separate from any real corpus slug.

Run directly:
    ./.venv/bin/python -m pytest tests/test_smart_city.py -v
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf
import pytest

from tools.pipeline import DocumentContext
from extractors.smart_city import SMART_CITY, _printed_label_for_pdf_index, _RUBRIC_AXES

SLUG = "g-sc-fixture"


def _build_fixture_pdf(path: Path) -> None:
    doc = pymupdf.open()

    # page 0: cover, portrait -- not spread-paginated, mirrors the real
    # document's own cover/back-cover pages, which carry no structure.
    doc.new_page(width=595, height=842).insert_text((50, 50), "COVER", fontsize=10)

    # page 1: front-matter spread. The Contents table lives in the right
    # half (x > mid), each row as three separate words (code, title, printed
    # page) at the same y -- exactly how the real front-matter page lays
    # out (see extractors/smart_city.py's _build_contents_nodes docstring).
    p1 = doc.new_page(width=1191, height=842)
    rows = [
        ("1", "INTRODUCTION", "1", 155),
        ("2", "OVERVIEW", "3", 200),
        ("2.1", "Widgets", "5", 240),
        ("3", "DEEP DIVE", "7", 300),
        ("3.2", "People", "9", 340),
        ("3.3", "Solutions", "11", 380),
    ]
    for code, title, page_no, y in rows:
        p1.insert_text((618, y), code, fontsize=9)
        p1.insert_text((660, y), title, fontsize=9)
        p1.insert_text((1150, y), page_no, fontsize=9)

    # pages 2-3 (0-based): the ladder phrase, once per page, with a
    # different sustainability-principle name each time plus one repeat --
    # mirrors the real report restating it almost verbatim in two places.
    doc.new_page(width=1191, height=842).insert_text(
        (50, 100),
        "Contribution to objectives defined under the alpha-test sustainability "
        "principle - None, Minimal, Significant, Transformational",
        fontsize=8,
    )
    p3 = doc.new_page(width=1191, height=842)
    p3.insert_text(
        (50, 100),
        "Contribution to objectives defined under the alpha-test sustainability "
        "principle - None, Minimal, Significant, Transformational",
        fontsize=8,
    )
    p3.insert_text(
        (50, 130),
        "Contribution to objectives defined under the beta-test sustainability "
        "principle - None, Minimal, Significant, Transformational",
        fontsize=8,
    )

    # page 4: rubric page A -- only needs the 'Weighting' column header the
    # extractor uses to confirm the page before the anchor page belongs to
    # the same table.
    doc.new_page(width=1191, height=842).insert_text((50, 50), "Criteria Description Weighting", fontsize=8)

    # page 5: rubric page B. Two axes only (CAPEX to buy, OPEX) -- the other
    # ~24 real ones are deliberately absent so the "not found" warning path
    # gets exercised too, not just the happy path.
    p5 = doc.new_page(width=1191, height=842)
    p5.insert_text(
        (50, 50),
        "CAPEX to buy High qty and/or high cost of solution = 0, "
        "low qty and/or low cost of solution = 5 10%",
        fontsize=8,
    )
    p5.insert_text(
        (50, 90),
        "OPEX High life cycle cost = 0, low life cycle cost = 5 5%",
        fontsize=8,
    )

    # page 6: the persona table -- header words at the exact tokens the
    # extractor keys off (see _extract_personas), three rows in three
    # x-bands, a Figure caption marking the table's bottom edge.
    p6 = doc.new_page(width=1191, height=842)
    # the extractor locates this page by the literal "Persona Group" caption
    # text, then keys column bands off the standalone "Persona"/"Key"/
    # "Technology" header words -- both need to be present.
    p6.insert_text((300, 200), "Persona Group", fontsize=9)
    p6.insert_text((450, 200), "Key", fontsize=9)
    p6.insert_text((600, 200), "Technology", fontsize=9)
    for i, (name, asset, tech) in enumerate([
        ("Explorer", "AssetA", "TechA"),
        ("Commuter", "AssetB", "TechB"),
        ("Resident", "AssetC", "TechC"),
    ]):
        y = 245 + i * 40
        p6.insert_text((300, y), name, fontsize=8)
        p6.insert_text((450, y), asset, fontsize=8)
        p6.insert_text((600, y), tech, fontsize=8)
    p6.insert_text((300, 360), "Figure 3.2: test caption", fontsize=7)

    # page 7: an external-document reference mention.
    doc.new_page(width=1191, height=842).insert_text(
        (50, 50), "Refer to TEST-DOC-CODE-Example_File for details.", fontsize=8
    )

    doc.save(path)
    doc.close()


def _ctx(path: Path, slug: str = SLUG) -> DocumentContext:
    doc = pymupdf.open(path)
    page_count = doc.page_count
    doc.close()
    return DocumentContext(
        document_id="doc-g-sc-1", slug=slug, path=path, doc_kind="solutions_framework",
        page_count=page_count, pages=[], meta={"content_status": "real"},
    )


def test_doc_kinds_and_purity():
    assert SMART_CITY.doc_kinds == ("solutions_framework",)
    assert not hasattr(SMART_CITY, "conn")
    assert "db" not in dir(SMART_CITY)


def test_printed_label_formula():
    # printed page 1 is the left half of the first body page (pdf index 2,
    # 0-based) -- verified against the real document's own footer numbers
    # for every one of its 117 body pages (see the module docstring).
    assert _printed_label_for_pdf_index(2) == "1 / 2"
    assert _printed_label_for_pdf_index(3) == "3 / 4"
    assert _printed_label_for_pdf_index(1) is None  # before the body


def test_extractor_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fixture.pdf"
        _build_fixture_pdf(path)
        ex = SMART_CITY.extract(_ctx(path))

    # -- structure: the Contents table becomes a node tree ------------------
    by_code = {n.code: n for n in ex.nodes if n.code}
    assert {"1", "2", "2.1", "3", "3.2", "3.3"} <= set(by_code)
    assert by_code["2.1"].parent_ref == by_code["2"].ref
    assert by_code["3.2"].parent_ref == by_code["3"].ref
    assert by_code["2"].node_kind == "chapter"
    assert by_code["2.1"].node_kind == "section"

    # -- the rating ladder ----------------------------------------------------
    assert ex.rating_scales and ex.rating_scales[0]["slug"] == "smart-city-alignment-ladder"
    level_names = [r["name"] for r in sorted(ex.rating_levels, key=lambda r: r["ordinal"])]
    assert level_names == ["None", "Minimal", "Significant", "Transformational"]

    ladder_items = {it.title: it for it in ex.items if it.item_type == "requirement"}
    assert "Contribution ladder: alpha-test sustainability principle" in ladder_items
    assert "Contribution ladder: beta-test sustainability principle" in ladder_items
    alpha = ladder_items["Contribution ladder: alpha-test sustainability principle"]
    assert alpha.payload["target_text"] == "None, Minimal, Significant, Transformational"
    assert alpha.payload["requirement_kind"] == "graded"
    # alpha appears on both fixture pages 2 and 3 (0-based) -- both citations kept.
    assert {c.page_index for c in alpha.citations} == {3, 4}
    assert all(c.printed_page_label for c in alpha.citations)
    beta = ladder_items["Contribution ladder: beta-test sustainability principle"]
    assert len(beta.citations) == 1

    # -- the solution rubric ---------------------------------------------------
    dv_by_id = {dv["id"]: dv for dv in ex.design_variables}
    assert set(dv_by_id) == {"sc_capex_to_buy", "sc_opex"}
    values_by_var: dict[str, list[dict]] = {}
    for v in ex.design_variable_values:
        values_by_var.setdefault(v["variable_id"], []).append(v)
    capex_labels = {v["ordinal"]: v["label"] for v in values_by_var["sc_capex_to_buy"]}
    assert capex_labels[0] == "High qty and/or high cost of solution = 0 (weighting 10%)"
    assert capex_labels[5] == "low qty and/or low cost of solution = 5 (weighting 10%)"
    opex_labels = {v["ordinal"]: v["label"] for v in values_by_var["sc_opex"]}
    assert opex_labels[0] == "High life cycle cost = 0 (weighting 5%)"
    assert opex_labels[5] == "low life cycle cost = 5 (weighting 5%)"

    # every axis this fixture didn't provide text for warns instead of guessing
    missing_axis_names = {name for _id, _anchor, name in _RUBRIC_AXES} - {"CAPEX to buy", "OPEX"}
    warned_names = {w for w in ex.warnings for name in missing_axis_names if name in w}
    assert len(warned_names) >= 1

    guidance_items = [it for it in ex.items if it.item_type == "guidance"]
    assert len(guidance_items) == 1
    assert "CAPEX to buy" in guidance_items[0].payload["body_md"]

    # -- personas ---------------------------------------------------------------
    personas = {it.title: it for it in ex.items if it.item_type == "pattern"}
    assert set(personas) == {"Persona: Explorer", "Persona: Commuter", "Persona: Resident"}
    explorer = personas["Persona: Explorer"]
    assert explorer.payload["pattern_kind"] == "persona"
    assert explorer.payload["attributes"]["key_assets_or_districts"] == "AssetA"
    assert explorer.payload["attributes"]["technology_needs"] == "TechA"

    # -- external references -----------------------------------------------------
    assert any(r.raw_text == "TEST-DOC-CODE-Example_File" for r in ex.references)

    # -- nothing lost silently: the rubric's per-solution scoring gap is reported
    assert any("external workbook" in w for w in ex.warnings)


def test_missing_structures_warn_not_raise():
    """A document with none of the four recognised structures should not
    raise -- every extraction stage declines cleanly with a warning, the
    same conservative behaviour compliance_table.py has for a document
    without its appendix (see CONTRACT.md)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "empty.pdf"
        doc = pymupdf.open()
        doc.new_page(width=595, height=842).insert_text((50, 50), "cover", fontsize=10)
        doc.new_page(width=1191, height=842).insert_text((700, 200), "nothing here", fontsize=10)
        doc.save(path)
        doc.close()

        ex = SMART_CITY.extract(_ctx(path))

    assert ex.items == []
    assert any("Contents table not recognised" in w for w in ex.warnings)
    assert any("ladder" in w or "Ladder" in w for w in ex.warnings)
    assert any("rubric" in w for w in ex.warnings)
    assert any("persona" in w for w in ex.warnings)


def test_write_extraction_roundtrip():
    """Full pure-extract -> write_extraction round trip against the
    throwaway test database (tests/conftest.py redirects DATABASE_URL)."""
    from tools import db
    from tools.write_extraction import write_extraction

    slug = "g-sc-roundtrip"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fixture.pdf"
        _build_fixture_pdf(path)
        ctx = _ctx(path, slug=slug)
        doc = pymupdf.open(path)
        page_count = doc.page_count
        doc.close()
        ex = SMART_CITY.extract(ctx)

        with db.transaction() as conn:
            conn.execute("DELETE FROM source_document WHERE slug = %s", (slug,))
            row = conn.execute(
                "INSERT INTO source_document (slug, doc_kind, sha256, page_count) "
                "VALUES (%s, 'solutions_framework', %s, %s) RETURNING id",
                (slug, "d" * 64, page_count),
            ).fetchone()
            document_id = row["id"]
            for i in range(page_count):
                conn.execute(
                    "INSERT INTO source_page (document_id, page_index, text, content_status) "
                    "VALUES (%s, %s, '', 'real')",
                    (document_id, i + 1),
                )

        with db.transaction() as conn:
            counts = write_extraction(conn, document_id, ex)

        assert counts["items"] == len(ex.items)
        assert counts["design_variables"] == 2

        # idempotent: re-running clears and rewrites, not duplicates
        with db.transaction() as conn:
            counts2 = write_extraction(conn, document_id, ex)
        assert counts2["items"] == counts["items"]

        with db.transaction() as conn:
            conn.execute("DELETE FROM source_document WHERE slug = %s", (slug,))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
