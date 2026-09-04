"""Tests for tools/classify_facets.py.

Fixtures live under document slug `j-facet-fixture` with fixed uuids, so a
rerun replaces rather than accumulates. tests/conftest.py redirects
DATABASE_URL to arch_test before anything here imports a tool -- the corpus
database must never be the one under test.

Run in isolation; arch_test is shared:
    ./.venv/bin/python -m pytest tests/test_classify_facets.py -q
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools import db
from tools.classify_facets import classify_items

DOC_SLUG = "j-facet-fixture"
DOC_ID = "0000000b-0000-0000-0000-00000000f001"
SHA256 = hashlib.sha256(b"j-facet-fixture").hexdigest()

WATER_ITEM = "0000000b-0000-0000-0000-00000000f002"
LOREM_ITEM = "0000000b-0000-0000-0000-00000000f003"
MUTE_ITEM = "0000000b-0000-0000-0000-00000000f004"

# A statement that names its own facets in the corpus's own vocabulary.
WATER_STATEMENT = (
    "Potable water consumption at building scale shall not exceed "
    "95 l/p/day for residential and 13 l/p/day for office."
)
# Deliberately signal-free: no metric, no use class, no scale word. An item
# like this SHOULD come out untagged -- a facet invented for it would be worse
# than the gap.
MUTE_STATEMENT = "The consultant shall coordinate with the wider team as required."


def _setup(conn) -> None:
    conn.execute(
        "INSERT INTO source_document (id, slug, doc_kind, sha256, page_count, content_status) "
        "VALUES (%s, %s, 'crib_sheet', %s, 2, 'real') "
        "ON CONFLICT (sha256) DO UPDATE SET slug = EXCLUDED.slug",
        (DOC_ID, DOC_SLUG, SHA256),
    )
    for item_id, statement, status in (
        (WATER_ITEM, WATER_STATEMENT, "real"),
        (LOREM_ITEM, WATER_STATEMENT, "lorem"),
        (MUTE_ITEM, MUTE_STATEMENT, "real"),
    ):
        conn.execute(
            "INSERT INTO knowledge_item (id, item_type, document_id, title, statement, content_status) "
            "VALUES (%s, 'requirement', %s, 'j-facet item', %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET statement = EXCLUDED.statement, "
            "content_status = EXCLUDED.content_status",
            (item_id, DOC_ID, statement, status),
        )
        conn.execute(
            "INSERT INTO requirement (knowledge_item_id, requirement_kind, target_text, comparator) "
            "VALUES (%s, 'graded', %s, 'none') ON CONFLICT (knowledge_item_id) DO NOTHING",
            (item_id, statement),
        )


@pytest.fixture(scope="module", autouse=True)
def fixture_document():
    with db.transaction() as conn:
        _setup(conn)
    yield
    with db.transaction() as conn:
        conn.execute("DELETE FROM source_document WHERE id = %s", (DOC_ID,))


def _tags(conn, item_id: str) -> dict[str, float]:
    rows = db.all_rows(
        conn,
        "SELECT term_id, confidence FROM item_term WHERE knowledge_item_id = %s",
        (item_id,),
    )
    return {r["term_id"]: float(r["confidence"] or 0) for r in rows}


def test_tags_the_facets_the_statement_names():
    with db.transaction() as conn:
        classify_items(conn, DOC_ID)
        tags = _tags(conn, WATER_ITEM)
    assert any(t.startswith("topic.water") for t in tags), tags
    assert any(t.startswith("scale.building") for t in tags), tags


def test_placeholder_content_is_not_tagged():
    """Identical text, flagged lorem. The flag has to win, or the facet
    browser serves placeholder text under a real topic."""
    with db.transaction() as conn:
        classify_items(conn, DOC_ID)
        assert _tags(conn, LOREM_ITEM) == {}


# Facets that describe what an item SAYS, as opposed to where it came from.
# authority is deliberately excluded: it is a property of the document, known
# for every item in it, and asserting it is not an inference.
CONTENT_TAXONOMIES = ("topic", "scale", "building_use", "level", "discipline",
                      "project_type", "region", "stage")


def test_a_signal_free_item_gets_no_content_facet():
    """Precision over recall: a wrong facet files a hotel benchmark under
    Residential and someone designs to it. A statement that names no metric,
    use class or scale must come back with nothing inferred about its content
    -- provenance tags are fine, invented ones are not."""
    with db.transaction() as conn:
        classify_items(conn, DOC_ID)
        tags = _tags(conn, MUTE_ITEM)
    invented = [t for t in tags if t.split(".", 1)[0] in CONTENT_TAXONOMIES]
    assert invented == [], f"inferred content facets from a signal-free statement: {invented}"


def test_confidence_is_never_certain_for_a_keyword_match():
    with db.transaction() as conn:
        classify_items(conn, DOC_ID)
        tags = _tags(conn, WATER_ITEM)
    assert tags, "expected the water statement to tag at all"
    assert all(0 < c <= 1.0 for c in tags.values())
    assert any(c < 1.0 for c in tags.values()), (
        "every tag claimed total certainty; confidence is what a reviewer sorts by"
    )


def test_rerunning_replaces_rather_than_accumulates():
    with db.transaction() as conn:
        first = classify_items(conn, DOC_ID)
        after_first = _tags(conn, WATER_ITEM)
        second = classify_items(conn, DOC_ID)
        after_second = _tags(conn, WATER_ITEM)
    assert first["tags_written"] == second["tags_written"]
    assert after_first == after_second
