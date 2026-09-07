"""The retrieval policy, pinned where the three adapters can only agree.

Five rules about what a reader may be shown used to be written out by hand in
`tools/search.py`, `tools/mcp_server.py` and `web/lib/queries.ts` -- two to four
copies each, in two languages -- and a sixth (exclude `review_status =
'rejected'`) was written nowhere. They now live in db/schema.sql:
`is_placeholder_status()`, `item_has_term()`, `v_retrievable_chunk`,
`v_retrievable_item`, `v_term_item_count`.

Half of these tests assert that two surfaces now agree. The other half assert
that they still deliberately DISAGREE, and are named so that a future reader
does not mistake one for an oversight and "fix" it:

    test_review_queue_still_sees_rejected
    test_get_citation_does_not_filter_placeholder
    test_web_shows_placeholder_and_mcp_does_not

Fixture rows all live under document slug `h-pol-fixture` with fixed uuids, so
reruns are idempotent. arch_test is shared with other agents' fixtures and with
web/tests -- every assertion here is scoped to this document. Never assert on
an unscoped count.

    ./.venv/bin/python -m pytest tests/test_retrieval_policy.py -q
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools import db, search
from tools import mcp_server as m

DOC_SLUG = "h-pol-fixture"
DOC_ID = "0000000b-0000-0000-0000-00000000e001"
SHA256 = hashlib.sha256(b"h-pol-fixture").hexdigest()

APPROVED_ITEM = "0000000b-0000-0000-0000-00000000e010"
REJECTED_ITEM = "0000000b-0000-0000-0000-00000000e011"
LOREM_ITEM = "0000000b-0000-0000-0000-00000000e012"
UNCITED_ITEM = "0000000b-0000-0000-0000-00000000e013"

APPROVED_CHUNK = "0000000b-0000-0000-0000-00000000e020"
REJECTED_CHUNK = "0000000b-0000-0000-0000-00000000e021"
LOREM_CHUNK = "0000000b-0000-0000-0000-00000000e022"
UNCITED_CHUNK = "0000000b-0000-0000-0000-00000000e023"
SHORT_PAGE_CHUNK = "0000000b-0000-0000-0000-00000000e024"
FIGURE_CHUNK = "0000000b-0000-0000-0000-00000000e025"

REAL_PAGE = "0000000b-0000-0000-0000-00000000e030"
WIP_PAGE = "0000000b-0000-0000-0000-00000000e031"
FIGURE_ASSET = "0000000b-0000-0000-0000-00000000e040"

# One token per row, so a query can name exactly one fixture row and nothing
# in the rest of the corpus.
T_APPROVED = "hpolapprovedtoken"
T_REJECTED = "hpolrejectedtoken"
T_LOREM = "hpolloremtoken"
T_UNCITED = "hpoluncitedtoken"
T_SHORT = "hpolshorttoken"
T_FIGURE = "hpolfiguretoken"

PAD = " and some further sentences of ordinary prose so this chunk clears the floor."


def _setup(conn) -> None:
    conn.execute(
        "INSERT INTO source_document (id, slug, doc_kind, sha256, page_count, content_status) "
        "VALUES (%s, %s, 'guideline_report', %s, 2, 'real') "
        "ON CONFLICT (sha256) DO UPDATE SET slug = EXCLUDED.slug",
        (DOC_ID, DOC_SLUG, SHA256),
    )

    # Two pages: one real, one work-in-progress. The WIP page is the whole
    # point of the figure case below.
    for page_id, idx, status in ((REAL_PAGE, 1, "real"), (WIP_PAGE, 2, "wip")):
        conn.execute(
            "INSERT INTO source_page (id, document_id, page_index, printed_page_label, "
            "text, content_status) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET content_status = EXCLUDED.content_status",
            (page_id, DOC_ID, idx, str(idx), f"page {idx} body text", status),
        )

    items = (
        (APPROVED_ITEM, "real", "approved", "H-POL approved"),
        (REJECTED_ITEM, "real", "rejected", "H-POL rejected"),
        (LOREM_ITEM, "lorem", "approved", "H-POL lorem"),
        (UNCITED_ITEM, "real", "approved", "H-POL uncited"),
    )
    for item_id, content_status, review_status, title in items:
        conn.execute(
            "INSERT INTO knowledge_item (id, item_type, document_id, title, statement, "
            "content_status, review_status) "
            "VALUES (%s, 'guidance', %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET content_status = EXCLUDED.content_status, "
            "review_status = EXCLUDED.review_status",
            (item_id, DOC_ID, title, f"{title} statement", content_status, review_status),
        )

    # An uncited benchmark, to prove the citation rule now bites in
    # tools/search.get_benchmark as well as in the MCP copy of it.
    conn.execute(
        "INSERT INTO benchmark (knowledge_item_id, metric_id, value_numeric, value_text) "
        "VALUES (%s, 'upfront_embodied_carbon', 42, '42') "
        "ON CONFLICT (knowledge_item_id) DO NOTHING",
        (UNCITED_ITEM,),
    )
    conn.execute(
        "INSERT INTO benchmark (knowledge_item_id, metric_id, value_numeric, value_text) "
        "VALUES (%s, 'upfront_embodied_carbon', 43, '43') "
        "ON CONFLICT (knowledge_item_id) DO NOTHING",
        (REJECTED_ITEM,),
    )

    # Fixed citation ids: `citation` has no natural key, so an id-less insert
    # would pile up a fresh row on every run of this module.
    for cit_id, item_id in (
        ("0000000b-0000-0000-0000-00000000e050", APPROVED_ITEM),
        ("0000000b-0000-0000-0000-00000000e051", REJECTED_ITEM),
        ("0000000b-0000-0000-0000-00000000e052", LOREM_ITEM),
    ):
        conn.execute(
            "INSERT INTO citation (id, knowledge_item_id, document_id, page_id, page_index, "
            "printed_page_label) VALUES (%s, %s, %s, %s, 1, '1') "
            "ON CONFLICT (id) DO NOTHING",
            (cit_id, item_id, DOC_ID, REAL_PAGE),
        )

    conn.execute(
        "INSERT INTO source_asset (id, page_id, code, caption, vlm_description, sha256) "
        "VALUES (%s, %s, 'FIGURE H-POL', 'H-POL figure', %s, %s) "
        "ON CONFLICT (id) DO NOTHING",
        (FIGURE_ASSET, WIP_PAGE, f"Figure description mentioning {T_FIGURE}.",
         hashlib.sha256(b"h-pol-figure").hexdigest()),
    )

    chunks = (
        # id, item, asset, page_from, page_to, text, content_status
        (APPROVED_CHUNK, APPROVED_ITEM, None, 1, 1, f"Approved chunk {T_APPROVED}.{PAD}", "real"),
        (REJECTED_CHUNK, REJECTED_ITEM, None, 1, 1, f"Rejected chunk {T_REJECTED}.{PAD}", "real"),
        (LOREM_CHUNK, LOREM_ITEM, None, 1, 1, f"Lorem chunk {T_LOREM}.{PAD}", "lorem"),
        # No page at all: the "row with neither slug nor page is dropped" case.
        (UNCITED_CHUNK, UNCITED_ITEM, None, None, None, f"Uncited chunk {T_UNCITED}.{PAD}", "real"),
        # A page chunk under the 40-character floor. One real page chunk in the
        # corpus is three characters long.
        (SHORT_PAGE_CHUNK, None, None, 1, 1, f"{T_SHORT}.", "real"),
        # content_status 'real' on the chunk, sitting on a page marked 'wip'.
        # This is the shape of the 141 figure descriptions both surfaces used
        # to serve as fact.
        (FIGURE_CHUNK, None, FIGURE_ASSET, 2, 2, f"Figure chunk {T_FIGURE}.{PAD}", "real"),
    )
    for chunk_id, item_id, asset_id, page_from, page_to, text, status in chunks:
        conn.execute(
            "INSERT INTO chunk (id, document_id, knowledge_item_id, asset_id, page_from, "
            "page_to, text, content_status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET text = EXCLUDED.text, "
            "content_status = EXCLUDED.content_status",
            (chunk_id, DOC_ID, item_id, asset_id, page_from, page_to, text, status),
        )


@pytest.fixture(scope="module", autouse=True)
def _fixture_data():
    with db.transaction() as conn:
        _setup(conn)
    yield


def _chunk_ids(conn) -> set[str]:
    """chunk ids v_retrievable_chunk exposes for this fixture's document."""
    rows = db.all_rows(
        conn,
        "SELECT chunk_id::text AS id FROM v_retrievable_chunk WHERE document_slug = %s",
        (DOC_SLUG,),
    )
    return {r["id"] for r in rows}


