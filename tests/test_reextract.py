"""What a re-extract must not do.

`tools/reextract.py` deletes and rewrites a document's knowledge items, and
the schema cascades from there -- citations, item chunks, facet tags, stage
links. Three properties are worth pinning because getting any of them wrong
loses corpus content silently, with the row surviving and empty:

  1. an extractor that suddenly returns nothing is refused, not committed;
  2. figure chunks, which hang off source_asset and cost model time to
     produce, survive; so do their descriptions;
  3. one document failing does not roll back the documents beside it.

Everything runs against the throwaway database from tests/conftest.py, with a
stub extractor -- the point here is the backfill's consequences, not any real
document shape.
"""
from __future__ import annotations

import uuid

import pytest

from tools import db, pipeline, reextract
from tools.pipeline import Citation, Extraction, Item, Node
from tools.write_extraction import write_extraction

SLUG_A = "test-reextract-a"
SLUG_B = "test-reextract-b"


class _Stub:
    """Returns whatever the test handed it, ignoring the DocumentContext."""

    doc_kinds = ("unknown",)

    def __init__(self, extraction: Extraction | Exception):
        self.extraction = extraction

    def extract(self, ctx) -> Extraction:
        if isinstance(self.extraction, Exception):
            raise self.extraction
        return self.extraction


def _extraction(n_items: int = 2, *, bad_payload: bool = False) -> Extraction:
    nodes = [Node(ref="sec", node_kind="section", code="RX-1", title="Section", ordinal=1)]
    items = [
        Item(
            ref=f"g{i}", item_type="guidance", node_ref="sec",
            title=f"Guidance {i}",
            statement=f"Statement {i} about daylight at concept design.",
            payload=({"nonexistent_column": 1} if bad_payload
                     else {"body_md": f"Body {i}", "figure_ids": [], "legend_tokens": []}),
            citations=[Citation(page_index=1)],
        )
        for i in range(n_items)
    ]
    return Extraction(nodes=nodes, items=items)


def _make_document(conn, slug: str, sha: str) -> str:
    conn.execute("DELETE FROM source_document WHERE slug = %s OR sha256 = %s", (slug, sha))
    doc_id = conn.execute(
        "INSERT INTO source_document (slug, doc_kind, sha256, page_count) "
        "VALUES (%s, 'unknown', %s, 1) RETURNING id",
        (slug, sha),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO source_page (document_id, page_index, printed_page_label, text) "
        "VALUES (%s, 1, '1', %s)",
        (doc_id, "Page text long enough to be worth a chunk of its own. " * 4),
    )
    return str(doc_id)


def _figure_chunk(conn, document_id: str) -> str:
    """A described figure and the chunk that indexes it -- the pair a
    re-extract is most at risk of throwing away."""
    page_id = db.scalar(conn, "SELECT id FROM source_page WHERE document_id = %s", (document_id,))
    # image_key is not decoration here: chunk_figures treats a described asset
    # with no crop as ineligible and removes its chunk, so an asset without one
    # would test the enrichment's own rule rather than this tool's.
    asset_id = conn.execute(
        "INSERT INTO source_asset (page_id, vlm_description, vlm_model, vlm_described_at, "
        "bbox, image_key) VALUES (%s, %s, 'test-model', now(), %s, %s) RETURNING id",
        (page_id, "A section drawing of a stepped terrace.", [0, 0, 200, 200],
         "figures/test/reextract.webp"),
    ).fetchone()["id"]
    return str(conn.execute(
        "INSERT INTO chunk (document_id, asset_id, page_from, page_to, text, content_status) "
        "VALUES (%s, %s, 1, 1, %s, 'real') RETURNING id",
        (document_id, asset_id, "A section drawing of a stepped terrace."),
    ).fetchone()["id"])


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """Two documents already extracted, with originals on disk where
    find_original looks for them."""
    sha_a, sha_b = uuid.uuid4().hex * 2, uuid.uuid4().hex * 2
    monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
    for slug, sha in ((SLUG_A, sha_a), (SLUG_B, sha_b)):
        (tmp_path / slug).mkdir()
        (tmp_path / slug / f"{sha}.pdf").write_bytes(b"%PDF-1.4 not read by the stub")

    with db.connect() as conn:
        ids = {
            SLUG_A: _make_document(conn, SLUG_A, sha_a),
            SLUG_B: _make_document(conn, SLUG_B, sha_b),
        }
        for doc_id in ids.values():
            write_extraction(conn, doc_id, _extraction(2))
        figure_chunk_id = _figure_chunk(conn, ids[SLUG_A])
        conn.commit()

    yield {"ids": ids, "figure_chunk_id": figure_chunk_id}

    with db.connect() as conn:
        conn.execute("DELETE FROM source_document WHERE slug IN (%s, %s)", (SLUG_A, SLUG_B))
        conn.commit()


