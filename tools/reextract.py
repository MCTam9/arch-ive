"""Re-run the extractor and the writer over documents already in the corpus.

    python3 -m tools.reextract                      # dry run
    python3 -m tools.reextract --document crib-water --yes
    python3 -m tools.reextract --status

Extraction improves, and all of it lands at write time: `parse_value` recovers
ranges and percentage targets, `is_placeholder` is applied at every benchmark
site, and `write_extraction` enriches citations with `printed_page_label` and
`bbox`. None of that reaches a document written before the change. Measured on
the corpus that motivated this tool: 39 requirements held a numeric
`target_text` with `parsed_ok = false`, 17 of 958 citations carried a printed
page label and none carried a bbox. The data was not wrong when it was written;
it was written by an older extractor and nothing re-reads it.

`tools/refresh_chunk_text.py` is the same situation one layer down -- one
composer, run again over an existing corpus. This is that at the extractor
layer: rebuild the `DocumentContext` exactly as the `extracted` stage does,
call the registered extractor, hand the result to `write_extraction`.

**The consequences are the tool, not the extraction.** `write_extraction`
clears the document's prior `knowledge_item` rows, and the schema cascades:
citations, item chunks, `item_term` facet tags and `item_stage` links go with
them. A re-extract that stops at the writer therefore leaves a document
*less* useful than it found it -- untagged, unlinked, its page chunks rebuilt
from scratch and every chunk unembedded. So each document gets the writer,
then `pipeline.ENRICHMENTS` in order, then the embedder, which is the same
sequence `extracted -> enriched -> embedded` a fresh ingest runs.

What survives, and why:

- `source_page`, `source_asset` and their page renders. Nothing here touches
  the `pages` stage.
- **Figure chunks.** They hang off `source_asset`, not `knowledge_item`, and
  `write_extraction` spares `asset_id IS NOT NULL` deliberately -- a VLM
  description cost real money to produce. `chunk_figures` re-runs anyway and
  is a no-op when nothing changed.
- `doc_node`: the writer upserts nodes on `code`, it never clears them.
- Lookup tables (unit, metric, framework, criterion, rating scale/level).

What does not survive, and is rebuilt here:

- `knowledge_item` + subtype rows, `citation`, item chunks, page chunks,
  `external_reference` -- all rewritten by the writer.
- `item_term`, `item_stage` -- cascaded away, rebuilt by the enrichments.
- `chunk.embedding` -- every rewritten chunk starts NULL, which is exactly the
  state `tools/embed_chunks.py` resumes from.

And two things nothing here can rebuild, which is why this is `--yes`-gated:

- **Human review decisions.** `knowledge_item.review_status` /
  `reviewed_by` are columns on the deleted rows. A document with approved or
  rejected items comes back all-`pending`; the plan says how many.
- **Reference resolution.** `external_reference` rows are rewritten
  unresolved, so `python3 -m tools.resolve_references --yes` is the follow-up
  for any document that cites another.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from tools import db, pipeline
from tools.env import load_env

# One statement of the counts, so "before" and "after" cannot be measured
# differently -- a re-extract that silently loses content is only visible if
# both sides of the comparison are the same query.
_COUNT_SQL = """
SELECT
  (SELECT count(*) FROM knowledge_item k
     WHERE k.document_id = %(doc)s)::int AS items,
  (SELECT count(*) FROM knowledge_item k
     WHERE k.document_id = %(doc)s AND k.review_status <> 'pending')::int AS reviewed,
  (SELECT count(*) FROM citation c
     WHERE c.document_id = %(doc)s AND c.knowledge_item_id IS NOT NULL)::int AS citations,
  (SELECT count(*) FROM citation c
     WHERE c.document_id = %(doc)s AND c.knowledge_item_id IS NOT NULL
       AND c.printed_page_label IS NOT NULL)::int AS labelled,
  (SELECT count(*) FROM citation c
     WHERE c.document_id = %(doc)s AND c.knowledge_item_id IS NOT NULL
       AND c.bbox IS NOT NULL)::int AS bbox,
  (SELECT count(*) FROM chunk ch
     WHERE ch.document_id = %(doc)s AND ch.knowledge_item_id IS NOT NULL)::int AS chunks_item,
  (SELECT count(*) FROM chunk ch
     WHERE ch.document_id = %(doc)s AND ch.knowledge_item_id IS NULL
       AND ch.asset_id IS NULL)::int AS chunks_page,
  (SELECT count(*) FROM chunk ch
     WHERE ch.document_id = %(doc)s AND ch.asset_id IS NOT NULL)::int AS chunks_figure,
  (SELECT count(*) FROM chunk ch
     WHERE ch.document_id = %(doc)s AND ch.embedding IS NULL)::int AS unembedded,
  (SELECT count(*) FROM item_term t JOIN knowledge_item k ON k.id = t.knowledge_item_id
     WHERE k.document_id = %(doc)s)::int AS terms,
  (SELECT count(*) FROM item_stage s JOIN knowledge_item k ON k.id = s.knowledge_item_id
     WHERE k.document_id = %(doc)s)::int AS stage_links
