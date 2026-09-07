"""Hybrid retrieval over `v_retrievable_chunk`: full-text always, vector cosine
when any chunk has an embedding, fused by reciprocal rank fusion. Every result
carries its citation (document slug + page) because an answer with no page to
point at is not useful in this corpus.

What is retrievable at all -- rejected items excluded, the page-text floor,
what counts as placeholder -- is not decided here. It is stated once in
db/schema.sql and read by this module, tools/mcp_server.py and
web/lib/queries.ts alike. Ranking is not shared and deliberately so: the vector
leg needs pgvector and a local embedding model, neither of which can run in a
Vercel function, so the web keeps its own lexical fallback.
"""
from __future__ import annotations

import argparse
import json
import sys

from tools import db

RRF_K = 60  # standard RRF damping constant


def _embed_query(text: str) -> str | None:
    """Best-effort query embedding; None (and search falls back to text-only)
    if sentence-transformers isn't installed or nothing is embedded yet."""
    try:
        from tools.embed_chunks import _load_model
    except ImportError:
        return None
    model = _load_model()
    if model is None:
        return None
    vec = model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0]
    return "[" + ",".join(f"{x:.8f}" for x in vec.tolist()) + "]"


def _facet_clauses(facets: dict[str, str] | None,
                    item_col: str = "c.knowledge_item_id") -> tuple[str, list]:
    """One `item_has_term(...)` per facet value, ANDed -- a result must carry
    every requested term. `facets` maps an arbitrary label to a taxonomy_term id.

    The subtree rule this used to spell out by hand (a term matches its whole
    ltree subtree, EXISTS rather than a JOIN so a chunk is never multiplied)
    now lives in db/schema.sql, where web/lib/queries.ts reads the same one.
    `item_has_term` is non-strict on purpose: a page or figure chunk has no
    knowledge_item and gets false rather than NULL.

    `item_col` names the uuid column to test, so a caller querying a different
    surface -- v_retrievable_item, or the raw knowledge_item table in
    tests/test_facet_subtree.py -- can point the same clause at its own."""
    if not facets:
        return "", []
    clauses = []
    params: list = []
    for term_id in facets.values():
        clauses.append(f"AND item_has_term({item_col}, %s)")
        params.append(term_id)
    return " ".join(clauses), params


def search(conn, query: str, *, facets: dict[str, str] | None = None, limit: int = 20,
            include_placeholder: bool = False, require_citation: bool = True) -> list[dict]:
    """Hybrid full-text + vector search over v_retrievable_chunk.

    The view carries the policy: rejected items and sub-floor page text are
    already gone from it, and `is_placeholder` / `has_citation` are columns
    rather than clauses restated here. The two flags are keyword-only and
    default to the safe answer -- real content, with a page to point at.

    `include_placeholder=True` returns lorem/template/wip/draft content (and
    anything sitting on a page marked as such), every row labelled
    `is_placeholder` with the offending status in `placeholder_status`.
    """
    facet_sql, facet_params = _facet_clauses(facets)
    policy = ""
    if not include_placeholder:
        policy += " AND NOT c.is_placeholder"
    if require_citation:
        policy += " AND c.has_citation"

    fts_rows = db.all_rows(
        conn,
        f"""SELECT c.chunk_id,
                   row_number() OVER (ORDER BY ts_rank(c.tsv, websearch_to_tsquery('english', %s)) DESC) AS rnk
            FROM v_retrievable_chunk c
            WHERE c.tsv @@ websearch_to_tsquery('english', %s)
              {policy}
              {facet_sql}
            ORDER BY rnk
            LIMIT 200""",
        (query, query, *facet_params),
    )

    vec_rows = []
    embedding = _embed_query(query)
    if embedding is not None and db.scalar(conn, "SELECT 1 FROM chunk WHERE embedding IS NOT NULL LIMIT 1"):
        vec_rows = db.all_rows(
            conn,
            f"""SELECT c.chunk_id,
                       row_number() OVER (ORDER BY c.embedding <=> %s::vector) AS rnk
                FROM v_retrievable_chunk c
                WHERE c.embedding IS NOT NULL
                  {policy}
                  {facet_sql}
                ORDER BY rnk
                LIMIT 200""",
            (embedding, *facet_params),
        )

    fused: dict[str, float] = {}
    for r in fts_rows:
        fused[r["chunk_id"]] = fused.get(r["chunk_id"], 0.0) + 1.0 / (RRF_K + r["rnk"])
    for r in vec_rows:
        fused[r["chunk_id"]] = fused.get(r["chunk_id"], 0.0) + 1.0 / (RRF_K + r["rnk"])

    if not fused:
        return []

    ranked_ids = sorted(fused, key=fused.get, reverse=True)[:limit]
    # Hydrate through the same view the ranking legs read, so a row the policy
    # excludes cannot re-enter here by way of a stale id.
    rows = db.all_rows(
        conn,
        """SELECT c.chunk_id, c.text, c.knowledge_item_id, c.page_from, c.page_to,
                  c.item_type, c.title, c.statement, c.content_status,
                  c.is_placeholder, c.placeholder_status,
                  c.document_slug, c.document_title
           FROM v_retrievable_chunk c
           WHERE c.chunk_id = ANY(%s)""",
        (ranked_ids,),
    )
    by_id = {r["chunk_id"]: r for r in rows}

    results = []
    for chunk_id in ranked_ids:
        row = by_id.get(chunk_id)
        if row is None:
            continue
        results.append({
            "score": round(fused[chunk_id], 6),
            "chunk_id": str(chunk_id),
            "knowledge_item_id": str(row["knowledge_item_id"]) if row["knowledge_item_id"] else None,
            "item_type": row["item_type"],
            "title": row["title"],
            "statement": row["statement"],
            "text": row["text"],
            "content_status": row["content_status"],
            "is_placeholder": row["is_placeholder"],
            "placeholder_status": row["placeholder_status"],
            "citation": {
                "document_slug": row["document_slug"],
                "document_title": row["document_title"],
                "page_from": row["page_from"],
                "page_to": row["page_to"],
            },
        })
    return results


