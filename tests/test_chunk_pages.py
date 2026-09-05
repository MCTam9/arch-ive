"""Page text is windowed to fit the embedding model, and the rule is testable
without a database.

A page with no extracted item became one chunk holding the whole page. The
model has a 512-token window, and a third of those chunks exceeded it — so
their vectors described only the top of the page while the row looked indexed.
Full-text never noticed, because `tsv` covers the whole string, which is how it
stayed hidden.

The budget is tokens rather than characters because this corpus varies by 5x in
density: a page of prose runs to 6.5 characters per token, a page of dimensions
and codes to 1.3. A character window sized for prose still overflows on exactly
the pages whose numbers people search for.
"""
from __future__ import annotations

import uuid

import pytest

from tools import chunk_pages, db

PROSE = ("The daylight strategy shall be assessed at concept design. " * 60).strip()
DENSE = " ".join(f"{n}.{n} MIN {n * 25}mm;" for n in range(1, 400))


def _tokens(text: str) -> int:
    return chunk_pages.estimate_tokens(text)


def test_a_short_page_is_one_window():
    assert chunk_pages.split("A short page of text, nothing more.") == [
        "A short page of text, nothing more."
    ]


def test_empty_text_produces_nothing():
    assert chunk_pages.split("") == []
    assert chunk_pages.split("   \n\n  ") == []


@pytest.mark.parametrize("text", [PROSE, DENSE], ids=["prose", "dense"])
def test_every_window_fits_the_model(text):
    """The whole point. 420 is the budget; 512 is the model's hard limit and
    the estimator's worst measured underestimate is 3%."""
    windows = chunk_pages.split(text)
    assert len(windows) > 1, "this fixture is meant to be long enough to split"
    assert all(_tokens(w) <= chunk_pages.TARGET_TOKENS for w in windows), (
        [_tokens(w) for w in windows]
    )


def test_a_dense_page_yields_shorter_windows_than_prose():
    """The reason the budget is not measured in characters."""
    prose = max(len(w) for w in chunk_pages.split(PROSE))
    dense = max(len(w) for w in chunk_pages.split(DENSE))
    assert dense < prose, (
        f"dense text should get a smaller character window ({dense} vs {prose}); "
        "a fixed character budget is what let the densest pages overflow"
    )


def test_windows_overlap_so_a_straddling_sentence_survives():
    windows = chunk_pages.split(PROSE)
    joined = sum(len(w) for w in windows)
    assert joined > len(PROSE), "windows should overlap, not partition"


def test_no_window_is_below_the_noise_floor():
    assert all(len(w) >= chunk_pages.MIN_WINDOW for w in chunk_pages.split(PROSE))


def test_a_run_with_no_sentence_breaks_is_still_split():
    """A table dumped as one unbroken run, which this corpus has plenty of."""
    windows = chunk_pages.split("x" * 200 + " " + ("9" * 8 + " ") * 900)
    assert len(windows) > 1
    assert all(_tokens(w) <= chunk_pages.TARGET_TOKENS for w in windows)


@pytest.fixture
def page():
    """A document with one long page that already has a whole-page chunk."""
    tag = uuid.uuid4().hex[:8]
    with db.transaction() as conn:
        document_id = db.scalar(
            conn,
            "INSERT INTO source_document (slug, sha256) VALUES (%s, %s) RETURNING id",
            (f"test-pagechunk-{tag}", uuid.uuid4().hex),
        )
        conn.execute(
            "INSERT INTO source_page (document_id, page_index, text, content_status) "
            "VALUES (%s, 3, %s, 'real')",
            (document_id, PROSE),
        )
        conn.execute(
            "INSERT INTO chunk (document_id, page_from, page_to, text, content_status) "
            "VALUES (%s, 3, 3, %s, 'real')",
            (document_id, PROSE),
        )
    yield {"document_id": str(document_id), "slug": f"test-pagechunk-{tag}"}
    with db.transaction() as conn:
        conn.execute("DELETE FROM source_document WHERE id = %s", (document_id,))


def _run(slug: str) -> dict[str, int]:
    with db.connect() as conn:
        counts = chunk_pages.apply(conn, chunk_pages.plan(conn, slug))
        conn.commit()
    return counts


def _rows(document_id: str) -> list[dict]:
    with db.connect() as conn:
        return db.all_rows(
            conn,
            "SELECT ordinal, text, embedding IS NULL AS unembedded FROM chunk "
            "WHERE document_id = %s AND knowledge_item_id IS NULL AND asset_id IS NULL "
            "ORDER BY ordinal",
            (document_id,),
        )


def test_an_existing_whole_page_chunk_is_rewindowed(page):
    counts = _run(page["slug"])
    assert counts["insert"] > 0 and counts["rewrite"] == 1
    rows = _rows(page["document_id"])
    assert [r["ordinal"] for r in rows] == list(range(len(rows)))
    assert all(_tokens(r["text"]) <= chunk_pages.TARGET_TOKENS for r in rows)
    assert all(r["unembedded"] for r in rows), "rewritten and new windows both need embedding"


def test_running_twice_changes_nothing(page):
    _run(page["slug"])
    assert _run(page["slug"]) == {"insert": 0, "rewrite": 0, "stale": 0}


def test_a_shortened_page_drops_the_windows_it_no_longer_fills(page):
    _run(page["slug"])
    before = len(_rows(page["document_id"]))
    with db.transaction() as conn:
        conn.execute(
            "UPDATE source_page SET text = %s WHERE document_id = %s",
            ("Now a single short paragraph of text.", page["document_id"]),
        )
    counts = _run(page["slug"])
    assert counts["stale"] == before - 1
    assert len(_rows(page["document_id"])) == 1
