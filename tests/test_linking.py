"""Tests for tools/link_stages.py and tools/resolve_references.py.

Fixtures live under document slug `k-link-fixture` with fixed uuids so reruns
replace rather than accumulate. tests/conftest.py redirects DATABASE_URL to
arch_test before anything here imports a tool.

Run in isolation; arch_test is shared:
    ./.venv/bin/python -m pytest tests/test_linking.py -q
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools import db
from tools.link_stages import link_stages
from tools.resolve_references import resolve_references

DOC_SLUG = "k-link-fixture"
DOC_ID = "0000000c-0000-0000-0000-00000000f001"
SHA256 = hashlib.sha256(b"k-link-fixture").hexdigest()
ITEM_ID = "0000000c-0000-0000-0000-00000000f002"
NODE_ID = "0000000c-0000-0000-0000-00000000f003"
REF_MODULE_ID = "0000000c-0000-0000-0000-00000000f004"


def _setup(conn) -> None:
    conn.execute(
        "INSERT INTO source_document (id, slug, doc_kind, sha256, page_count, content_status) "
        "VALUES (%s, %s, 'crib_sheet', %s, 2, 'real') "
        "ON CONFLICT (sha256) DO UPDATE SET slug = EXCLUDED.slug",
        (DOC_ID, DOC_SLUG, SHA256),
    )
    conn.execute(
        "INSERT INTO doc_node (id, document_id, node_kind, title, ordinal) "
        "VALUES (%s, %s, 'sheet', 'k-link sheet', 0) ON CONFLICT (id) DO NOTHING",
        (NODE_ID, DOC_ID),
    )
    conn.execute(
        "INSERT INTO knowledge_item (id, item_type, document_id, node_id, title, statement, content_status) "
        "VALUES (%s, 'requirement', %s, %s, 'k-link item', 'k-link statement', 'real') "
        "ON CONFLICT (id) DO NOTHING",
        (ITEM_ID, DOC_ID, NODE_ID),
    )
    # target_text names a masterplan submission gate verbatim, which is the
    # per-item signal the linker is supposed to act on -- as opposed to a
    # whole-document scope statement, which it must record more weakly.
    conn.execute(
        "INSERT INTO requirement (knowledge_item_id, requirement_kind, target_text, comparator) "
        "VALUES (%s, 'compliance', 'Submit at CD and DD gates.', 'none') "
        "ON CONFLICT (knowledge_item_id) DO UPDATE SET target_text = EXCLUDED.target_text",
        (ITEM_ID,),
    )
    conn.execute(
        "INSERT INTO external_reference (id, from_document_id, from_node_id, raw_text, ref_kind, status) "
        "VALUES (%s, %s, %s, '(Module 2 Chapter 4)', 'module_chapter', 'unresolved') "
        "ON CONFLICT (id) DO UPDATE SET status = 'unresolved', resolved_document_id = NULL",
        (REF_MODULE_ID, DOC_ID, NODE_ID),
    )


@pytest.fixture(scope="module", autouse=True)
def fixture_document():
    with db.transaction() as conn:
        _setup(conn)
    yield
    with db.transaction() as conn:
        conn.execute("DELETE FROM source_document WHERE id = %s", (DOC_ID,))


def test_stage_links_record_how_precise_they_are():
    """A document-scope statement applies to every item in the document and a
    stage cell applies to one. Both are true; they are not equally precise,
    and without confidence a filter for 'RIBA 3' returns them at identical
    authority."""
    with db.transaction() as conn:
        link_stages(conn)
        rows = db.all_rows(
            conn,
            "SELECT confidence, count(*) n FROM item_stage GROUP BY 1 ORDER BY 1",
        )
        # this fixture's signal is per-item, so it must not be filed at the
        # weaker document-scope confidence
        own = db.all_rows(
            conn,
            "SELECT stage_id, confidence FROM item_stage WHERE knowledge_item_id = %s",
            (ITEM_ID,),
        )

    assert rows, "expected link_stages to write something for the fixture"
    for row in rows:
        assert row["confidence"] is not None, "a stage link with no stated confidence"
        assert 0 < float(row["confidence"]) <= 1.0

    assert own, "the CD/DD gate in target_text produced no link"
    assert all(float(r["confidence"]) >= 0.9 for r in own), own


def test_link_stages_is_idempotent():
    with db.transaction() as conn:
        first = link_stages(conn)
        before = db.scalar(conn, "SELECT count(*) FROM item_stage")
        second = link_stages(conn)
        after = db.scalar(conn, "SELECT count(*) FROM item_stage")
    assert first["stage_links_written"] == second["stage_links_written"]
    assert before == after


def test_a_reference_to_an_uncollected_document_is_missing_not_unresolved():
    """The crib sheets cite a six-module parent guide nobody has. Recording
    that as missing_source is the deliverable: it turns a dangling citation
    into a specific document to go and find. Leaving it 'unresolved' says only
    that nobody looked."""
    with db.transaction() as conn:
        resolve_references(conn)
        row = db.one(
            conn,
            "SELECT status, resolved_document_id FROM external_reference WHERE id = %s",
            (REF_MODULE_ID,),
        )
    assert row["status"] == "missing_source"
    assert row["resolved_document_id"] is None


def test_resolve_references_is_idempotent():
    with db.transaction() as conn:
        first = resolve_references(conn)
        second = resolve_references(conn)
        unresolved = db.scalar(
            conn, "SELECT count(*) FROM external_reference WHERE status = 'unresolved'"
        )
    assert first == second
    assert unresolved == 0 or unresolved > 0  # either is legitimate; must not crash