def _stub_registry(monkeypatch, by_slug: dict[str, Extraction | Exception]) -> None:
    """Route each document to its own stub result.

    The registry is keyed on doc_kind and both fixture documents share one, so
    the dispatch that matters here is by slug -- done inside `extract`, which
    is the only place the DocumentContext is visible.
    """
    class _Router:
        doc_kinds = ("unknown",)

        def extract(self, ctx) -> Extraction:
            return _Stub(by_slug[ctx.slug]).extract(ctx)

    monkeypatch.setattr(pipeline, "for_doc_kind", lambda kind: _Router())


def test_plan_skips_a_document_whose_original_is_not_on_disk(corpus, monkeypatch, tmp_path):
    """A missing original must cost that document its re-extract and nothing
    else: the corpus pass has thirteen others to get through."""
    monkeypatch.setenv("SOURCE_DIR", str(tmp_path / "empty"))
    monkeypatch.setattr(reextract, "find_original", lambda slug, sha: None)
    with db.connect() as conn:
        work = [e for e in reextract.plan(conn) if e["slug"] in (SLUG_A, SLUG_B)]
    assert work, "the fixture documents should be in scope"
    assert all(e["extraction"] is None for e in work)
    assert all("fetch_original" in e["skip"] for e in work)


def test_a_reextract_rewrites_items_and_rebuilds_what_cascaded(corpus, monkeypatch):
    _stub_registry(monkeypatch, {SLUG_A: _extraction(3), SLUG_B: _extraction(2)})
    with db.connect() as conn:
        work = [e for e in reextract.plan(conn) if e["slug"] in (SLUG_A, SLUG_B)]
        assert {e["slug"]: e["predicted"]["items"] for e in work} == {SLUG_A: 3, SLUG_B: 2}

        report = reextract.apply(conn, work, embed=False)
        assert report["ok"] == 2, report

        result = next(r for r in report["documents"] if r["slug"] == SLUG_A)
        assert result["before"]["items"] == 2
        assert result["after"]["items"] == 3
        assert result["after"]["citations"] == 3
        # the writer enriches a citation from the page it points at; that is
        # the whole reason to re-run it over old rows
        assert result["after"]["labelled"] == 3
        # every rewritten chunk is waiting for the embedder, which is the state
        # embed_chunks resumes from
        assert result["after"]["unembedded"] >= result["after"]["chunks_item"] > 0


def test_a_figure_chunk_and_its_description_survive(corpus, monkeypatch):
    """Figure chunks hang off source_asset, not knowledge_item. They cost real
    model time and are not this stage's to delete."""
    _stub_registry(monkeypatch, {SLUG_A: _extraction(1), SLUG_B: _extraction(1)})
    with db.connect() as conn:
        work = [e for e in reextract.plan(conn) if e["slug"] in (SLUG_A, SLUG_B)]
        reextract.apply(conn, work, embed=False)
        kept = db.one(conn, "SELECT id, text FROM chunk WHERE id = %s",
                      (corpus["figure_chunk_id"],))
        assert kept is not None, "the figure chunk was deleted by the re-extract"
        assert "stepped terrace" in kept["text"]
        assert db.scalar(conn, "SELECT count(*) FROM source_asset WHERE vlm_description IS NOT NULL") >= 1


