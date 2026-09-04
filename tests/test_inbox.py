"""Tests for the inbox pipeline (tools/ingest_inbox.py and friends).

Uses the live DB per CONTRACT.md -- every test that writes rows scopes its
cleanup to its own tmp_path (for ingest_job, via source_path) or to a
'test-' slug prefix (for source_document), so the suite is safe to re-run
against a shared database.

Two sibling modules this pipeline calls (tools/build_structure.py,
tools/write_extraction.py, tools/embed_chunks.py) are owned by other agents
working in parallel and may not exist yet in this checkout. `tools/classify_
document.py` and `tools/ingest_document.py` are used for real -- they were
available when this suite was written. Where a sibling module is still
missing, `fake_stage_modules` installs a minimal stand-in into sys.modules
for the duration of a test (never written to disk) so the orchestrator's own
wiring -- the thing this file actually tests -- can be exercised end to end
regardless of build order.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

import pymupdf
import pytest

from tools import db, pipeline
from tools import ingest_inbox as ii


# ── helpers ──────────────────────────────────────────────────────────────


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _wait_and_sweep(inbox_dir: Path) -> None:
    """Stability accumulates across sweeps (see stable_candidates): the first
    sweep only starts the clock for a newly-seen file. Two sweeps with real
    time passing between them is what a freshly-dropped file needs, exactly
    as it would from two runs of the daemon's periodic sweep."""
    ii.run_once(dry_run=False)
    time.sleep(ii.STABILITY_SECONDS + 0.05)
    ii.run_once(dry_run=False)


def _make_pdf(path: Path, *, pages: int = 1, width: float = 400.0, height: float = 300.0, tag: str = "") -> Path:
    """A tiny synthetic PDF. Dimensions/page-count deliberately don't match
    any of classify_document's special-cased shapes, so it classifies as
    ('unknown', 0.15) -- low confidence, exercising the needs_review path."""
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=width, height=height)
        page.insert_text((20, 20), f"synthetic fixture page {i + 1} {tag}")
    doc.save(path)
    doc.close()
    return path


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def inbox_env(tmp_path, monkeypatch):
    """A throwaway inbox + private dir, wired up via the same env vars the
    real orchestrator reads, with STABILITY_SECONDS shrunk so tests don't
    need real multi-second sleeps."""
    inbox_dir = tmp_path / "inbox"
    private_dir = tmp_path / "private"
    private_dir.mkdir(parents=True)
    (private_dir / "documents.yaml").write_text("documents: []\n", encoding="utf-8")

    monkeypatch.setenv("INBOX_DIR", str(inbox_dir))
    monkeypatch.setenv("PRIVATE_DIR", str(private_dir))
    monkeypatch.setenv("SOURCE_DIR", str(tmp_path / "source"))
    monkeypatch.setenv("INGEST_LOCK", str(tmp_path / "test.lock"))
    monkeypatch.delenv("RCLONE_CONFIG", raising=False)
    monkeypatch.delenv("RCLONE_REMOTE", raising=False)
    monkeypatch.setattr(ii, "STABILITY_SECONDS", 0.05)

    ii.ensure_state_dirs(inbox_dir)
    return inbox_dir


@pytest.fixture
def fake_stage_modules(monkeypatch):
    """Stand-ins for sibling stages not yet built in this checkout, so the
    orchestrator can be driven all the way to 'done'/'needs_review'. Real
    modules (classify_document, ingest_document) are used unmodified."""
    build_structure = types.ModuleType("tools.build_structure")
    build_structure.build_structure = lambda conn, document_id, path: 0
    write_extraction = types.ModuleType("tools.write_extraction")
    write_extraction.write_extraction = lambda conn, document_id, extraction: {"items": 0}
    embed_chunks = types.ModuleType("tools.embed_chunks")
    embed_chunks.embed_pending = lambda conn, document_id=None: 0

    for name, mod in {
        "tools.build_structure": build_structure,
        "tools.write_extraction": write_extraction,
        "tools.embed_chunks": embed_chunks,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)

    class _FakeExtractor:
        doc_kinds = ("unknown",)

        def extract(self, ctx):
            return pipeline.Extraction()

    monkeypatch.setitem(pipeline._REGISTRY, "unknown", _FakeExtractor())


