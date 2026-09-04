"""End-to-end check: ingest the whole corpus and assert the result is sound.

Run after the pipeline is assembled. Reports real numbers rather than passing
quietly, because the failure mode that matters here is not a crash -- it is a
knowledge base that looks populated while serving placeholder text as guidance.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import db

CHECKS: list[tuple[str, str, object]] = [
    ("documents ingested", "SELECT count(*) FROM source_document WHERE is_current", 14),
    ("pages extracted", "SELECT count(*) FROM source_page", None),
    ("pages flagged not-real",
     "SELECT count(*) FROM source_page WHERE content_status <> 'real'", None),
    ("doc_nodes", "SELECT count(*) FROM doc_node", None),
    ("knowledge items", "SELECT count(*) FROM knowledge_item", None),
    ("  requirements", "SELECT count(*) FROM requirement", None),
    ("  benchmarks", "SELECT count(*) FROM benchmark", None),
    ("  patterns", "SELECT count(*) FROM pattern", None),
    ("  templates", "SELECT count(*) FROM template", None),
    ("  definitions", "SELECT count(*) FROM definition", None),
    ("chunks", "SELECT count(*) FROM chunk", None),
    ("chunks embedded", "SELECT count(*) FROM chunk WHERE embedding IS NOT NULL", None),
    ("citations", "SELECT count(*) FROM citation", None),
    ("unresolved references",
     "SELECT count(*) FROM external_reference WHERE status = 'unresolved'", None),
]

# Values read off the source PDFs by hand. If extraction drifts, these break.
GROUND_TRUTH = [
    ("embodied carbon 2030, flats",
     """SELECT value_numeric FROM v_benchmark
        WHERE metric_id = 'upfront_embodied_carbon' AND target_year = 2030
          AND building_use_id LIKE '%flat%' LIMIT 1""", 380),
    ("embodied carbon 2030, hotel",
     """SELECT value_numeric FROM v_benchmark
        WHERE metric_id = 'upfront_embodied_carbon' AND target_year = 2030
          AND building_use_id LIKE '%hotel%' LIMIT 1""", 500),
    # 39 is the 2030 target; 35 is 2050. An earlier hand-read of the unordered
    # text layer transposed them -- the coordinate-aligned table is the truth.
    ("eui 2030, flats",
     """SELECT value_numeric FROM v_benchmark
        WHERE metric_id = 'eui' AND target_year = 2030
          AND building_use_id LIKE '%flat%' LIMIT 1""", 39),
    ("eui 2050, flats",
     """SELECT value_numeric FROM v_benchmark
        WHERE metric_id = 'eui' AND target_year = 2050
          AND building_use_id LIKE '%flat%' LIMIT 1""", 35),
    ("eui 2030, general offices",
     """SELECT value_numeric FROM v_benchmark
        WHERE metric_id = 'eui' AND target_year = 2030
          AND building_use_id LIKE '%general_office%' LIMIT 1""", 72),
]

INTEGRITY = [
    # A spreadsheet has no page to cite -- its provenance is a sheet and cell
    # reference, not a page image. Only paged documents owe a citation.
    ("every item from a paged document is cited",
     """SELECT count(*) FROM knowledge_item ki
        JOIN source_document d ON d.id = ki.document_id
        WHERE coalesce(d.page_count, 0) > 0
          AND NOT EXISTS (SELECT 1 FROM citation c WHERE c.knowledge_item_id = ki.id)""", 0),
    # A handful legitimately have none: constants written inline inside a
    # formula (the hours-per-month figure, for one) have no cell of their own.
    ("template parameters without a cell stay rare",
     """SELECT (count(*) FILTER (WHERE cell_ref IS NULL)) * 10 <= count(*)
        FROM template_parameter""", True),
    ("no item sourced from a placeholder page",
     """SELECT count(*) FROM knowledge_item ki
        JOIN citation c ON c.knowledge_item_id = ki.id
        JOIN source_page p ON p.id = c.page_id
        WHERE p.content_status <> 'real' AND ki.content_status = 'real'""", 0),
    ("no benchmark lost its verbatim value",
     "SELECT count(*) FROM benchmark WHERE value_text IS NULL OR value_text = ''", 0),
    ("no requirement lost its verbatim target",
     "SELECT count(*) FROM requirement WHERE target_text IS NULL AND NOT is_deliverable", 0),
    ("one current revision per slug",
     """SELECT count(*) FROM (SELECT slug FROM source_document WHERE is_current
                              GROUP BY slug HAVING count(*) > 1) x""", 0),
]


def run() -> int:
    failures = 0
    with db.connect() as conn:
        print("\n── counts " + "─" * 50)
        for label, sql, expected in CHECKS:
            got = db.scalar(conn, sql)
            flag = ""
            if expected is not None and got != expected:
                flag, failures = f"  ← expected {expected}", failures + 1
            print(f"  {label:<32} {got}{flag}")

        print("\n── ground truth (read off the source PDFs) " + "─" * 17)
        for label, sql, expected in GROUND_TRUTH:
            got = db.scalar(conn, sql)
            ok = got is not None and float(got) == float(expected)
            failures += 0 if ok else 1
            print(f"  {'ok  ' if ok else 'FAIL'}  {label:<34} {got} (want {expected})")

        print("\n── integrity " + "─" * 47)
        for label, sql, expected in INTEGRITY:
            got = db.scalar(conn, sql)
            ok = got == expected
            failures += 0 if ok else 1
            print(f"  {'ok  ' if ok else 'FAIL'}  {label:<44} {got}")

        print("\n── per document " + "─" * 44)
        for row in db.all_rows(conn, """
            SELECT d.slug, d.doc_kind::text AS kind, d.page_count,
                   count(DISTINCT n.id) AS nodes,
                   count(DISTINCT k.id) AS items
            FROM source_document d
            LEFT JOIN doc_node n ON n.document_id = d.id
            LEFT JOIN knowledge_item k ON k.document_id = d.id
            WHERE d.is_current GROUP BY d.slug, d.doc_kind, d.page_count ORDER BY d.slug"""):
            print(f"  {row['slug']:<32} {row['kind']:<20} "
                  f"{row['page_count'] or 0:>4}p {row['nodes']:>5}n {row['items']:>6}i")

    print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILURES'}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