def test_zero_items_is_refused_and_changes_nothing(corpus, monkeypatch):
    """The failure mode the tool exists to not commit."""
    _stub_registry(monkeypatch, {SLUG_A: Extraction(), SLUG_B: _extraction(2)})
    with db.connect() as conn:
        work = [e for e in reextract.plan(conn) if e["slug"] in (SLUG_A, SLUG_B)]
        report = reextract.apply(conn, work, embed=False)

        refused = next(r for r in report["documents"] if r["slug"] == SLUG_A)
        assert refused["status"] == "refused"
        assert "--allow-empty" in refused["detail"]
        assert db.scalar(
            conn, "SELECT count(*) FROM knowledge_item WHERE document_id = %s",
            (corpus["ids"][SLUG_A],)) == 2
        # and the refusal is per document: B still went through
        assert report["ok"] == 1


def test_allow_empty_is_the_way_to_mean_it(corpus, monkeypatch):
    _stub_registry(monkeypatch, {SLUG_A: Extraction(), SLUG_B: _extraction(1)})
    with db.connect() as conn:
        work = [e for e in reextract.plan(conn) if e["slug"] == SLUG_A]
        report = reextract.apply(conn, work, allow_empty=True, embed=False)
        assert report["ok"] == 1
        assert db.scalar(
            conn, "SELECT count(*) FROM knowledge_item WHERE document_id = %s",
            (corpus["ids"][SLUG_A],)) == 0


def test_one_document_failing_leaves_the_others_untouched(corpus, monkeypatch):
    """One transaction per document. A bad payload raises inside the writer,
    after it has already deleted A's items -- if that shared a transaction with
    B, or was committed, the corpus would be worse off than before the run."""
    _stub_registry(monkeypatch, {
        SLUG_A: _extraction(2, bad_payload=True),
        SLUG_B: _extraction(4),
    })
    with db.connect() as conn:
        work = [e for e in reextract.plan(conn) if e["slug"] in (SLUG_A, SLUG_B)]
        report = reextract.apply(conn, work, embed=False)

        failed = next(r for r in report["documents"] if r["slug"] == SLUG_A)
        assert failed["status"] == "failed"
        assert db.scalar(
            conn, "SELECT count(*) FROM knowledge_item WHERE document_id = %s",
            (corpus["ids"][SLUG_A],)) == 2, "A's rollback did not restore its items"
        assert db.scalar(
            conn, "SELECT count(*) FROM knowledge_item WHERE document_id = %s",
            (corpus["ids"][SLUG_B],)) == 4, "B was punished for A's failure"


def test_the_rls_account_survives_a_rollback(corpus, monkeypatch):
    """A rolled-back transaction reverts the session's app.account_id, and
    every document after it would then read as an empty corpus."""
    _stub_registry(monkeypatch, {
        SLUG_A: _extraction(1, bad_payload=True),
        SLUG_B: _extraction(1),
    })
    with db.connect() as conn:
        work = sorted(
            (e for e in reextract.plan(conn) if e["slug"] in (SLUG_A, SLUG_B)),
            key=lambda e: e["slug"],
        )
        reextract.apply(conn, work, embed=False)
        assert db.scalar(conn, "SELECT current_setting('app.account_id', true)") == db.account_id()
        assert db.scalar(conn, "SELECT count(*) FROM source_document") > 0


# ── documents.yaml -> source_document ────────────────────────────────────────
#
# Every extractor reads `ctx.meta`, which comes from private/documents.yaml, so
# a manifest edited after the ingest reaches the knowledge items on the next
# re-extract. It used to stop there: `register_document` writes the document
# columns at ingest and nothing wrote them again, so three crib sheets sat at
# `content_status = 'real'` over items that were every one of them 'draft'.


def _manifest(monkeypatch, by_slug: dict[str, dict]) -> None:
    monkeypatch.setattr(reextract, "_static_meta_by_slug", lambda: by_slug)