@pytest.fixture(autouse=True)
def cleanup_test_db_rows(tmp_path):
    """Scoped tightly on purpose: source_path LIKE this test's own tmp_path,
    and slugs this suite could plausibly have produced ('test-' from an
    explicit sidecar, 'unfiled-' from the no-slug-resolved fallback). Also
    removes just the rendered-page directories these rows own, rather than
    the whole (shared, gitignored-but-not-test-owned) .tmp/pages tree."""
    yield
    with db.connect() as conn:
        doc_ids = [
            r["id"]
            for r in db.all_rows(
                conn, "SELECT id FROM source_document WHERE slug LIKE 'test-%' OR slug LIKE 'unfiled-%'"
            )
        ]
        conn.execute("DELETE FROM ingest_job WHERE source_path LIKE %s", (f"{tmp_path}%",))
        conn.execute("DELETE FROM source_document WHERE slug LIKE 'test-%' OR slug LIKE 'unfiled-%'")
        conn.commit()
    for doc_id in doc_ids:
        shutil.rmtree(Path(".tmp") / "pages" / str(doc_id), ignore_errors=True)


# ── stability check ──────────────────────────────────────────────────────


def test_stability_update_pure_logic():
    """No filesystem, no sleeping: the state-machine at the heart of the
    stability check, driven by fabricated clock values."""
    now = 1000.0
    sample = ii.stability_update(None, size=10, mtime=100.0, now=now)
    assert sample.first_seen == now
    assert not ii.is_stable(sample, now=now, stability_seconds=5)

    # unchanged size/mtime on the next look: first_seen must NOT reset
    same = ii.stability_update(sample, size=10, mtime=100.0, now=now + 3)
    assert same.first_seen == now
    assert not ii.is_stable(same, now=now + 3, stability_seconds=5)
    assert ii.is_stable(same, now=now + 5, stability_seconds=5)

    # a size change (still being written) resets the clock
    changed = ii.stability_update(same, size=20, mtime=100.0, now=now + 6)
    assert changed.first_seen == now + 6
    assert not ii.is_stable(changed, now=now + 6, stability_seconds=5)


def test_stability_rejects_file_still_being_written(inbox_env):
    """The single most important correctness property: a file whose size or
    mtime just changed is never picked up, no matter how many sweeps run."""
    path = inbox_env / "growing.bin"
    path.write_bytes(b"x" * 100)
    t0 = path.stat().st_mtime

    assert ii.stable_candidates(inbox_env, now=t0) == []

    # more bytes arrive -- still being copied
    path.write_bytes(b"x" * 5000)
    t1 = path.stat().st_mtime
    assert ii.stable_candidates(inbox_env, now=t1 + 1) == []  # only 1s since the last change

    # no further writes; enough wall-clock time has now passed (generous
    # margin -- the exact-threshold boundary is a floating point hazard,
    # not something worth pinning down to the microsecond in a test)
    stable = ii.stable_candidates(inbox_env, now=t1 + ii.STABILITY_SECONDS + 5)
    assert [p.name for p in stable] == ["growing.bin"]


# ── ignore patterns ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected",
    [
        (".DS_Store", True),
        ("._AppleDoubleFile.pdf", True),
        ("_defaults.yaml", True),
        (".hidden.pdf", True),
        ("report.pdf.crdownload", True),
        ("report.pdf.part", True),
        ("report.pdf.download", True),
        ("report.pdf", False),
        ("calc-fees.xlsx", False),
    ],
)
def test_should_ignore(name, expected):
    assert ii.should_ignore(name) is expected


def test_scan_candidates_excludes_state_dirs_and_sidecars(inbox_env):
    (inbox_env / "document.pdf").write_bytes(b"content")
    (inbox_env / "document.meta.yaml").write_text("slug: test-x\n", encoding="utf-8")
    (inbox_env / "_defaults.yaml").write_text("doc_kind: unknown\n", encoding="utf-8")
    (inbox_env / "_processing" / "leftover.pdf").write_bytes(b"leftover")

    names = [p.name for p in ii.scan_candidates(inbox_env)]
    assert names == ["document.pdf"]


