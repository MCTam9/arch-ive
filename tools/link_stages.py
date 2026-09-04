"""Fill item_stage from each document's own stage vocabulary.

Four incompatible stage vocabularies coexist in the corpus (see
db/schema.sql's stage_scheme / stage / stage_crosswalk, seeded with 4
schemes, 28 stages, 40 crosswalk rows). This module never guesses a RIBA
number directly -- it identifies the *native* stage a document itself names,
then walks stage_crosswalk from that native stage to record its canonical
(riba_2020) equivalent too. Both the native and canonical stage_id land in
item_stage, so a query can filter by either vocabulary.

Three signals, each verified against the source before being encoded here:

1. `framework-vol-e1`'s compliance deliverables spell out, per bullet, which
   masterplan submission substage(s) they are due at -- e.g. a line ending
   "... CMP  DMP" on p.19-20, explained by that page's own legend
   ("Submission Substages: Conceptual Master Plan CMP  Detailed Master Plan
   DMP"). The PDF-extraction reading-order issue documented in
   workflows/verify_extraction.md reorders these trailing codes into
   requirement.target_text, but the codes themselves survive, so a
   word-boundary scan recovers them without re-parsing the PDF. Checked
   against every requirement row's target_text corpus-wide: the codes appear
   nowhere else, so this is not corpus-specific pattern-matching luck, it is
   the actual shape of the data.

2. `calc-fees` bands its columns under literal header cells "STAGE C",
   "STAGE D&E", "STAGE FGH", "STAGE JKL" (row 3 of the "Fee calculation"
   sheet) -- the old RIBA lettered stages, grouped. calc-budget-cost-rates
   and calc-cashflow have no such header; their column axis is plain
   calendar months, which is not a stage vocabulary at all and is
   deliberately left unlinked (see (3) in the module docstring below and
   CONTRACT.md's "never guess" rule).

3. `typology-multifamily`'s own introduction (p.9-10) states, in its own
   words, that the guide is written "for the design of Multifamily Housing
   at master plan and concept design stages" and covers "concept design and
   schematic design work stages" -- a whole-document scope declaration, not
   a per-item one. That is why it is encoded as a small explicit table
   rather than a generic text scan: the same phrases ("concept design",
   "schematic design") also appear inside framework-vol-e2's own per-stage
   narrative sections, where they describe *one* stage each rather than
   declaring the whole document's scope, and a generic scan would over-link
   items there.

Idempotent: re-running recomputes and replaces item_stage for the requested
scope rather than accumulating duplicate rows.
"""
from __future__ import annotations

import re
import sys
from typing import Iterable

from tools import db

# masterplan.* stage codes as they appear verbatim in framework-vol-e1's
# compliance deliverable text (see docstring point 1).
MASTERPLAN_CODE_RE = re.compile(r"\b(CMP|DMP|CD|SD|DD)\b")

# spreadsheet column-band headers naming a grouped legacy RIBA stage, e.g.
# "STAGE C", "STAGE D&E", "STAGE FGH" (see docstring point 2).
STAGE_BAND_RE = re.compile(r"^STAGE\s+([A-Z&]+)$")

# whole-document stage scope declared in a document's own front matter --
# not something that can be safely detected by a generic text scan (see
# docstring point 3). Values are native stage ids in the `generic` scheme.
DOCUMENT_SCOPE_STAGES: dict[str, tuple[str, ...]] = {
    "typology-multifamily": ("generic.concept", "generic.schematic"),
}

# How much a link from each rule is worth. The first two read a stage from the
# item's own row or column header; the third reads one sentence in the front
# matter and applies it to all 313 items in the document.
RULE_CONFIDENCE: dict[str, float] = {
    "masterplan_deliverable_code": 0.9,
    "calculator_stage_band": 0.9,
    "document_scope_statement": 0.5,
}


def link_stages(conn, document_id: str | None = None) -> dict:
    """Fill item_stage from each document's own stage vocabulary. Idempotent."""
    scope_item_ids = _scope_item_ids(conn, document_id)
    if scope_item_ids:
        conn.execute(
            "DELETE FROM item_stage WHERE knowledge_item_id = ANY(%s)",
            [scope_item_ids],
        )

    by_rule: dict[str, list[tuple[str, str]]] = {
        "masterplan_deliverable_code": _masterplan_code_links(conn, document_id),
        "calculator_stage_band": _calculator_stage_band_links(conn, document_id),
        "document_scope_statement": _document_scope_links(conn, document_id),
    }

    valid_stage_ids = {r["id"] for r in db.all_rows(conn, "SELECT id FROM stage")}
    # A document-scope statement is a true fact about every item in the
    # document and a much weaker one than an item's own stage cell. Persist
    # the difference rather than letting the coarse link pass for a precise
    # one; a reviewer, and the UI, sort on this.
    rows: dict[tuple[str, str], float] = {}
    counts_by_rule: dict[str, int] = {}
    for rule, links in by_rule.items():
        confidence = RULE_CONFIDENCE[rule]
        counts_by_rule[rule] = len({item_id for item_id, _ in links})
        for item_id, native_stage_id in links:
            if native_stage_id not in valid_stage_ids:
                continue  # defensive: every id above is hand-verified, but never write a dangling fk
            for stage_id in (native_stage_id, *_crosswalk_targets(conn, native_stage_id)):
                key = (item_id, stage_id)
                rows[key] = max(rows.get(key, 0.0), confidence)

    for (item_id, stage_id), confidence in rows.items():
        conn.execute(
            "INSERT INTO item_stage (knowledge_item_id, stage_id, assigned_by, confidence) "
            "VALUES (%s, %s, 'rule', %s) ON CONFLICT DO NOTHING",
            (item_id, stage_id, confidence),
        )

    return {
        "items_linked": len({item_id for item_id, _ in rows}),
        "stage_links_written": len(rows),
        "items_by_rule": counts_by_rule,
    }