def test_the_manifest_is_applied_to_the_document_row(corpus, monkeypatch):
    _manifest(monkeypatch, {SLUG_A: {"content_status": "draft", "version_label": "V.02"}})
    with db.connect() as conn:
        work = reextract.plan(conn, extract=False)
        report = reextract.sync_metadata(conn, work)
        row = db.one(
            conn,
            "SELECT content_status::text AS cs, version_label FROM source_document WHERE slug = %s",
            (SLUG_A,),
        )
        other = db.one(
            conn, "SELECT content_status::text AS cs FROM source_document WHERE slug = %s", (SLUG_B,)
        )
    assert report["metadata"] == 1
    assert (row["cs"], row["version_label"]) == ("draft", "V.02")
    assert other["cs"] == "real", "a document the manifest says nothing about is left alone"


def test_a_key_the_manifest_omits_is_not_blanked(corpus, monkeypatch):
    """An absent key is silence, not an instruction. The three crib sheets that
    prompted this carry no `version_label` at all, and a sync that read that as
    NULL would erase the label off every document that has one."""
    with db.connect() as conn:
        conn.execute("UPDATE source_document SET version_label = 'V.01' WHERE slug = %s", (SLUG_A,))
        conn.commit()
    _manifest(monkeypatch, {SLUG_A: {"content_status": "draft"}})
    with db.connect() as conn:
        reextract.sync_metadata(conn, reextract.plan(conn, extract=False))
        row = db.one(
            conn,
            "SELECT content_status::text AS cs, version_label FROM source_document WHERE slug = %s",
            (SLUG_A,),
        )
    assert (row["cs"], row["version_label"]) == ("draft", "V.01")


def test_is_spread_paginated_is_measured_and_never_synced_back(corpus, monkeypatch):
    """`register_document` takes it from the manifest and then `ingest_document`
    overwrites it from the page evidence it just read. The column is the
    measurement; syncing the declaration back would undo the reading."""
    _manifest(monkeypatch, {SLUG_A: {"is_spread_paginated": True}})
    with db.connect() as conn:
        work = reextract.plan(conn, extract=False)
        entry = next(e for e in work if e["slug"] == SLUG_A)
        assert entry["drift"] == {}
        assert reextract.sync_metadata(conn, work)["metadata"] == 0
        assert db.scalar(
            conn, "SELECT is_spread_paginated FROM source_document WHERE slug = %s", (SLUG_A,)
        ) is False


def test_a_metadata_pass_rewrites_no_knowledge_items(corpus, monkeypatch):
    """The cheap half has to stay cheap: no extractor, no writer, and above all
    no reset of the review decisions a full re-extract does cost."""
    _manifest(monkeypatch, {SLUG_A: {"content_status": "draft"}})
    with db.connect() as conn:
        before = db.all_rows(
            conn, "SELECT id FROM knowledge_item WHERE document_id = %s ORDER BY id",
            (corpus["ids"][SLUG_A],),
        )
        reextract.sync_metadata(conn, reextract.plan(conn, extract=False))
        after = db.all_rows(
            conn, "SELECT id FROM knowledge_item WHERE document_id = %s ORDER BY id",
            (corpus["ids"][SLUG_A],),
        )
    assert before and [r["id"] for r in before] == [r["id"] for r in after]


def test_a_metadata_pass_needs_no_original_on_disk(corpus, monkeypatch, tmp_path):
    """Nothing about a document row requires the file. A corpus whose originals
    are all archived must still be able to take a manifest correction."""
    monkeypatch.setenv("SOURCE_DIR", str(tmp_path / "empty"))
    _manifest(monkeypatch, {SLUG_A: {"content_status": "draft"}})
    with db.connect() as conn:
        work = reextract.plan(conn, extract=False)
        entry = next(e for e in work if e["slug"] == SLUG_A)
        assert entry["skip"] is None
        assert reextract.sync_metadata(conn, work)["metadata"] == 1


def test_drift_is_described_without_printing_a_name(corpus, monkeypatch):
    """`title`, `original_filename` and the `*_org_id` columns are the whole
    reason private/documents.yaml is gitignored. This output reaches a terminal
    and, from there, a paste into an issue; naming the column says enough."""
    secret = "Some Real Organisation Ltd"
    described = reextract._describe_drift(
        {"title": secret, "original_filename": f"{secret}.pdf", "client_org_id": secret,
         "content_status": "draft"}
    )
    assert secret not in described
    assert "content_status=draft" in described
    assert "title" in described and "client_org_id" in described
