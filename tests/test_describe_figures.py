"""The description run has to leave a trace, and the trace has to stay opaque.

Describing a figure means its image was sent to whatever produced the text.
`tools/fetch_original.py` has always logged a document leaving the archive;
for one release the larger event -- several hundred crops of that document's
contents going to a model -- was recorded nowhere but as a model name on each
asset row. These tests pin both halves of the fix: that a run logs itself, and
that what it logs cannot identify the client.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tools import db, describe_figures


@pytest.fixture
def figure():
    """A document with one page and one cropped, undescribed asset."""
    slug = f"test-describe-{uuid.uuid4().hex[:8]}"
    with db.transaction() as conn:
        document_id = db.scalar(
            conn,
            "INSERT INTO source_document (slug, sha256) VALUES (%s, %s) RETURNING id",
            (slug, uuid.uuid4().hex),
        )
        page_id = db.scalar(
            conn,
            "INSERT INTO source_page (document_id, page_index) VALUES (%s, 1) RETURNING id",
            (document_id,),
        )
        asset_id = db.scalar(
            conn,
            "INSERT INTO source_asset (page_id, image_key) VALUES (%s, %s) RETURNING id",
            (page_id, f"figures/{document_id}/{uuid.uuid4()}.webp"),
        )
    yield {"document_id": str(document_id), "asset_id": str(asset_id), "slug": slug}
    with db.transaction() as conn:
        # audit_log.document_id is ON DELETE SET NULL, so the rows would survive
        # the document and orphan themselves into every later assertion.
        conn.execute("DELETE FROM audit_log WHERE document_id = %s", (document_id,))
        conn.execute("DELETE FROM source_document WHERE id = %s", (document_id,))


def _run(tmp_path, figure, description="a described figure, long enough to pass"):
    path = tmp_path / "descriptions.jsonl"
    path.write_text(
        json.dumps({"asset_id": figure["asset_id"], "description": description,
                    "model": "test-model"}) + "\n",
        encoding="utf-8",
    )
    with db.connect() as conn:
        written, problems = describe_figures.load(conn, path)
        conn.commit()
        logged = describe_figures.audit(conn, written)
    return written, problems, logged


def test_a_run_writes_one_audit_row_per_document(tmp_path, figure):
    written, problems, logged = _run(tmp_path, figure)
    assert problems == []
    assert written == [figure["asset_id"]]
    assert logged == 1

    with db.connect() as conn:
        rows = db.all_rows(
            conn,
            "SELECT action, detail FROM audit_log WHERE document_id = %s",
            (figure["document_id"],),
        )
    assert len(rows) == 1, "one row per document per run, not one per figure"
    assert rows[0]["action"] == "describe"
    assert rows[0]["detail"]["producer"] == "test-model"
    assert rows[0]["detail"]["figures"] == 1
    assert rows[0]["detail"]["via"] == "tools.describe_figures"


def test_the_row_names_no_title_slug_or_path(tmp_path, figure):
    """The log's one security property: it references documents by id only.

    Every other column is a uuid, so `detail` is the only place a slug, a title
    or a filesystem path could ever reach the table. Nothing that reads
    audit_log should be able to learn what the corpus is about.
    """
    _run(tmp_path, figure)
    with db.connect() as conn:
        detail = db.all_rows(
            conn,
            "SELECT detail::text AS d FROM audit_log WHERE document_id = %s AND action = 'describe'",
            (figure["document_id"],),
        )[0]["d"]
    assert figure["slug"] not in detail
    assert "/" not in detail, "a path reached the log; the crop key must never be recorded"


def test_audit_failure_never_costs_the_run(tmp_path, figure):
    """The descriptions are committed before this runs, so a broken log must
    degrade to a warning. The alternative -- an exception here -- would abort a
    run whose expensive half already succeeded."""
    with db.connect() as conn:
        assert describe_figures.audit(conn, ["not-a-uuid"]) == 0
        assert describe_figures.audit(conn, []) == 0


def test_a_rejected_line_is_not_logged_as_described(tmp_path, figure):
    """`written` carries ids, not a count, precisely so the log can only ever
    name assets the database actually accepted."""
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"asset_id": figure["asset_id"], "description": "tiny",
                                "model": "test-model"}) + "\n", encoding="utf-8")
    with db.connect() as conn:
        written, problems = describe_figures.load(conn, path)
        assert written == []
        assert len(problems) == 1
        assert describe_figures.audit(conn, written) == 0