"""


def counts(conn, document_id: str) -> dict[str, int]:
    """Everything a re-extract can destroy, in one row."""
    return dict(db.one(conn, _COUNT_SQL, {"doc": document_id}) or {})


def _source_dir() -> Path:
    return Path(os.environ.get("SOURCE_DIR", "~/arch-ive-source")).expanduser()


def find_original(slug: str, sha256: str) -> Path | None:
    """The document's original file on local disk, or None.

    Two local locations, in the order the rest of the repo uses them:
    SOURCE_DIR/<slug>/<sha256>.<ext>, which `archive_original.archive` wrote,
    then `.tmp/restored/`, where `fetch_original` leaves a restore. **R2 is
    not tried.** A restore decrypts an archived original and writes an
    audit_log row saying a document left the archive; that is a deliberate act
    with a tool of its own, not something a backfill should start doing to 14
    documents because it was run without arguments.
    """
    for candidate in (
        sorted((_source_dir() / slug).glob(f"{sha256}.*")),
        sorted(Path(".tmp/restored").glob(f"{sha256}.*")),
    ):
        if candidate:
            return candidate[0]
    return None


def _static_meta_by_slug() -> dict[str, dict]:
    """slug -> the metadata ingest passed to the extractor, from documents.yaml.

    `meta` is not a column: `register_document` keeps the handful of keys that
    are columns and drops the rest, so the dict an extractor saw cannot be
    read back out of `source_document`. private/documents.yaml is the same
    file the ingest read, so re-reading it reproduces that argument exactly.
    Values may hold real organisation names -- pass them through, never print.
    """
    from tools.ingest_inbox import _private_dir, load_documents_yaml

    by_slug: dict[str, dict] = {}
    for entry in load_documents_yaml(_private_dir()).values():
        slug = entry.get("slug")
        if slug:
            by_slug[slug] = {k: v for k, v in entry.items() if k not in ("slug", "doc_kind")}
    return by_slug


def _meta_for(row: dict, static: dict[str, dict]) -> dict[str, Any]:
    """documents.yaml if it is there, otherwise what the columns still hold.

    The fallback matters for a corpus this tool did not ingest -- a test
    fixture, or a document whose sidecar is long gone: `content_status` is the
    key every extractor actually reads, and it did reach a column.
    """
    if row["slug"] in static:
        return dict(static[row["slug"]])
    return {
        "title": row["title"],
        "content_status": row["content_status"],
        "is_spread_paginated": row["is_spread_paginated"],
    }


# Columns that documents.yaml owns and that only `register_document` ever
# writes -- imported rather than restated, because a second copy of this set is
# how the two levels drifted apart in the first place.
# `register_document` takes `is_spread_paginated` from the manifest, and then
# `ingest_document` overwrites it from the page evidence it just read -- the
# manifest value is a hint, the column is a measurement. Syncing it back would
# undo that. It is the one key in `_DOCUMENT_COLUMNS` the manifest does not own.
_MEASURED_COLUMNS = frozenset({"is_spread_paginated"})

_PRINTABLE_METADATA = frozenset({
    "content_status", "version_label", "language", "confidentiality",
})


def _metadata_drift(conn, row: dict, meta: dict[str, Any]) -> dict[str, Any]:
    """Manifest values the `source_document` row no longer agrees with.

    Every extractor reads `ctx.meta`, so a manifest edited after the ingest
    reaches the knowledge items on the next re-extract -- and stops there,
    because `register_document` runs at ingest and nothing writes these columns
    again. That is how three crib sheets came to hold `content_status = 'real'`
    over items that are every one of them 'draft': the file is a draft, the
    manifest was corrected to say so a day after the ingest, and the document
    row never heard. Only keys the manifest actually carries are compared; an
    absent key is not an instruction to blank a column.

    The comparison is `IS DISTINCT FROM` in Postgres rather than `!=` in
    Python, because the manifest is YAML and the columns are typed: an
    `issue_date` written as a quoted string is a `str` here and a
    `datetime.date` there, and comparing those two reports drift on a value
    that is already correct -- forever, since writing it changes nothing. The
    engine that will do the UPDATE is the one that should decide whether the
    UPDATE is needed, and it settles `date`, the enums and `text[]` alike.
    """
    from tools.ingest_document import _DOCUMENT_COLUMNS

    columns = sorted(c for c in _DOCUMENT_COLUMNS - _MEASURED_COLUMNS if c in meta)
    if not columns:
        return {}
    checks = ", ".join(f"{c} IS DISTINCT FROM %s AS differs_{i}" for i, c in enumerate(columns))
    result = db.one(
        conn,
        f"SELECT {checks} FROM source_document WHERE id = %s",
        [*(meta[c] for c in columns), row["id"]],
    )
    if result is None:  # RLS, or a row retired between the two statements
        return {}
    return {c: meta[c] for i, c in enumerate(columns) if result[f"differs_{i}"]}


def _describe_drift(drift: dict[str, Any]) -> str:
    """Column names always; values only for the ones that cannot name a person
    or an organisation. `title`, `original_filename` and the `*_org_id` columns
    are the whole reason documents.yaml is gitignored -- naming the column is
    enough to say what changed."""
    parts = []
    for column in sorted(drift):
        if column in _PRINTABLE_METADATA:
            parts.append(f"{column}={drift[column]}")
        else:
            parts.append(column)
    return ", ".join(parts)


def _apply_drift(conn, document_id: str, drift: dict[str, Any]) -> None:
    assignments = ", ".join(f"{c} = %s" for c in sorted(drift))
    conn.execute(
        f"UPDATE source_document SET {assignments} WHERE id = %s",
        [*(drift[c] for c in sorted(drift)), document_id],
    )


def plan(conn, document: str | None = None, *, extract: bool = True) -> list[dict]:
    """One entry per current document: what it holds now, and what a re-extract
    would produce.

    The extraction itself happens here, not in `apply`, because extractors are
    pure by contract -- no database, no network -- so running one is a read.
    That is what lets the dry run state real numbers instead of a promise, and
    it is what makes the empty-extraction check a *pre*-condition: `apply` gets
    handed the `Extraction` this function already produced rather than
    re-deriving it and possibly deriving something else.
    """
    if extract:
        pipeline.load_extractors()
    static = _static_meta_by_slug()

    where = " AND d.slug = %s" if document else ""
    rows = db.all_rows(
        conn,
        f"""SELECT d.*, d.id::text AS id, d.doc_kind::text AS doc_kind,
                   d.content_status::text AS content_status
              FROM source_document d
             WHERE d.is_current{where}
             ORDER BY d.slug""",
        (document,) if document else (),
    )

    work: list[dict] = []
    for row in rows:
        meta = _meta_for(row, static)
        entry: dict[str, Any] = {
            "document_id": row["id"],
            "slug": row["slug"],
            "doc_kind": row["doc_kind"],
            "before": counts(conn, row["id"]),
            "extraction": None,
            "predicted": {"items": 0, "citations": 0, "warnings": 0},
            "drift": _metadata_drift(conn, row, meta),
            "skip": None,
        }
        work.append(entry)

        if not extract:
            # A metadata-only pass reads documents.yaml and the document row.
            # It needs neither the original nor the extractor, so it must not
            # report a missing original as a skip.
            continue

        path = find_original(row["slug"], row["sha256"])
        if path is None:
            # Skip, never fail: one missing original must not cost the other
            # thirteen their re-extract.
            entry["skip"] = "no local original (run: python3 -m tools.fetch_original <slug>)"
            continue

        pages = db.all_rows(
            conn,
            "SELECT * FROM source_page WHERE document_id = %s ORDER BY page_index",
            (row["id"],),
        )
        ctx = pipeline.DocumentContext(
            document_id=row["id"],
            slug=row["slug"],
            path=path,
            doc_kind=row["doc_kind"],
            page_count=row["page_count"] or len(pages),
            pages=pages,
            meta=meta,
        )
        try:
            extraction = pipeline.for_doc_kind(row["doc_kind"]).extract(ctx)
        except Exception as exc:  # noqa: BLE001 - one bad shape is not the run
            entry["skip"] = f"extractor raised {type(exc).__name__}: {exc}"
            continue

        entry["extraction"] = extraction
        entry["predicted"] = {
            "items": len(extraction.items),
            "citations": sum(len(i.citations) for i in extraction.items),
            "warnings": len(extraction.warnings),
        }
    return work


def _would_empty(entry: dict) -> bool:
    """The failure this tool exists to not commit: an extractor that now
    returns nothing for a document that currently holds items. The writer would
    delete them and write nothing back, and the document would look ingested."""
    return bool(entry["extraction"] is not None
                and entry["predicted"]["items"] == 0
                and entry["before"]["items"] > 0)


def _reset_account(conn) -> None:
    """Re-apply the RLS account after a rollback.

    `set_config(..., is_local=false)` is still transactional in Postgres: the
    setting `tools.db.connect` made is reverted along with the aborted
    transaction, and every later document on this connection would then see
    zero rows and read as an empty corpus.
    """
    conn.execute("SELECT set_config('app.account_id', %s, false)", (db.account_id(),))


def _sync_one(conn, entry: dict, result: dict) -> None:
    """Write one document's manifest drift, and record what changed."""
    if not entry["drift"]:
        return
    try:
        _apply_drift(conn, entry["document_id"], entry["drift"])
        conn.commit()
        result["metadata"] = _describe_drift(entry["drift"])
    except Exception as exc:  # noqa: BLE001 - one column set is not the run
        conn.rollback()
        _reset_account(conn)
        result["metadata"] = f"FAILED: {type(exc).__name__}: {exc}"