def _search_ids(conn, token: str, **kw) -> set[str]:
    return {r["chunk_id"] for r in search.search(conn, token, limit=50, **kw)}


# ─────────────────────────────────────────────────────────────────────────
# Unconditional policy: rejected items
# ─────────────────────────────────────────────────────────────────────────

def test_rejected_items_are_not_in_the_search_surface():
    with db.connect() as conn:
        assert REJECTED_CHUNK not in _chunk_ids(conn)
        assert APPROVED_CHUNK in _chunk_ids(conn)


def test_rejected_items_are_not_in_the_browse_surface():
    with db.connect() as conn:
        rows = db.all_rows(
            conn,
            "SELECT id::text AS id FROM v_retrievable_item WHERE document_slug = %s",
            (DOC_SLUG,),
        )
    ids = {r["id"] for r in rows}
    assert REJECTED_ITEM not in ids
    assert {APPROVED_ITEM, LOREM_ITEM, UNCITED_ITEM} <= ids


def test_search_does_not_return_a_rejected_item():
    with db.connect() as conn:
        assert _search_ids(conn, T_REJECTED) == set()
        assert APPROVED_CHUNK in _search_ids(conn, T_APPROVED)


def test_rejected_items_are_gone_from_the_reporting_views():
    with db.connect() as conn:
        rows = db.all_rows(
            conn,
            "SELECT knowledge_item_id::text AS id FROM v_benchmark WHERE document_slug = %s",
            (DOC_SLUG,),
        )
    assert REJECTED_ITEM not in {r["id"] for r in rows}


