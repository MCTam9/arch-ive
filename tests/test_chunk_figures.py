"""Figure descriptions reach the index once, and stay correct when they change.

`chunk.asset_id` existed unwritten since the table was created; these tests pin
the semantics of the tool that finally fills it. The cases that matter are not
the happy one -- they are re-running (a description costs a model call, and a
second run must not duplicate or re-embed it), a description that changed (a
stale vector left on rewritten text keeps the row findable at the wrong
coordinates), and a description withdrawn or downgraded to decorative (whose
chunk must not linger, searchable, describing a figure nobody would describe
that way now).
"""
from __future__ import annotations

import uuid

import pytest

from tools import chunk_figures, db

REAL = "A bar chart comparing embodied carbon by frame type, in kgCO2e/m2 GIA."
DECORATIVE = chunk_figures.DECORATIVE_PREFIX + "full-bleed stock photograph of a skyline."


@pytest.fixture
def figure():
    """A document with one page and one described, cropped figure."""
    tag = uuid.uuid4().hex[:8]
    with db.transaction() as conn:
        document_id = db.scalar(
            conn,
            "INSERT INTO source_document (slug, sha256) VALUES (%s, %s) RETURNING id",
            (f"test-figchunk-{tag}", uuid.uuid4().hex),
        )
        page_id = db.scalar(
            conn,
            "INSERT INTO source_page (document_id, page_index, text) "
            "VALUES (%s, 7, 'page text') RETURNING id",
            (document_id,),
        )
        asset_id = db.scalar(
            conn,
            "INSERT INTO source_asset (page_id, image_key, vlm_description, vlm_model) "
            "VALUES (%s, %s, %s, 'test-model') RETURNING id",
            (page_id, f"figures/{document_id}/{uuid.uuid4()}.webp", REAL),
        )
    yield {"document_id": str(document_id), "asset_id": str(asset_id), "slug": f"test-figchunk-{tag}"}
    with db.transaction() as conn:
        conn.execute("DELETE FROM source_document WHERE id = %s", (document_id,))


def _run(slug: str) -> dict[str, int]:
    with db.connect() as conn:
        counts = chunk_figures.apply(conn, chunk_figures.plan(conn, slug))
        conn.commit()
    return counts


def _chunks(asset_id: str) -> list[dict]:
    with db.connect() as conn:
        return db.all_rows(
            conn,
            "SELECT id::text, text, page_from, page_to, content_status::text, "
            "       embedding IS NULL AS unembedded, document_id::text "
            "FROM chunk WHERE asset_id = %s",
            (asset_id,),
        )


def test_a_described_figure_becomes_one_chunk(figure):
    assert _run(figure["slug"])["insert"] == 1
    rows = _chunks(figure["asset_id"])
    assert len(rows) == 1
    row = rows[0]
    assert row["text"] == REAL
    assert row["page_from"] == row["page_to"] == 7, "the citation comes from the asset's page"
    assert row["document_id"] == figure["document_id"]
    assert row["unembedded"] is True, "a new chunk is left for embed_chunks to pick up"
    # 'real' is deliberate: content_status describes how finished the *source*
    # is, and the figure genuinely appears in the document. Authorship is the
    # other axis, recoverable through asset_id -> source_asset.vlm_model.
    assert row["content_status"] == "real"


def test_running_twice_changes_nothing(figure):
    _run(figure["slug"])
    before = _chunks(figure["asset_id"])[0]["id"]
    counts = _run(figure["slug"])
    assert counts == {"insert": 0, "rewrite": 0, "stale": 0}
    rows = _chunks(figure["asset_id"])
    assert len(rows) == 1 and rows[0]["id"] == before


def test_a_changed_description_is_rewritten_and_the_vector_cleared(figure):
    _run(figure["slug"])
    with db.transaction() as conn:
        conn.execute(
            "UPDATE chunk SET embedding = array_fill(0.1::real, ARRAY[384])::vector "
            "WHERE asset_id = %s",
            (figure["asset_id"],),
        )
        conn.execute(
            "UPDATE source_asset SET vlm_description = %s WHERE id = %s",
            (REAL + " Updated with the legend values.", figure["asset_id"]),
        )
    assert _run(figure["slug"])["rewrite"] == 1
    row = _chunks(figure["asset_id"])[0]
    assert row["text"].endswith("legend values.")
    assert row["unembedded"] is True, (
        "a rewritten chunk must lose its vector; embed_chunks selects on "
        "embedding IS NULL and would otherwise never revisit it"
    )


def test_a_decorative_description_is_never_indexed(figure):
    with db.transaction() as conn:
        conn.execute(
            "UPDATE source_asset SET vlm_description = %s WHERE id = %s",
            (DECORATIVE, figure["asset_id"]),
        )
    assert _run(figure["slug"])["insert"] == 0
    assert _chunks(figure["asset_id"]) == []


def test_a_figure_downgraded_to_decorative_loses_its_chunk(figure):
    """The case that makes re-running safe after a re-description."""
    _run(figure["slug"])
    assert len(_chunks(figure["asset_id"])) == 1
    with db.transaction() as conn:
        conn.execute(
            "UPDATE source_asset SET vlm_description = %s WHERE id = %s",
            (DECORATIVE, figure["asset_id"]),
        )
    assert _run(figure["slug"])["stale"] == 1
    assert _chunks(figure["asset_id"]) == []


def test_a_withdrawn_description_loses_its_chunk(figure):
    _run(figure["slug"])
    with db.transaction() as conn:
        conn.execute(
            "UPDATE source_asset SET vlm_description = NULL WHERE id = %s",
            (figure["asset_id"],),
        )
    assert _run(figure["slug"])["stale"] == 1
    assert _chunks(figure["asset_id"]) == []