def sync_metadata(conn, work: list[dict]) -> dict:
    """Apply documents.yaml to `source_document` and nothing else.

    Separated from `apply` because it is the cheap half: no original file, no
    extractor, no re-embedding, and nothing a human reviewed is reset. A
    manifest correction is the common case and should not cost a corpus pass.
    """
    results = []
    for entry in work:
        result = {"slug": entry["slug"], "status": "ok", "before": entry["before"], "after": None}
        results.append(result)
        _sync_one(conn, entry, result)
    return {
        "documents": results,
        "ok": 0, "skipped": 0, "refused": 0, "failed": 0, "embedded": 0,
        "metadata": sum(1 for r in results if r.get("metadata")),
    }


def apply(conn, work: list[dict], *, allow_empty: bool = False, embed: bool = True) -> dict:
    """Rewrite each planned document: writer, enrichments, embedder.

    One transaction per document, committed before the next one starts, so a
    failure part-way through a corpus pass leaves every other document exactly
    as it was rather than rolling back a morning's work.
    """
    from tools.write_extraction import write_extraction

    results: list[dict] = []
    for entry in work:
        result = {"slug": entry["slug"], "before": entry["before"], "after": None}
        results.append(result)

        # Metadata first, in a transaction of its own. It needs no original and
        # no extractor, so a document this pass has to skip still gets the
        # manifest applied to its row -- and a failed extraction below cannot
        # roll it back out again.
        _sync_one(conn, entry, result)

        if entry["skip"]:
            result["status"] = "skipped"
            result["detail"] = entry["skip"]
            continue
        if _would_empty(entry) and not allow_empty:
            result["status"] = "refused"
            result["detail"] = (f"extractor returned 0 items, {entry['before']['items']} in the "
                                f"database; pass --allow-empty if that is intended")
            continue

        document_id, slug = entry["document_id"], entry["slug"]
        try:
            written = write_extraction(conn, document_id, entry["extraction"])
            enriched = {
                step.name: step.run(conn, document_id=document_id, slug=slug)
                for step in pipeline.ENRICHMENTS
            }
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - this document only
            conn.rollback()
            _reset_account(conn)
            result["status"] = "failed"
            result["detail"] = f"{type(exc).__name__}: {exc}"
            continue

        result["status"] = "ok"
        result["written"] = {k: v for k, v in written.items() if k != "warnings"}
        result["warnings"] = len(written.get("warnings") or [])
        result["enriched"] = enriched

        # Embedding is committed separately, and after: a model that fails to
        # load must not roll back an extraction that succeeded. What it leaves
        # behind is `embedding IS NULL`, which is the resumable state
        # `tools/embed_chunks.py` was built to pick up.
        if embed:
            result["embedded"] = _embed(conn, document_id)
        result["after"] = counts(conn, document_id)

    ok = [r for r in results if r["status"] == "ok"]
    return {
        "documents": results,
        "ok": len(ok),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "refused": sum(1 for r in results if r["status"] == "refused"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "embedded": sum(r.get("embedded") or 0 for r in ok),
        "metadata": sum(1 for r in results if r.get("metadata")),
    }


def _embed(conn, document_id: str) -> int | None:
    """Chunks embedded, or None if the model is not installed."""
    from tools.embed_chunks import embed_pending, model_available

    if not model_available():
        return None
    try:
        n = embed_pending(conn, document_id)
        conn.commit()
        return n
    except Exception as exc:  # noqa: BLE001 - the extraction is already committed
        conn.rollback()
        _reset_account(conn)
        print(f"  warning: embedding failed for one document ({type(exc).__name__}: {exc}); "
              f"run: python3 -m tools.embed_chunks")
        return None


def status(conn, document: str | None = None) -> int:
    """Where the corpus stands on the write-time gains a re-extract would apply."""
    where = " AND d.slug = %s" if document else ""
    rows = db.all_rows(
        conn,
        f"""SELECT d.slug, d.sha256, d.doc_kind::text AS doc_kind,
                   (SELECT count(*) FROM knowledge_item k
                      WHERE k.document_id = d.id)::int AS items,
                   (SELECT count(*) FROM citation c
                      WHERE c.document_id = d.id AND c.knowledge_item_id IS NOT NULL)::int AS cites,
                   (SELECT count(*) FROM citation c
                      WHERE c.document_id = d.id AND c.knowledge_item_id IS NOT NULL
                        AND c.printed_page_label IS NOT NULL)::int AS labelled,
                   (SELECT count(*) FROM citation c
                      WHERE c.document_id = d.id AND c.knowledge_item_id IS NOT NULL
                        AND c.bbox IS NOT NULL)::int AS bbox,
                   (SELECT count(*) FROM requirement r
                      JOIN knowledge_item k ON k.id = r.knowledge_item_id
                     WHERE k.document_id = d.id AND NOT r.parsed_ok
                       AND r.target_text ~ '[0-9]')::int AS unparsed,
                   (SELECT count(*) FROM knowledge_item k
                      WHERE k.document_id = d.id AND k.review_status <> 'pending')::int AS reviewed
              FROM source_document d
             WHERE d.is_current{where}
             ORDER BY d.slug""",
        (document,) if document else (),
    )
    print(f"  {'document':30} {'items':>6} {'cites':>6} {'labelled':>9} {'bbox':>6} "
          f"{'unparsed':>9} {'reviewed':>9}  original")
    for r in rows:
        original = "yes" if find_original(r["slug"], r["sha256"]) else "MISSING"
        print(f"  {r['slug']:30} {r['items']:>6} {r['cites']:>6} {r['labelled']:>9} "
              f"{r['bbox']:>6} {r['unparsed']:>9} {r['reviewed']:>9}  {original}")
    print("\n  labelled/bbox: citations carrying printed_page_label / bbox -- both are")
    print("  written by tools/write_extraction.py, so a low count means old data.")
    print("  unparsed: requirements whose target_text holds a digit that did not parse.")
    print("  reviewed: human review decisions a re-extract would reset to 'pending'.")
    return 0


def _print_plan(work: list[dict]) -> None:
    print(f"  {'document':30} {'items':>16} {'citations':>16} {'chunks':>8}  note")
    for e in work:
        before, pred = e["before"], e["predicted"]
        chunks = before["chunks_item"] + before["chunks_page"]
        if e["skip"]:
            arrow_items = f"{before['items']:>6} -> {'--':>6}"
            arrow_cites = f"{before['citations']:>6} -> {'--':>6}"
            note = e["skip"]
        else:
            arrow_items = f"{before['items']:>6} -> {pred['items']:>6}"
            arrow_cites = f"{before['citations']:>6} -> {pred['citations']:>6}"
            notes = []
            if _would_empty(e):
                notes.append("REFUSED: 0 items (--allow-empty to override)")
            if before["reviewed"]:
                notes.append(f"resets {before['reviewed']} review decision(s)")
            if pred["warnings"]:
                notes.append(f"{pred['warnings']} extractor warning(s)")
            note = "; ".join(notes)
        if e["drift"]:
            # Shown for a skipped document too: the metadata sync needs no
            # original, so it lands whether or not the extractor runs.
            drift_note = f"metadata: {_describe_drift(e['drift'])}"
            note = f"{drift_note}; {note}" if note else drift_note
        print(f"  {e['slug']:30} {arrow_items:>16} {arrow_cites:>16} {chunks:>8}  {note}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--document", help="restrict to one document slug")
    ap.add_argument("--status", action="store_true",
                    help="what the corpus holds now, and what a re-extract would fix")
    ap.add_argument("--allow-empty", action="store_true",
                    help="write even where the extractor returns 0 items for a document "
                         "that currently has some")
    ap.add_argument("--no-embed", action="store_true",
                    help="leave rewritten chunks unembedded; run tools.embed_chunks after")
    ap.add_argument("--metadata-only", action="store_true",
                    help="apply private/documents.yaml to source_document and stop -- no "
                         "re-extraction, no reset review decisions")
    ap.add_argument("--yes", action="store_true", help="write; without it this is a dry run")
    args = ap.parse_args()

    load_env()
    with db.connect() as conn:
        if args.status:
            return status(conn, args.document)

        work = plan(conn, args.document, extract=not args.metadata_only)
        if not work:
            print(f"reextract: no current document matches {args.document!r}")
            return 1
        print(f"reextract: {len(work)} document(s) in scope\n")

        if args.metadata_only:
            drifted = [e for e in work if e["drift"]]
            for e in drifted:
                print(f"  {e['slug']:30} {_describe_drift(e['drift'])}")
            print(f"\nreextract: {len(drifted)} of {len(work)} document(s) disagree with "
                  f"private/documents.yaml")
            if not drifted:
                return 0
            if not args.yes:
                print("dry run -- source_document metadata only; pass --yes to write")
                return 0
            report = sync_metadata(conn, work)
            print()
            for r in report["documents"]:
                if r.get("metadata"):
                    print(f"  {r['slug']:30} {r['metadata']}")
            print(f"\nreextract: {report['metadata']} document row(s) updated")
            return 0

        _print_plan(work)

        refused = [e for e in work if _would_empty(e) and not args.allow_empty]
        skipped = [e for e in work if e["skip"]]
        print(f"\nreextract: {len(work) - len(refused) - len(skipped)} to rewrite, "
              f"{len(skipped)} skipped, {len(refused)} refused")
        if not args.yes:
            print("dry run -- items, citations, item chunks, facet tags and stage links are")
            print("rewritten in place for each document above; pass --yes to write")
            return 0

        report = apply(conn, work, allow_empty=args.allow_empty, embed=not args.no_embed)

    print()
    for r in report["documents"]:
        if r.get("metadata"):
            print(f"  {r['slug']:30} metadata {r['metadata']}")
        if r["status"] != "ok":
            print(f"  {r['slug']:30} {r['status']}: {r['detail']}")
            continue
        b, a = r["before"], r["after"]
        print(f"  {r['slug']:30} items {b['items']}->{a['items']}  "
              f"citations {b['citations']}->{a['citations']}  "
              f"chunks {b['chunks_item'] + b['chunks_page']}->{a['chunks_item'] + a['chunks_page']}"
              f" (+{a['chunks_figure']} figure chunks kept)")
        print(f"  {'':30} labels {b['labelled']}->{a['labelled']}  "
              f"bbox {b['bbox']}->{a['bbox']}  terms {b['terms']}->{a['terms']}  "
              f"stages {b['stage_links']}->{a['stage_links']}  "
              f"unembedded {a['unembedded']}")
    print(f"\nreextract: {report['ok']} rewritten, {report['skipped']} skipped, "
          f"{report['refused']} refused, {report['failed']} failed; "
          f"{report['embedded']} chunk(s) embedded")
    if args.no_embed or any(r.get("embedded") is None for r in report["documents"]
                            if r["status"] == "ok"):
        print("next: python3 -m tools.embed_chunks")
    print("next: python3 -m tools.resolve_references  (references were rewritten unresolved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