def test_review_queue_still_sees_rejected():
    """DELIBERATELY DIFFERENT, not an oversight.

    A rejected item is excluded from browse and from search, and must stay
    visible to the review queue and the item page -- which read
    `knowledge_item` directly for exactly this reason. If the queue read
    v_retrievable_item, rejecting an item would make it unreviewable, and
    un-rejecting it impossible.
    """
    with db.connect() as conn:
        row = db.one(
            conn,
            "SELECT k.id::text AS id, k.review_status::text AS review_status "
            "FROM knowledge_item k JOIN source_document d ON d.id = k.document_id "
            "WHERE d.slug = %s AND k.review_status = 'rejected'",
            (DOC_SLUG,),
        )
    assert row is not None and row["id"] == REJECTED_ITEM


def test_facet_counts_exclude_rejected_so_they_cannot_promise_hidden_rows():
    """v_term_item_count is a LEFT JOIN whose knowledge_item side carries the
    same review_status filter as v_retrievable_item. A count that advertises
    rows the filter will not return is worse than no count."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO item_term (knowledge_item_id, term_id) "
            "SELECT %s, id FROM taxonomy_term WHERE taxonomy_id = 'topic' "
            "ORDER BY id LIMIT 1 ON CONFLICT DO NOTHING",
            (REJECTED_ITEM,),
        )
    with db.connect() as conn:
        term_id = db.scalar(
            conn, "SELECT term_id FROM item_term WHERE knowledge_item_id = %s", (REJECTED_ITEM,))
        assert term_id is not None, "fixture failed to tag the rejected item"
        counted = db.scalar(
            conn, "SELECT item_count FROM v_term_item_count WHERE term_id = %s", (term_id,))
        matched = db.scalar(
            conn,
            "SELECT count(*)::int FROM v_retrievable_item WHERE item_has_term(id, %s)",
            (term_id,),
        )
    assert counted == matched


# ─────────────────────────────────────────────────────────────────────────
# Unconditional policy: the page-text floor
# ─────────────────────────────────────────────────────────────────────────

def test_a_short_page_chunk_is_below_the_floor():
    with db.connect() as conn:
        assert SHORT_PAGE_CHUNK not in _chunk_ids(conn)
        assert _search_ids(conn, T_SHORT) == set()


def test_the_floor_is_page_only_and_never_touches_items_or_figures():
    """180 item chunks in the corpus are shorter than 40 characters and are
    perfectly good answers, so the floor exempts anything that has an item or
    an asset behind it. A blanket floor would delete them silently."""
    with db.transaction() as conn:
        conn.execute("UPDATE chunk SET text = %s WHERE id = %s", ("Tiny.", APPROVED_CHUNK))
    try:
        with db.connect() as conn:
            assert APPROVED_CHUNK in _chunk_ids(conn)
    finally:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE chunk SET text = %s WHERE id = %s",
                (f"Approved chunk {T_APPROVED}.{PAD}", APPROVED_CHUNK),
            )


# ─────────────────────────────────────────────────────────────────────────
# Conditional policy: citations
# ─────────────────────────────────────────────────────────────────────────

def test_a_chunk_with_no_page_is_dropped_by_default():
    with db.connect() as conn:
        assert _search_ids(conn, T_UNCITED) == set()


def test_a_chunk_with_no_page_is_still_reachable_when_asked_for():
    """`require_citation` is a named parameter, not a hardcoded rule: the row
    is in v_retrievable_chunk with has_citation=false, so a caller that knows
    what it is doing can still see it."""
    with db.connect() as conn:
        assert UNCITED_CHUNK in _search_ids(conn, T_UNCITED, require_citation=False)
        assert db.scalar(
            conn, "SELECT has_citation FROM v_retrievable_chunk WHERE chunk_id = %s",
            (UNCITED_CHUNK,)) is False


def test_both_get_benchmark_implementations_drop_an_uncited_row():
    """CONTRACT.md: a row with neither a slug nor a page is dropped, not
    returned with a null citation. tools/mcp_server.get_benchmark enforced
    that and tools/search.get_benchmark did not -- the divergence the view
    exists to end."""
    with db.connect() as conn:
        via_search = {str(r["knowledge_item_id"])
                      for r in search.get_benchmark(conn, "upfront_embodied_carbon")}
    with m.read_connect() as conn:
        via_mcp = {r["knowledge_item_id"]
                   for r in m.get_benchmark(conn, metric="upfront_embodied_carbon",
                                             limit=200)["results"]}
    assert UNCITED_ITEM not in via_search
    assert UNCITED_ITEM not in via_mcp


# ─────────────────────────────────────────────────────────────────────────
# Conditional policy: placeholders, including the page-inheritance decision
# ─────────────────────────────────────────────────────────────────────────

def test_a_figure_on_a_wip_page_is_placeholder_even_though_the_chunk_says_real():
    """The decided behaviour change. 141 figure-description chunks in the dev
    corpus carry content_status='real' while sitting on a page marked 'wip',
    and both surfaces served them as real content. A description of a
    work-in-progress page is work in progress."""
    with db.connect() as conn:
        row = db.one(
            conn,
            "SELECT chunk_content_status::text AS chunk_status, "
            "       page_content_status::text AS page_status, "
            "       is_placeholder, placeholder_status::text AS placeholder_status "
            "FROM v_retrievable_chunk WHERE chunk_id = %s",
            (FIGURE_CHUNK,),
        )
    assert row["chunk_status"] == "real"
    assert row["page_status"] == "wip"
    assert row["is_placeholder"] is True
    assert row["placeholder_status"] == "wip"


def test_mcp_search_loses_the_wip_figure_by_default_and_can_get_it_back():
    with m.read_connect() as conn:
        default = m.search_knowledge(conn, T_FIGURE, limit=20)
        opened = m.search_knowledge(conn, T_FIGURE, limit=20, include_placeholder=True)
    assert [r for r in default["results"] if r["chunk_id"] == FIGURE_CHUNK] == []
    got = [r for r in opened["results"] if r["chunk_id"] == FIGURE_CHUNK]
    assert got, "include_placeholder=True must return the figure it hid"
    assert got[0]["is_placeholder"] is True
    assert opened["placeholder_included"] is True


def test_web_shows_placeholder_and_mcp_does_not():
    """DELIBERATELY DIFFERENT, not an oversight.

    Placeholder content keeps today's split: the MCP surface must never serve
    it as fact, and the web shows it labelled so a reviewer can find and fix
    it. What changed is that the split is now one named parameter over one
    view instead of two hand-written queries -- so the row the web renders and
    the row MCP withholds are provably the same row.
    """
    with db.connect() as conn:
        # The web's surface: the row is present, and it is labelled.
        row = db.one(
            conn,
            "SELECT is_placeholder, placeholder_status::text AS placeholder_status "
            "FROM v_retrievable_chunk WHERE chunk_id = %s",
            (LOREM_CHUNK,),
        )
    assert row is not None, "the web must still be able to render placeholder content"
    assert row["is_placeholder"] is True
    assert row["placeholder_status"] == "lorem"

    with m.read_connect() as conn:
        out = m.search_knowledge(conn, T_LOREM, limit=20)
    assert out["results"] == [], "MCP must not serve placeholder content as fact"


def test_placeholder_exclusion_and_labelling_come_from_the_same_column():
    """The label and the filter used to be two expressions that could disagree
    -- the MCP escape hatch had already dropped the chunk-level check the
    default path applied. Now both read `is_placeholder`."""
    with db.connect() as conn:
        hidden = _search_ids(conn, T_LOREM)
        shown = {r["chunk_id"]: r for r in
                 search.search(conn, T_LOREM, limit=50, include_placeholder=True)}
    assert LOREM_CHUNK not in hidden
    assert shown[LOREM_CHUNK]["is_placeholder"] is True
    assert shown[LOREM_CHUNK]["placeholder_status"] == "lorem"


def test_get_citation_does_not_filter_placeholder():
    """DELIBERATELY DIFFERENT, not an oversight.

    CONTRACT.md: get_citation is not filtered by content_status. The caller
    already holds a specific item_id -- most likely from a search that told it
    the row was a placeholder -- and asking where a placeholder came from is
    the whole point of the call. It labels rather than hides, and it reads the
    base tables so a *rejected* item's citations remain visible too.
    """
    with m.read_connect() as conn:
        lorem = m.get_citation(conn, LOREM_ITEM)
        rejected = m.get_citation(conn, REJECTED_ITEM)
    assert lorem["citations"], "a placeholder item's citation must still resolve"
    assert lorem["citations"][0]["is_placeholder"] is True
    assert rejected["citations"], "a rejected item's citation must still resolve"


def test_is_placeholder_status_is_the_only_definition_of_the_word():
    with db.connect() as conn:
        rows = db.all_rows(
            conn,
            "SELECT s::text AS s, is_placeholder_status(s) AS flag "
            "FROM unnest(enum_range(NULL::content_status)) AS s",
        )
    got = {r["s"]: r["flag"] for r in rows}
    assert got == {"real": False, "draft": True, "wip": True, "lorem": True,
                   "template": True, "mixed": False}


def test_is_placeholder_status_is_false_not_null_for_a_null_status():
    """A page or figure chunk has no knowledge_item, so this is called with
    NULL on every such row. NULL would poison the OR that builds
    is_placeholder and every count downstream of it."""
    with db.connect() as conn:
        assert db.scalar(conn, "SELECT is_placeholder_status(NULL)") is False
