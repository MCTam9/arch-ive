"""Put figure descriptions into `chunk`, so search can reach them.

    python3 -m tools.chunk_figures                 # dry run: what would change
    python3 -m tools.chunk_figures --yes
    python3 -m tools.chunk_figures --status
    python3 -m tools.embed_chunks                  # always the second step

`source_asset.vlm_description` was write-only data. 898 figures carry one and
nothing outside `tools/describe_figures.py` ever read them: they are in no
index, so a table locked inside a raster -- water benchmarks, an EN 15978
module map, a fire-safety matrix banded by building height -- could not be
found by anyone searching for exactly what it contains.

`chunk.asset_id` has existed since the table was created and has never been
written. `tools/write_extraction.py` already refuses to delete rows that carry
it ("keeps figure chunks, which this stage did not write and has no business
deleting"), and `workflows/ingest_documents.md` records that the
`Decorative image -- ` prefix exists to make those rows one LIKE away from
exclusion "whenever figures reach the index". This is that step; the slot was
prepared for it.

**Decorative figures are skipped.** 114 of the 898 are stock photography, brand
covers and slide dividers. Their descriptions are honest and useless: indexing
them would put 114 plausible paragraphs about nothing into a corpus people
search for guidance.

**Provenance.** `content_status` stays 'real' and that is deliberate: it
describes how finished the *source* is, and the figure genuinely appears in the
document. Who wrote the sentence is a different axis, carried by
`source_asset.vlm_model`, which `asset_id` lets any reader recover. See
`db/migrate/2026-09-05_figure_assets.sql` for the same argument at the point
the columns were added.
"""
from __future__ import annotations

import argparse
import sys

from tools import db
from tools.env import load_env

DECORATIVE_PREFIX = "Decorative image — "

# Assets that should have a chunk, with the columns one needs.
_ELIGIBLE = """
    SELECT a.id::text        AS asset_id,
           a.vlm_description AS text,
           a.vlm_model       AS model,
           p.document_id::text AS document_id,
           p.page_index      AS page_index,
           d.slug            AS document_slug
      FROM source_asset a
      JOIN source_page p     ON p.id = a.page_id
      JOIN source_document d ON d.id = p.document_id
     WHERE a.vlm_description IS NOT NULL
       AND a.image_key IS NOT NULL
       AND a.vlm_description NOT LIKE %s
"""


def plan(conn, document: str | None = None) -> dict[str, list[dict]]:
    """What a run would do: rows to insert, to rewrite, and to remove.

    `stale` is the case that makes this safe to re-run after a re-description:
    a figure whose description was withdrawn, or rewritten into a decorative
    one, keeps a chunk that no longer corresponds to anything. Left behind it
    stays searchable and cites a figure nobody would describe that way now.
    """
    params: list = [DECORATIVE_PREFIX + "%"]
    sql = _ELIGIBLE
    if document:
        sql += " AND d.slug = %s"
        params.append(document)
    eligible = {r["asset_id"]: r for r in db.all_rows(conn, sql, tuple(params))}

    have_sql = ("SELECT c.id::text AS chunk_id, c.asset_id::text AS asset_id, c.text "
                "FROM chunk c WHERE c.asset_id IS NOT NULL")
    have_params: tuple = ()
    if document:
        have_sql += (" AND c.document_id = (SELECT id FROM source_document WHERE slug = %s)")
        have_params = (document,)
    existing = {r["asset_id"]: r for r in db.all_rows(conn, have_sql, have_params)}

    insert = [r for aid, r in eligible.items() if aid not in existing]
    rewrite = [
        {**r, "chunk_id": existing[aid]["chunk_id"]}
        for aid, r in eligible.items()
        if aid in existing and existing[aid]["text"] != r["text"]
    ]
    stale = [r for aid, r in existing.items() if aid not in eligible]
    return {"insert": insert, "rewrite": rewrite, "stale": stale}


def apply(conn, work: dict[str, list[dict]]) -> dict[str, int]:
    """Write the plan. New rows land with a NULL embedding, and a rewritten one
    has its embedding cleared -- that is the whole re-embed protocol, since
    `embed_chunks` selects on `embedding IS NULL`. A stale vector left attached
    to changed text is worse than no vector: the row stays findable, at the
    wrong coordinates, with nothing to show for it. `tsv` is a generated
    column and needs no step at all."""
    for r in work["insert"]:
        conn.execute(
            "INSERT INTO chunk (document_id, asset_id, page_from, page_to, text, content_status) "
            "VALUES (%s, %s, %s, %s, %s, 'real')",
            (r["document_id"], r["asset_id"], r["page_index"], r["page_index"], r["text"]),
        )
    for r in work["rewrite"]:
        conn.execute(
            "UPDATE chunk SET text = %s, embedding = NULL WHERE id = %s",
            (r["text"], r["chunk_id"]),
        )
    for r in work["stale"]:
        conn.execute("DELETE FROM chunk WHERE id = %s", (r["chunk_id"],))
    return {k: len(v) for k, v in work.items()}


def status(conn) -> int:
    rows = db.all_rows(
        conn,
        """
        SELECT d.slug,
               count(*) FILTER (WHERE a.vlm_description IS NOT NULL)::int         AS described,
               count(*) FILTER (WHERE a.vlm_description LIKE %s)::int             AS decorative,
               count(*) FILTER (WHERE c.id IS NOT NULL)::int                      AS chunked,
               count(*) FILTER (WHERE c.id IS NOT NULL AND c.embedding IS NULL)::int AS unembedded
          FROM source_asset a
          JOIN source_page p     ON p.id = a.page_id
          JOIN source_document d ON d.id = p.document_id
          LEFT JOIN chunk c      ON c.asset_id = a.id
         GROUP BY d.slug HAVING count(*) FILTER (WHERE a.vlm_description IS NOT NULL) > 0
         ORDER BY 4 DESC, 1
        """,
        (DECORATIVE_PREFIX + "%",),
    )
    if not rows:
        print("chunk_figures: nothing described yet -- run tools/describe_figures.py first")
        return 0
    print(f"  {'document':34} {'described':>9} {'decorative':>11} {'chunked':>8} {'unembedded':>11}")
    for r in rows:
        print(f"  {r['slug']:34} {r['described']:>9} {r['decorative']:>11} "
              f"{r['chunked']:>8} {r['unembedded']:>11}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--document", help="restrict to one document slug")
    ap.add_argument("--status", action="store_true", help="what is described and what is indexed")
    ap.add_argument("--yes", action="store_true", help="write; without it this is a dry run")
    args = ap.parse_args()

    load_env()
    with db.connect() as conn:
        if args.status:
            return status(conn)

        work = plan(conn, args.document)
        counts = {k: len(v) for k, v in work.items()}
        for r in work["insert"][:3]:
            print(f"\n--- {r['document_slug']} p{r['page_index']} ---\n{r['text'][:180]}…")
        print(f"\nchunk_figures: {counts['insert']} to insert, {counts['rewrite']} to rewrite, "
              f"{counts['stale']} stale to remove")
        if not args.yes:
            print("pass --yes to write")
            return 0

        written = apply(conn, work)
        conn.commit()
        print(f"chunk_figures: inserted {written['insert']}, rewrote {written['rewrite']}, "
              f"removed {written['stale']}")
        if written["insert"] or written["rewrite"]:
            print("next: python3 -m tools.embed_chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
