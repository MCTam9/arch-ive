"""Filtering by a taxonomy term must include the term's whole subtree.

Topic is an ltree hierarchy, and both filter paths -- `tools/search.py` and the
web's `listKnowledgeItems` -- used to match `item_term.term_id = <id>` exactly.
So asking for a top-level topic returned only the items tagged on the parent
itself: 'Health & Wellbeing' gave 17 of the 53 items filed under it, and the
new home page's tile would have promised 53 and delivered 17.

These tests pin the semantics on the Python side, which is the side with a test
runner; `web/lib/queries.ts` carries the same clause and the same comment.
"""
from __future__ import annotations

import uuid

import pytest

from tools import db, search


@pytest.fixture
def tree():
    """A two-level topic tree, an item on the parent and an item on the child."""
    tag = uuid.uuid4().hex[:8]
    tax = f"t{tag}"
    parent, child = f"{tax}.parent", f"{tax}.parent.child"
    with db.transaction() as conn:
        conn.execute("INSERT INTO taxonomy (id, name) VALUES (%s, %s)", (tax, "test taxonomy"))
        for term_id, path, code in ((parent, parent, "parent"), (child, child, "child")):
            conn.execute(
                "INSERT INTO taxonomy_term (id, taxonomy_id, code, label, path, parent_id) "
                "VALUES (%s, %s, %s, %s, %s::ltree, %s)",
                (term_id, tax, code, code, path, parent if term_id == child else None),
            )
        document_id = db.scalar(
            conn,
            "INSERT INTO source_document (slug, sha256) VALUES (%s, %s) RETURNING id",
            (f"test-subtree-{tag}", uuid.uuid4().hex),
        )
        items = {}
        for role, term_id in (("on_parent", parent), ("on_child", child)):
            item_id = db.scalar(
                conn,
                "INSERT INTO knowledge_item (document_id, item_type, title) "
                "VALUES (%s, 'guidance', %s) RETURNING id",
                (document_id, role),
            )
            conn.execute(
                "INSERT INTO item_term (knowledge_item_id, term_id) VALUES (%s, %s)",
                (item_id, term_id),
            )
            items[role] = str(item_id)
    yield {"parent": parent, "child": child, "items": items, "document_id": document_id, "tax": tax}
    with db.transaction() as conn:
        conn.execute("DELETE FROM source_document WHERE id = %s", (document_id,))
        conn.execute("DELETE FROM taxonomy_term WHERE taxonomy_id = %s", (tax,))
        conn.execute("DELETE FROM taxonomy WHERE id = %s", (tax,))


def _matching(conn, term_id: str, document_id) -> set[str]:
    """The items `search`'s facet filter would keep, for one term."""
    clause, params = search._facet_clauses({"topic": term_id})
    rows = db.all_rows(
        conn,
        f"SELECT ki.id::text AS id FROM knowledge_item ki "
        f"WHERE ki.document_id = %s {clause}",
        (document_id, *params),
    )
    return {r["id"] for r in rows}


def test_a_parent_term_matches_items_tagged_on_its_children(tree):
    with db.connect() as conn:
        got = _matching(conn, tree["parent"], tree["document_id"])
    assert got == {tree["items"]["on_parent"], tree["items"]["on_child"]}, (
        "filtering by a parent topic must return the whole subtree; matching "
        "term_id exactly is what made the browse counts disagree with the results"
    )


def test_a_child_term_does_not_match_its_parent(tree):
    """The subtree runs downward only. If it ran both ways, every leaf filter
    would drag in the parent's other branches."""
    with db.connect() as conn:
        got = _matching(conn, tree["child"], tree["document_id"])
    assert got == {tree["items"]["on_child"]}


def test_the_filter_never_duplicates_an_item(tree):
    """EXISTS, not a JOIN. A join on a subtree matches one row per descendant
    term, which would multiply chunks and corrupt the RRF fusion downstream."""
    with db.transaction() as conn:
        # Tag the same item with both terms in the subtree -- the case a JOIN
        # would return twice.
        conn.execute(
            "INSERT INTO item_term (knowledge_item_id, term_id) VALUES (%s, %s)",
            (tree["items"]["on_child"], tree["parent"]),
        )
    with db.connect() as conn:
        clause, params = search._facet_clauses({"topic": tree["parent"]})
        rows = db.all_rows(
            conn,
            f"SELECT ki.id::text AS id FROM knowledge_item ki "
            f"WHERE ki.document_id = %s {clause}",
            (tree["document_id"], *params),
        )
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)) == 2


def test_no_facets_adds_no_clause():
    for empty in (None, {}):
        clause, params = search._facet_clauses(empty)
        assert clause == "" and params == []