def _scope_item_ids(conn, document_id: str | None) -> list[str]:
    if document_id:
        rows = db.all_rows(
            conn, "SELECT id FROM knowledge_item WHERE document_id = %s", [document_id]
        )
    else:
        rows = db.all_rows(conn, "SELECT id FROM knowledge_item")
    return [r["id"] for r in rows]


def _crosswalk_targets(conn, stage_id: str) -> list[str]:
    rows = db.all_rows(
        conn, "SELECT to_stage_id FROM stage_crosswalk WHERE from_stage_id = %s", [stage_id]
    )
    return [r["to_stage_id"] for r in rows]


def _masterplan_code_links(conn, document_id: str | None) -> list[tuple[str, str]]:
    sql = (
        "SELECT k.id AS item_id, r.target_text "
        "FROM knowledge_item k "
        "JOIN requirement r ON r.knowledge_item_id = k.id "
        "WHERE r.target_text IS NOT NULL AND k.content_status = 'real'"
    )
    params: list = []
    if document_id:
        sql += " AND k.document_id = %s"
        params.append(document_id)

    links: list[tuple[str, str]] = []
    for row in db.all_rows(conn, sql, params):
        for code in set(MASTERPLAN_CODE_RE.findall(row["target_text"])):
            links.append((row["item_id"], f"masterplan.{code.lower()}"))
    return links


def _split_legacy_band(band: str) -> Iterable[str]:
    # 'C' -> ['C'];  'D&E' -> ['D', 'E'];  'FGH' -> ['F', 'G', 'H']
    return (ch for ch in band if ch.isalpha())


def _calculator_stage_band_links(conn, document_id: str | None) -> list[tuple[str, str]]:
    sql = (
        "SELECT DISTINCT s.document_id, c.value_text "
        "FROM spreadsheet_cell c "
        "JOIN spreadsheet_sheet s ON s.id = c.sheet_id "
        "WHERE c.value_text ~ '^STAGE [A-Z&]+$'"
    )
    params: list = []
    if document_id:
        sql += " AND s.document_id = %s"
        params.append(document_id)

    stage_ids_by_doc: dict[str, set[str]] = {}
    for row in db.all_rows(conn, sql, params):
        match = STAGE_BAND_RE.match(row["value_text"])
        if not match:
            continue
        for letter in _split_legacy_band(match.group(1)):
            stage_ids_by_doc.setdefault(row["document_id"], set()).add(
                f"riba_legacy.{letter.lower()}"
            )
    if not stage_ids_by_doc:
        return []

    links: list[tuple[str, str]] = []
    for doc_id, stage_ids in stage_ids_by_doc.items():
        item_rows = db.all_rows(
            conn,
            "SELECT t.knowledge_item_id AS id FROM template t "
            "JOIN knowledge_item k ON k.id = t.knowledge_item_id "
            "WHERE k.document_id = %s AND k.content_status = 'real'",
            [doc_id],
        )
        for item in item_rows:
            for stage_id in stage_ids:
                links.append((item["id"], stage_id))
    return links


def _document_scope_links(conn, document_id: str | None) -> list[tuple[str, str]]:
    sql = "SELECT id, slug FROM source_document WHERE is_current"
    params: list = []
    if document_id:
        sql += " AND id = %s"
        params.append(document_id)

    links: list[tuple[str, str]] = []
    for row in db.all_rows(conn, sql, params):
        stage_ids = DOCUMENT_SCOPE_STAGES.get(row["slug"])
        if not stage_ids:
            continue
        item_rows = db.all_rows(
            conn,
            "SELECT id FROM knowledge_item WHERE document_id = %s AND content_status = 'real'",
            [row["id"]],
        )
        for item in item_rows:
            for stage_id in stage_ids:
                links.append((item["id"], stage_id))
    return links


if __name__ == "__main__":
    doc_id = sys.argv[1] if len(sys.argv) > 1 else None
    with db.transaction() as _conn:
        result = link_stages(_conn, doc_id)
    print(f"link_stages: {result}")
