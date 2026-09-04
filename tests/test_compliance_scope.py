"""Tests for the requirement-scope fix in extractors/compliance_table.py.

Before this fix, a code's target reprinted differently under a second
appendix copy (a contractor-role scope-of-work section, or the compliance
checklist) produced a "disagrees, kept the first" warning and the differing
value was discarded. Now every sighting becomes one requirement_scope_
applicability row against the single canonical requirement -- see that
module's docstring for what was verified against the actual PDF.

Builds a two-page fake appendix by hand (no PDF involved -- the extractor's
scope-detection and write_extraction's persistence are exercised
independently) plus a round trip through write_extraction against arch_test.

Run directly against arch_test (tests/conftest.py redirects DATABASE_URL):
    ./.venv/bin/python -m pytest tests/test_compliance_scope.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractors.compliance_table import ComplianceTableExtractor
from tools import db
from tools.pipeline import Extraction, Item
from tools.write_extraction import write_extraction

SLUG = "f-scope-e2"
SHA256 = "f" * 64


def _ensure_document(conn) -> str:
    row = db.one(conn, "SELECT id FROM source_document WHERE sha256 = %s", (SHA256,))
    if row:
        return row["id"]
    row = conn.execute(
        "INSERT INTO source_document (slug, doc_kind, sha256, page_count) "
        "VALUES (%s, 'implementation_plan', %s, 4) RETURNING id",
        (SLUG, SHA256),
    ).fetchone()
    return row["id"]


def _scope_extraction() -> Extraction:
    """Two requirements, each seen under three scope reprints, mirroring the
    real e2 shape: two role sections that agree, one checklist that flips a
    deliverable's 'Y' to 'N/A' and gives a genuinely different value for a
    metered one. Built directly (not via pymupdf) to isolate write_extraction
    from PDF layout -- the PDF-reading side is covered by running the
    extractor against the real corpus (see tools/integration_check.py)."""
    from tools.pipeline import Citation

    ext = Extraction()
    ext.frameworks = [{"ref": "f-scope-fw", "slug": f"{SLUG}-fw", "name": "Test scope framework"}]
    ext.criteria = [{"ref": "crit-RE1.1", "framework_slug": f"{SLUG}-fw", "code": "RE1.1",
                     "title_primary": "Test criterion"}]
    ext.requirement_scopes = [
        {"id": f"{SLUG}-fw-role-a", "framework_slug": f"{SLUG}-fw", "code": "role_a", "title": "Role A", "ordinal": 0},
        {"id": f"{SLUG}-fw-role-b", "framework_slug": f"{SLUG}-fw", "code": "role_b", "title": "Role B", "ordinal": 1},
        {"id": f"{SLUG}-fw-checklist", "framework_slug": f"{SLUG}-fw", "code": "checklist", "title": "Checklist", "ordinal": 2},
    ]

    deliverable = Item(
        item_type="requirement", statement="Some plan developed in line with RE1.1 requirements",
        payload={
            "requirement_kind": "compliance", "criterion_id": "crit-RE1.1",
            "rating_level_id": None, "metric_id": None,
            "target_value": None, "target_text": "Y", "unit_id": None,
            "comparator": "none", "is_deliverable": True,
            "deliverable_name": "Some plan", "parsed_ok": False,
        },
        citations=[Citation(page_index=1)],
    )
    deliverable.scope_applicability = [
        {"scope_id": f"{SLUG}-fw-role-a", "applies": True, "target_text": "Y", "note": "page 1"},
        {"scope_id": f"{SLUG}-fw-role-b", "applies": True, "target_text": "Y", "note": "page 2"},
        {"scope_id": f"{SLUG}-fw-checklist", "applies": False, "target_text": "N/A", "note": "page 3"},
    ]
    ext.items = [deliverable]
    return ext


def _checks() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        results.append((name, cond, detail))

    with db.transaction() as conn:
        document_id = _ensure_document(conn)
        counts = write_extraction(conn, document_id, _scope_extraction())

    check("write reports 3 scope applicability rows",
          counts.get("requirement_scope_applicability") == 3, str(counts))
    check("write reports 3 requirement_scopes upserted",
          counts.get("requirement_scopes") == 3, str(counts))
    check("no warnings", counts["warnings"] == [], str(counts["warnings"]))

    with db.connect() as conn:
        scope_rows = db.all_rows(
            conn,
            """SELECT rs.code, rsa.applies, rsa.target_text
               FROM requirement_scope_applicability rsa
               JOIN requirement_scope rs ON rs.id = rsa.scope_id
               JOIN knowledge_item ki ON ki.id = rsa.knowledge_item_id
               WHERE ki.document_id = %s ORDER BY rs.ordinal""",
            (document_id,),
        )
        check("3 scope rows persisted", len(scope_rows) == 3, str(scope_rows))
        by_code = {r["code"]: r for r in scope_rows}
        check("role_a applies=True target=Y",
              by_code.get("role_a") == {"code": "role_a", "applies": True, "target_text": "Y"},
              str(by_code.get("role_a")))
        check("role_b applies=True target=Y",
              by_code.get("role_b") == {"code": "role_b", "applies": True, "target_text": "Y"},
              str(by_code.get("role_b")))
        check("checklist applies=False target=N/A -- the value the old code discarded",
              by_code.get("checklist") == {"code": "checklist", "applies": False, "target_text": "N/A"},
              str(by_code.get("checklist")))

        # the canonical requirement keeps the FIRST sighting's verbatim target,
        # unaffected by the per-scope rows
        canonical = db.one(
            conn, "SELECT target_text FROM requirement WHERE knowledge_item_id = "
                  "(SELECT id FROM knowledge_item WHERE document_id = %s)", (document_id,),
        )
        check("canonical requirement target_text still 'Y'",
              canonical is not None and canonical["target_text"] == "Y", str(canonical))

        view_rows = db.all_rows(
            conn, "SELECT scope_code, applies, scope_target_text FROM v_requirement_scope_matrix "
                  "WHERE document_slug = %s ORDER BY scope_code", (SLUG,),
        )
        check("v_requirement_scope_matrix returns all 3 rows in one join",
              len(view_rows) == 3, str(view_rows))

    # idempotency: re-running the same extraction must not duplicate rows
    # (knowledge_item is deleted and rebuilt on every write, so the join
    # rows -- which cascade off knowledge_item_id -- must too)
    with db.transaction() as conn:
        write_extraction(conn, document_id, _scope_extraction())

    with db.connect() as conn:
        appl_count2 = db.scalar(
            conn,
            """SELECT count(*) FROM requirement_scope_applicability rsa
               JOIN knowledge_item ki ON ki.id = rsa.knowledge_item_id
               WHERE ki.document_id = %s""",
            (document_id,),
        )
        check("scope applicability rows still == 3 after second write", appl_count2 == 3, str(appl_count2))

        scope_count2 = db.scalar(
            conn, "SELECT count(*) FROM requirement_scope WHERE framework_id = "
                  "(SELECT id FROM framework WHERE slug = %s)", (f"{SLUG}-fw",),
        )
        check("requirement_scope rows still == 3 (upsert, not duplicate)", scope_count2 == 3, str(scope_count2))

    return results


def test_scope_applicability_roundtrip() -> None:
    results = _checks()
    failed = [(n, d) for n, ok, d in results if not ok]
    assert not failed, "\n".join(f"FAIL {n}: {d}" for n, d in failed)


def test_extractor_detects_real_scopes(tmp_path) -> None:
    """The scope-title scanner is regex-driven, not corpus-specific -- verify
    it on a hand-built two-page PDF (role banner, then a checklist heading)
    rather than only against the real corpus."""
    import pymupdf

    doc = pymupdf.open()
    p1 = doc.new_page(width=600, height=800)
    p1.insert_text((50, 50), "Environmental Sustainability Scope of Work")
    p1.insert_text((50, 70), "Role A")
    p1.insert_text((50, 100), "RE1.1")
    p1.insert_text((150, 100), "Some plan developed in line with RE1.1 requirements")
    p1.insert_text((500, 100), "Y")

    p2 = doc.new_page(width=600, height=800)
    p2.insert_text((50, 50), "Fixture Compliance Requirements Checklist")
    p2.insert_text((50, 100), "RE1.1")
    p2.insert_text((150, 100), "Some plan developed in line with RE1.1 requirements")
    p2.insert_text((500, 100), "N/A")

    from extractors.compliance_table import _scan_scope_titles
    scopes = _scan_scope_titles(doc)
    doc.close()

    assert scopes[0] == "Role A", scopes
    assert scopes[1] == "Compliance Requirements Checklist", scopes


if __name__ == "__main__":
    results = _checks()
    ok = True
    for name, passed, detail in results:
        mark = "ok  " if passed else "FAIL"
        print(f"  {mark}  {name}" + (f"  ({detail})" if detail and not passed else ""))
        ok = ok and passed
    print("ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)
