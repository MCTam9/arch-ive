"""Round-trip test for tools/write_extraction.py.

Builds an Extraction by hand covering every item_type, writes it, reads it
back through the views, writes it AGAIN and asserts nothing duplicated.
Also exercises: dangling refs (warned, not raised), a document contributing
a unit/metric that already exists (no collision), a term id that doesn't
exist yet (skipped + counted), and a bad payload key (a clear, named error).

Run directly against the live DB from CONTRACT.md:
    ./.venv/bin/python tests/test_writer.py
(also discoverable by pytest, if you have it installed, as test_roundtrip)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import db
from tools.pipeline import Citation, Extraction, Item, Node, Reference
from tools.write_extraction import PayloadError, write_extraction

SHA256 = "b" * 64
SLUG = "test-writer-roundtrip"
BAD_PAYLOAD_SHA256 = "c" * 64
BAD_PAYLOAD_SLUG = "test-writer-badpayload"


def _ensure_document(conn, sha256: str = SHA256, slug: str = SLUG) -> str:
    row = db.one(conn, "SELECT id FROM source_document WHERE sha256 = %s", (sha256,))
    if row:
        return row["id"]
    row = conn.execute(
        "INSERT INTO source_document (slug, doc_kind, sha256, page_count) "
        "VALUES (%s, 'unknown', %s, 1) RETURNING id",
        (slug, sha256),
    ).fetchone()
    return row["id"]


def _ensure_bad_payload_document(conn) -> str:
    return _ensure_document(conn, BAD_PAYLOAD_SHA256, BAD_PAYLOAD_SLUG)


def _build_extraction() -> Extraction:
    nodes = [
        Node(ref="sec-1", node_kind="section", code="TEST-1", title="Test Section", ordinal=1),
    ]

    items = [
        Item(
            ref="req-1", item_type="requirement", node_ref="sec-1",
            title="EUI requirement",
            statement="Operational EUI shall not exceed 100 kWh/m2/yr.",
            payload={
                "requirement_kind": "graded", "metric_id": "eui",
                "target_value": 100, "target_text": "100 kWh/m2/yr",
                "unit_id": "kwh_m2_yr", "comparator": "lte",
                "is_deliverable": False, "parsed_ok": True,
            },
            citations=[Citation(page_index=1)],
            terms=["topic.operational_carbon", "does.not.exist"],
        ),
        Item(
            ref="bm-1", item_type="benchmark", node_ref="sec-1",
            title="Upfront embodied carbon benchmark",
            payload={
                "metric_id": "upfront_embodied_carbon", "value_numeric": 380,
                "value_text": "380", "unit_id": "kgco2e_m2_gia", "comparator": "lte",
                "is_placeholder": False, "building_use_id": "building_use.residential.flats",
                "target_year": 2030, "standard_id": "uknzcbs",
            },
            citations=[Citation(page_index=1, printed_page_label="1 / 2")],
            terms=["topic.embodied_carbon_circularity"],
        ),
        Item(
            ref="gd-1", item_type="guidance", node_ref="sec-1",
            title="Passive design guidance",
            content_status="draft", confidence=0.4,
            payload={
                "body_md": "Use external shading on south facades.",
                "figure_ids": [], "legend_tokens": ["SLA"],
            },
        ),
        Item(
            ref="pat-parent", item_type="pattern", node_ref="sec-1",
            title="Stepped Terrace Housing",
            payload={"pattern_kind": "typology", "code": "STH",
                      "name": "Stepped Terrace Housing", "attributes": {}},
        ),
        Item(
            ref="pat-child", item_type="pattern", node_ref="sec-1",
            title="Stepped Terrace Housing - Type 1",
            payload={"pattern_kind": "unit_type", "code": "STH-1",
                      "name": "Stepped Terrace Housing - Type 1",
                      "parent_pattern_id": "pat-parent", "attributes": {"beds": 3}},
        ),
        Item(
            ref="tpl-1", item_type="template", node_ref="sec-1",
            title="Fee calculator",
            payload={"template_kind": "calculator", "engine": "xlsx",
                      "slug": f"{SLUG}-calc"},
        ),
        Item(
            ref="def-1", item_type="definition", node_ref="sec-1",
            title="GIA",
            payload={"term": "GIA", "definition": "Gross Internal Area.",
                      "category": "TECHNICAL"},
        ),
        Item(
            ref="role-1", item_type="role", node_ref="sec-1",
            title="Sustainability Lead Architect",
            payload={"code": "SLA", "name": "Sustainability Lead Architect",
                      "reports_to": "Project Director"},
        ),
        Item(
            ref="step-1", item_type="process_step", node_ref="sec-1",
            title="Design review gate",
            payload={"code": "PS1", "ordinal": 1, "gate": "Pass/Fail",
                      "responsible_role_id": "role-1"},
        ),
    ]

    return Extraction(
        nodes=nodes,
        items=items,
        references=[Reference(raw_text="(Module 5 Chapter 3)", ref_kind="module_chapter",
                               from_node_ref="sec-1"),
                    Reference(raw_text="(orphan chapter ref)", ref_kind="module_chapter",
                               from_node_ref="nonexistent-node")],
        units=[{"id": "kwh_m2_yr", "symbol": "kWh/m2/yr", "dimension": "energy_intensity"}],
        metrics=[{"id": "eui", "name": "Energy Use Intensity", "default_unit_id": "kwh_m2_yr",
                  "higher_is_better": False}],
        frameworks=[{"slug": f"{SLUG}-fw", "name": "Test Roundtrip Framework"}],
        criteria=[
            {"framework_slug": f"{SLUG}-fw", "code": "RE1", "title_primary": "Test Criterion 1"},
            {"framework_slug": f"{SLUG}-fw", "code": "RE1.1", "title_primary": "Test Sub-criterion",
             "parent_code": "RE1"},
        ],
        design_variables=[{"id": "test_access_typology", "name": "ACCESS TYPOLOGY"}],
        design_variable_values=[{"id": "test_stepped_duplex", "variable_id": "test_access_typology",
                                  "label": "Stepped Duplex"}],
        warnings=[],
    )


def _checks() -> list[tuple[str, bool, str]]:
    """(name, passed, detail) triples, so a failure says exactly what broke."""
    results: list[tuple[str, bool, str]] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        results.append((name, cond, detail))

    with db.transaction() as conn:
        document_id = _ensure_document(conn)
        extraction = _build_extraction()
        counts1 = write_extraction(conn, document_id, extraction)

    check("first write: 9 items", counts1["items"] == 9, str(counts1["items"]))
    check("first write: 1 term skipped (does.not.exist)", counts1["item_terms_skipped"] == 1,
          str(counts1["item_terms_skipped"]))
    check("first write: dangling refs warned", any("dangling" in w for w in counts1["warnings"]),
          str(counts1["warnings"]))
    check("first write: 9 item chunks", counts1["chunks_item"] == 9, str(counts1["chunks_item"]))
    check("first write: 2 criteria", counts1["criteria"] == 2, str(counts1["criteria"]))
    check("first write: 2 external references", counts1["external_references"] == 2,
          str(counts1["external_references"]))

    with db.connect() as conn:
        ki_count = db.scalar(conn, "SELECT count(*) FROM knowledge_item WHERE document_id = %s", (document_id,))
        check("knowledge_item rows == 9 after first write", ki_count == 9, str(ki_count))

        pattern_row = db.one(
            conn,
            """SELECT p.name, parent.name AS parent_name
               FROM pattern p JOIN knowledge_item ki ON ki.id = p.knowledge_item_id
               LEFT JOIN pattern parent ON parent.knowledge_item_id = p.parent_pattern_id
               WHERE ki.document_id = %s AND p.code = 'STH-1'""",
            (document_id,),
        )
        check("pattern parent ref resolved", pattern_row is not None and pattern_row["parent_name"] == "Stepped Terrace Housing",
              str(pattern_row))

        role_row = db.one(
            conn,
            """SELECT ps.responsible_role_id, r.knowledge_item_id AS role_id
               FROM process_step ps JOIN knowledge_item ki ON ki.id = ps.knowledge_item_id
               JOIN role r ON r.code = 'SLA'
               WHERE ki.document_id = %s""",
            (document_id,),
        )
        check("process_step role ref resolved", role_row is not None
              and role_row["responsible_role_id"] == role_row["role_id"], str(role_row))

        bm_view = db.one(conn, "SELECT * FROM v_benchmark WHERE document_slug = %s", (SLUG,))
        check("v_benchmark has the row", bm_view is not None and bm_view["value_numeric"] == 380, str(bm_view))

        tpl_view = db.one(conn, "SELECT * FROM v_template_catalogue WHERE document_slug = %s", (SLUG,))
        check("v_template_catalogue has the row", tpl_view is not None, str(tpl_view))

        search_rows = db.all_rows(conn, "SELECT item_type, content_status FROM v_search WHERE document_slug = %s", (SLUG,))
        check("v_search sees all 9 items", len(search_rows) == 9, str(len(search_rows)))
        check("v_search carries the draft guidance item, marked", any(
            r["item_type"] == "guidance" and r["content_status"] == "draft" for r in search_rows), str(search_rows))

        try:
            from tools.search import search as hybrid_search
            hits = hybrid_search(conn, "shading facades", limit=10)
            check("search() excludes the draft guidance item", all(h["item_type"] != "guidance" for h in hits), str(hits))
        except Exception as exc:  # pragma: no cover - search.py is a sibling module, not under test here
            check("search() excludes the draft guidance item", False, f"search() raised: {exc}")

    # idempotency: write the identical extraction again
    with db.transaction() as conn:
        counts2 = write_extraction(conn, document_id, _build_extraction())

    with db.connect() as conn:
        ki_count2 = db.scalar(conn, "SELECT count(*) FROM knowledge_item WHERE document_id = %s", (document_id,))
        check("knowledge_item rows still == 9 after second write", ki_count2 == 9, str(ki_count2))
        chunk_count2 = db.scalar(conn, "SELECT count(*) FROM chunk WHERE document_id = %s", (document_id,))
        check("chunk rows still == 9 after second write", chunk_count2 == 9, str(chunk_count2))
        node_count2 = db.scalar(conn, "SELECT count(*) FROM doc_node WHERE document_id = %s AND code = 'TEST-1'", (document_id,))
        check("doc_node not duplicated by code", node_count2 == 1, str(node_count2))
        ext_ref_count2 = db.scalar(conn, "SELECT count(*) FROM external_reference WHERE from_document_id = %s", (document_id,))
        check("external_reference rows still == 2 after second write", ext_ref_count2 == 2, str(ext_ref_count2))
        crit_count2 = db.scalar(conn, "SELECT count(*) FROM criterion WHERE framework_id = (SELECT id FROM framework WHERE slug = %s)", (f"{SLUG}-fw",))
        check("criterion rows still == 2 after second write (upsert, not duplicate)", crit_count2 == 2, str(crit_count2))

    check("second write: 9 items again", counts2["items"] == 9, str(counts2["items"]))

    # bad payload key -> a clear, named error, not a psycopg syntax error.
    # A separate document, and the exception must propagate all the way out
    # of the `with` block so psycopg rolls the whole transaction back --
    # catching it *inside* the block would commit the pre-write deletes
    # without the (failed) reinsert and corrupt the document.
    raised: Exception | None = None
    try:
        with db.transaction() as conn:
            bad_document_id = _ensure_bad_payload_document(conn)
            bad = Extraction(items=[Item(item_type="definition", title="x",
                                          payload={"term": "x", "definition": "y", "termz": "bogus"})])
            write_extraction(conn, bad_document_id, bad)
    except PayloadError as exc:
        raised = exc
    check("bad payload key raises PayloadError", raised is not None and "termz" in str(raised), str(raised))

    with db.connect() as conn:
        # the failed write must not have left a half-written knowledge_item behind
        leftover = db.scalar(
            conn, "SELECT count(*) FROM knowledge_item WHERE document_id = "
                  "(SELECT id FROM source_document WHERE sha256 = %s)", (BAD_PAYLOAD_SHA256,))
        check("failed write left no knowledge_item behind", leftover == 0, str(leftover))

    return results


def test_roundtrip() -> None:
    """pytest entry point."""
    results = _checks()
    failed = [(n, d) for n, ok, d in results if not ok]
    assert not failed, "\n".join(f"FAIL {n}: {d}" for n, d in failed)


def main() -> int:
    results = _checks()
    ok = True
    for name, passed, detail in results:
        mark = "ok  " if passed else "FAIL"
        print(f"  {mark}  {name}" + (f"  ({detail})" if detail and not passed else ""))
        ok = ok and passed
    print("ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