# ── the tiny YAML reader ─────────────────────────────────────────────────


def test_parse_yaml_covers_the_shapes_documents_yaml_uses():
    text = """
    # a comment above a mapping
    defaults:
      confidentiality: client-confidential
      downloadable_by: [owner, editor]

    documents:
      - slug: widget-a
        file: "Some Folder/Widget A.pdf"
        version_label: null
        is_spread_paginated: true
        revision: "0.2"
      - slug: widget-b
        file: "Some Folder/Widget B.pdf"
        notes: >
          first line of a folded
          scalar that keeps going
    """
    data = ii.parse_yaml(text)
    assert data["defaults"]["downloadable_by"] == ["owner", "editor"]
    docs = data["documents"]
    assert len(docs) == 2
    assert docs[0]["slug"] == "widget-a"
    assert docs[0]["version_label"] is None
    assert docs[0]["is_spread_paginated"] is True
    assert docs[0]["revision"] == "0.2"  # stays a string despite looking numeric
    assert docs[1]["notes"] == "first line of a folded scalar that keeps going"


def test_resolve_static_meta_precedence(inbox_env):
    """_defaults.yaml < a documents.yaml filename match < a *.meta.yaml sidecar."""
    (inbox_env / "_defaults.yaml").write_text(
        "client_org: org-batch-default\ndoc_kind: unknown\n", encoding="utf-8"
    )
    known_docs = {"report.pdf": {"slug": "test-known-slug", "doc_kind": "calculator"}}
    (inbox_env / "report.meta.yaml").write_text("doc_kind: crib_sheet\n", encoding="utf-8")

    meta = ii.resolve_static_meta(inbox_env, known_docs, inbox_env / "report.pdf")
    assert meta["client_org"] == "org-batch-default"  # from _defaults.yaml, untouched
    assert meta["slug"] == "test-known-slug"  # from documents.yaml
    assert meta["doc_kind"] == "crib_sheet"  # sidecar wins over both


# ── duplicate by hash ─────────────────────────────────────────────────────


def test_duplicate_by_hash(inbox_env):
    content = b"duplicate-fixture-content-" + os.urandom(16)
    sha = _sha256_bytes(content)
    slug = f"test-dup-{sha[:8]}"

    with db.connect() as conn:
        db.insert_returning_id(
            conn, "source_document", {"slug": slug, "doc_kind": "unknown", "sha256": sha, "size_bytes": len(content)}
        )
        conn.commit()

    (inbox_env / "incoming.bin").write_bytes(content)
    _wait_and_sweep(inbox_env)

    assert not (inbox_env / "incoming.bin").exists()
    dup_files = [p for p in (inbox_env / "_duplicates").iterdir()]
    assert len(dup_files) == 1
    # never registered as a new document -- no second row for this sha
    with db.connect() as conn:
        rows = db.all_rows(conn, "SELECT id FROM source_document WHERE sha256 = %s", (sha,))
    assert len(rows) == 1


# ── revision by slug ──────────────────────────────────────────────────────


