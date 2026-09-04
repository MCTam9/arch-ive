"""Hybrid retrieval over `chunk`: full-text always, vector cosine when any
chunk has an embedding, fused by reciprocal rank fusion. Every result carries
its citation (document slug + page) because an answer with no page to point
at is not useful in this corpus.
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


def _facet_join(facets: dict[str, str] | None) -> tuple[str, list]:
    """One item_term join per facet value, ANDed -- a result must carry every
    requested term. `facets` maps an arbitrary label to a taxonomy_term id."""
    if not facets:
        return "", []
    clauses = []
    params: list = []
    for i, term_id in enumerate(facets.values()):
        alias = f"ft{i}"
        clauses.append(
            f"JOIN item_term {alias} ON {alias}.knowledge_item_id = ki.id AND {alias}.term_id = %s"
        )
        params.append(term_id)
    return " ".join(clauses), params


def search(conn, query: str, *, facets: dict[str, str] | None = None, limit: int = 20) -> list[dict]:
    """Hybrid full-text + vector search over knowledge-item chunks.

    Excludes content_status != 'real' by default (draft/wip/lorem/template
    content should never masquerade as a real answer). Every row carries
    document_slug + page_index (its citation) alongside the chunk text.
    """
    facet_sql, facet_params = _facet_join(facets)

    fts_rows = db.all_rows(
        conn,
        f"""SELECT c.id AS chunk_id,
                   row_number() OVER (ORDER BY ts_rank(c.tsv, websearch_to_tsquery('english', %s)) DESC) AS rnk
            FROM chunk c
            JOIN knowledge_item ki ON ki.id = c.knowledge_item_id
            {facet_sql}
            WHERE c.tsv @@ websearch_to_tsquery('english', %s)
              AND c.content_status = 'real' AND ki.content_status = 'real'
              AND ki.review_status <> 'rejected'
            ORDER BY rnk
            LIMIT 200""",
        (query, *facet_params, query),
    )

    vec_rows = []
    embedding = _embed_query(query)
    if embedding is not None and db.scalar(conn, "SELECT 1 FROM chunk WHERE embedding IS NOT NULL LIMIT 1"):
        vec_rows = db.all_rows(
            conn,
            f"""SELECT c.id AS chunk_id,
                       row_number() OVER (ORDER BY c.embedding <=> %s::vector) AS rnk
                FROM chunk c
                JOIN knowledge_item ki ON ki.id = c.knowledge_item_id
                {facet_sql}
                WHERE c.embedding IS NOT NULL
                  AND c.content_status = 'real' AND ki.content_status = 'real'
                  AND ki.review_status <> 'rejected'
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
    rows = db.all_rows(
        conn,
        """SELECT c.id AS chunk_id, c.text, c.knowledge_item_id, c.page_from, c.page_to,
                  ki.item_type, ki.title, ki.statement,
                  d.slug AS document_slug, d.title AS document_title
           FROM chunk c
           LEFT JOIN knowledge_item ki ON ki.id = c.knowledge_item_id
           JOIN source_document d ON d.id = c.document_id
           WHERE c.id = ANY(%s)""",
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
    building use and target year."""
    sql = "SELECT * FROM v_benchmark WHERE metric_id = %s"
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
            out = search(conn, args.query, facets=facets, limit=args.limit)
        elif args.cmd == "benchmark":
            out = get_benchmark(conn, args.metric_id, args.building_use, args.year)
        else:
            out = get_requirement_matrix(conn, args.framework_slug, args.level)

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