def get_benchmark(conn, metric_id: str, building_use: str | None = None, year: int | None = None) -> list[dict]:
    """Benchmarks for a metric, via v_benchmark, optionally narrowed by
    building use and target year.

    `has_citation` is not optional. CONTRACT.md: a row carrying neither a
    document slug nor a page is dropped, not returned with a null citation.
    tools/mcp_server.get_benchmark enforced that and this one did not, which is
    the shape of divergence the view exists to end."""
    sql = "SELECT * FROM v_benchmark WHERE has_citation AND metric_id = %s"
    params: list = [metric_id]
    if building_use is not None:
        sql += " AND building_use_id = %s"
        params.append(building_use)
    if year is not None:
        sql += " AND target_year = %s"
        params.append(year)
    sql += " ORDER BY target_year NULLS LAST, value_numeric NULLS LAST"
    return db.all_rows(conn, sql, params)


def get_requirement_matrix(conn, framework_slug: str, level: str | None = None) -> list[dict]:
    """Requirement rows for a framework via v_requirement_matrix, optionally
    narrowed to one rating level (matched on level code or name)."""
    sql = "SELECT * FROM v_requirement_matrix WHERE framework_slug = %s"
    params: list = [framework_slug]
    if level is not None:
        sql += " AND (level_code = %s OR level_name = %s)"
        params.extend([level, level])
    sql += " ORDER BY criterion_path NULLS LAST, level_ordinal NULLS LAST"
    return db.all_rows(conn, sql, params)


def _main() -> int:
    ap = argparse.ArgumentParser(description="Hybrid search over the arch-ive knowledge base.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", help="hybrid full-text + vector search")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--facet", action="append", default=[], metavar="TERM_ID",
                     help="taxonomy_term id to require; repeatable")
    sp.add_argument("--include-placeholder", action="store_true",
                     help="also return lorem/template/wip/draft content, labelled")
    sp.add_argument("--allow-uncited", action="store_true",
                     help="keep rows with no page to point at (dropped by default)")

    bp = sub.add_parser("benchmark", help="get_benchmark")
    bp.add_argument("metric_id")
    bp.add_argument("--building-use")
    bp.add_argument("--year", type=int)

    mp = sub.add_parser("matrix", help="get_requirement_matrix")
    mp.add_argument("framework_slug")
    mp.add_argument("--level")

    args = ap.parse_args()

    with db.connect() as conn:
        if args.cmd == "search":
            facets = {t: t for t in args.facet} or None
            out = search(conn, args.query, facets=facets, limit=args.limit,
                          include_placeholder=args.include_placeholder,
                          require_citation=not args.allow_uncited)
        elif args.cmd == "benchmark":
            out = get_benchmark(conn, args.metric_id, args.building_use, args.year)
        else:
            out = get_requirement_matrix(conn, args.framework_slug, args.level)

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