def test_revision_by_slug(inbox_env, fake_stage_modules):
    """As of this writing this test fails, and the failure is not in this
    module: tools/ingest_document.py's register_document() INSERTs the new
    source_document row before it UPDATEs the previous row's is_current to
    false. The partial unique index source_document_current_slug is checked
    immediately (Postgres does not defer it here), so the INSERT itself
    raises UniqueViolation while the old row is still is_current -- the
    'registered' stage fails and the file lands in _failed/. CONTRACT.md is
    explicit that the old flag must be cleared in the same transaction as
    the new insert; the bug is the *order* of those two statements, not the
    transaction boundary. This orchestrator's own contribution -- resolving
    the slug, calling register_document inside one run_stage transaction,
    and handling the stage failure by filing the original to _failed/ with
    an error.json -- is correct and is exercised regardless of this bug.
    Left failing (rather than adjusted to match the bug) so it starts
    passing the moment ingest_document.py's insert/update order is fixed."""
    slug = "test-revision-doc"

    _make_pdf(inbox_env / "revA.pdf", tag="A")
    (inbox_env / "revA.meta.yaml").write_text(f"slug: {slug}\n", encoding="utf-8")
    _wait_and_sweep(inbox_env)

    with db.connect() as conn:
        rows = db.all_rows(conn, "SELECT id, is_current, supersedes_id FROM source_document WHERE slug = %s", (slug,))
    assert len(rows) == 1
    first_id, first_current, first_supersedes = rows[0]["id"], rows[0]["is_current"], rows[0]["supersedes_id"]
    assert first_current is True
    assert first_supersedes is None

    # a second, different file dropped under the SAME slug is a new revision
    _make_pdf(inbox_env / "revB.pdf", tag="B", pages=2)
    (inbox_env / "revB.meta.yaml").write_text(f"slug: {slug}\n", encoding="utf-8")
    _wait_and_sweep(inbox_env)

    with db.connect() as conn:
        rows = db.all_rows(
            conn,
            "SELECT id, is_current, supersedes_id FROM source_document WHERE slug = %s ORDER BY ingested_at",
            (slug,),
        )
    assert len(rows) == 2
    old_row, new_row = rows[0], rows[1]
    assert str(old_row["id"]) == str(first_id)
    assert old_row["is_current"] is False  # flipped by the same transaction that inserted the revision
    assert new_row["is_current"] is True
    assert str(new_row["supersedes_id"]) == str(first_id)

    # the partial-unique index invariant: exactly one is_current row per slug
    with db.connect() as conn:
        current_count = db.scalar(
            conn, "SELECT count(*) FROM source_document WHERE slug = %s AND is_current", (slug,)
        )
    assert current_count == 1


# ── crash-resume ──────────────────────────────────────────────────────────


def test_crash_resume(inbox_env, fake_stage_modules, monkeypatch):
    """Simulate a process death right after the 'classified' stage recorded
    ok: the file is left sitting in _processing/ with no finalizing move.
    A fresh sweep must resume that exact job -- not re-run stages that
    already succeeded, and not treat it as a brand new file."""
    import tools.classify_document as classify_document

    calls = {"n": 0}
    real_classify = classify_document.classify

    def counting_classify(path):
        calls["n"] += 1
        return real_classify(path)

    monkeypatch.setattr(classify_document, "classify", counting_classify)

    src = _make_pdf(inbox_env / "crashme.pdf", tag="crash")
    processing_path = inbox_env / "_processing" / src.name
    shutil.move(str(src), str(processing_path))  # as if a prior run had already picked it up

    job_id = ii._find_or_create_job(processing_path, processing_path.name)
    sha = ii._run_hashed(job_id, processing_path, force=False)
    assert ii._run_deduped(job_id, sha, force=False) is None
    ii._run_classified(job_id, processing_path, force=False)
    assert calls["n"] == 1
    # <-- the process "dies" here: no more stages run, file stays put

    assert processing_path.exists()
    ii.run_once(dry_run=False)  # a fresh sweep, as if the daemon restarted

    assert calls["n"] == 1, "classify ran again -- resume re-executed a stage that already succeeded"
    assert not processing_path.exists()

    with db.connect() as conn:
        job = db.one(conn, "SELECT state FROM ingest_job WHERE id = %s", (job_id,))
    assert job["state"] in ("done", "needs_review")

    moved = list((inbox_env / "_done").glob("*/*")) + list((inbox_env / "_review").iterdir())
    assert len(moved) == 1


# ── stale lock ────────────────────────────────────────────────────────────


def _dead_pid() -> int:
    """A pid that was valid but has since been reaped -- guaranteed dead,
    unlike guessing an unused pid number."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def test_stale_lock_is_reclaimed(tmp_path):
    lock_path = tmp_path / "stale.lock"
    lock_path.write_text(str(_dead_pid()), encoding="utf-8")

    ii.acquire_lock(lock_path)  # must not raise
    assert lock_path.read_text().strip() == str(os.getpid())

    ii.release_lock(lock_path)
    assert not lock_path.exists()


def test_live_lock_blocks_a_second_run(tmp_path):
    lock_path = tmp_path / "live.lock"
    lock_path.write_text(str(os.getpid()), encoding="utf-8")  # our own pid: definitely alive

    with pytest.raises(RuntimeError):
        ii.acquire_lock(lock_path)

    lock_path.unlink()
