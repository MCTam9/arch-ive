"""Local MCP retrieval server for arch-ive.

Runs over stdio, on a READ-ONLY database role (`arch_read`, see db/roles.sql).
There is no public endpoint -- this is meant to be registered with
`claude mcp add` and run on the same machine as the database.

Two rules shape every tool here:

  1. Every result carries a citation (document slug + page). A row with no
     page to point at is dropped before it reaches the caller, not returned
     with a null citation -- an answer nobody can verify is worse than no
     answer.
  2. Placeholder content (`content_status` in {lorem, template, wip, draft})
     is excluded by default. ~24% of one document in this corpus is
     lorem/WIP filler; serving it as guidance is the single biggest failure
     mode of this system. Tools that touch content accept an
     `include_placeholder` flag for the rare case it's wanted deliberately,
     and every row so returned is labelled `is_placeholder: true`.

Connection handling deliberately does NOT go through tools/db.py: that
module connects as arch_app (read-write) using DATABASE_URL. This server
connects as arch_read using DATABASE_URL_READONLY, setting app.account_id
per connection the same way tools/db.py does, because RLS is FORCED and an
unset account sees nothing.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import db  # noqa: E402  (all_rows/one/scalar are plain executors, not connection factories)
from tools.search import _facet_clauses, search as _hybrid_search  # noqa: E402

READONLY_DEFAULT_DSN = "postgresql://arch_read:dev@localhost:55432/postgres"
DEFAULT_ACCOUNT = "00000000-0000-0000-0000-0000000000aa"

# content_status values that must never be served as fact by default.
PLACEHOLDER_STATUSES = ("lorem", "template", "wip", "draft")

TEXT_TRUNCATE_LIMIT = 600


def readonly_dsn() -> str:
    return os.environ.get("DATABASE_URL_READONLY", READONLY_DEFAULT_DSN)


def account_id() -> str:
    return os.environ.get("ARCHIVE_ACCOUNT_ID", DEFAULT_ACCOUNT)


@contextmanager
def read_connect() -> Iterator[psycopg.Connection]:
    """A connection as arch_read with the RLS account applied for its whole
    lifetime. Separate from tools.db.connect(): that one is arch_app."""
    with psycopg.connect(readonly_dsn(), row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.account_id', %s, false)", (account_id(),))
        yield conn


# ─────────────────────────────────────────────────────────────────────────
# Small helpers shared across tools
# ─────────────────────────────────────────────────────────────────────────

def _clamp(n: int | None, default: int, maximum: int) -> int:
    if n is None:
        return default
    return max(1, min(int(n), maximum))


def _truncate_strings(obj: Any, limit: int = TEXT_TRUNCATE_LIMIT) -> tuple[Any, bool]:
    """Walk a JSON-ish structure, truncating long strings. Returns
    (possibly-modified copy, whether anything was truncated) so callers can
    say so rather than silently shortening an answer."""
    truncated = False
    if isinstance(obj, str):
        if len(obj) > limit:
            return obj[:limit] + f"… [truncated, {len(obj)} chars total]", True
        return obj, False
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k], t = _truncate_strings(v, limit)
            truncated = truncated or t
        return out, truncated
    if isinstance(obj, list):
        out = []
        for v in obj:
            v2, t = _truncate_strings(v, limit)
            out.append(v2)
            truncated = truncated or t
        return out, truncated
    return obj, False


def _content_status_exclude_clause(column: str, params: list) -> str:
    """Parameterised NOT IN over the fixed placeholder-status allowlist.
    The excluded values are a Python constant, never user input."""
    placeholders = ", ".join(["%s"] * len(PLACEHOLDER_STATUSES))
    params.extend(PLACEHOLDER_STATUSES)
    return f"{column} NOT IN ({placeholders})"


def _existing_columns(conn: psycopg.Connection, view_name: str) -> set[str]:
    rows = db.all_rows(
        conn,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        (view_name,),
    )
    return {r["column_name"] for r in rows}


# ─────────────────────────────────────────────────────────────────────────
# Tool implementations -- pure functions over a supplied connection, so
# tests can call them directly against a test database without going
# through stdio or the MCP protocol at all.
# ─────────────────────────────────────────────────────────────────────────

def search_knowledge(conn, query: str, *, facets: dict[str, str] | None = None,
                      limit: int = 20, include_placeholder: bool = False) -> dict:
    """Full-text/vector hybrid search over chunk.tsv, via tools.search.search.
    `facets` maps an arbitrary label to a taxonomy_term id, ANDed together --
    same contract as tools/search.py's CLI."""
    limit = _clamp(limit, default=20, maximum=50)

    if not include_placeholder:
        results = _hybrid_search(conn, query, facets=facets, limit=limit)
        results = [r for r in results if r["citation"].get("page_from") is not None
                   or r["citation"].get("page_to") is not None]
        for r in results:
            r["is_placeholder"] = False
        payload, truncated = _truncate_strings(results)
        return {"query": query, "results": payload, "truncated": truncated,
                "placeholder_included": False}

    # Explicit opt-in escape hatch: full-text only (no vector fusion), the
    # content_status filter relaxed, every row labelled. This is deliberately
    # a separate, simpler code path rather than a flag threaded through
    # tools.search.search -- that function's default (real-only) behaviour
    # must stay the safe default with no way to weaken it by accident.
    facet_sql, facet_params = _facet_clauses(facets)
    rows = db.all_rows(
        conn,
        f"""SELECT c.id AS chunk_id, c.text, c.knowledge_item_id, c.page_from, c.page_to,
                   c.content_status AS chunk_content_status,
                   ki.item_type, ki.title, ki.statement,
                   ki.content_status AS item_content_status,
                   d.slug AS document_slug, d.title AS document_title
            FROM chunk c
            JOIN knowledge_item ki ON ki.id = c.knowledge_item_id
            JOIN source_document d ON d.id = c.document_id
            WHERE c.tsv @@ websearch_to_tsquery('english', %s)
              AND ki.review_status <> 'rejected'
              AND (c.page_from IS NOT NULL OR c.page_to IS NOT NULL)
              {facet_sql}
            ORDER BY ts_rank(c.tsv, websearch_to_tsquery('english', %s)) DESC
            LIMIT %s""",
        (query, *facet_params, query, limit),
    )
    results = []
    for r in rows:
        is_placeholder = (r["chunk_content_status"] in PLACEHOLDER_STATUSES
                           or r["item_content_status"] in PLACEHOLDER_STATUSES)
        results.append({
            "chunk_id": str(r["chunk_id"]),
            "knowledge_item_id": str(r["knowledge_item_id"]) if r["knowledge_item_id"] else None,
            "item_type": r["item_type"],
            "title": r["title"],
            "statement": r["statement"],
            "text": r["text"],
            "is_placeholder": is_placeholder,
            "content_status": r["item_content_status"],
            "citation": {
                "document_slug": r["document_slug"],
                "document_title": r["document_title"],
                "page_from": r["page_from"],
                "page_to": r["page_to"],
            },
        })
    payload, truncated = _truncate_strings(results)
    return {"query": query, "results": payload, "truncated": truncated,
            "placeholder_included": True,
            "warning": "placeholder/draft/wip/template content included on request; "
                       "check is_placeholder on each row before treating it as fact"}


def get_benchmark(conn, *, metric: str | None = None, building_use: str | None = None,
                   target_year: int | None = None, limit: int = 50,
                   include_placeholder: bool = False) -> dict:
    """Benchmarks from v_benchmark, optionally narrowed by metric, building
    use and target year."""
    limit = _clamp(limit, default=50, maximum=200)
    clauses: list[str] = ["page_index IS NOT NULL"]  # no citation, no result
    params: list[Any] = []
    if metric is not None:
        clauses.append("metric_id = %s")
        params.append(metric)
    if building_use is not None:
        clauses.append("building_use_id = %s")
        params.append(building_use)
    if target_year is not None:
        clauses.append("target_year = %s")
        params.append(target_year)
    if not include_placeholder:
        clauses.append(_content_status_exclude_clause("content_status", params))

    sql = ("SELECT * FROM v_benchmark WHERE " + " AND ".join(clauses) +
           " ORDER BY target_year NULLS LAST, value_numeric NULLS LAST LIMIT %s")
    params.append(limit)
    rows = db.all_rows(conn, sql, params)
    for r in rows:
        r["knowledge_item_id"] = str(r["knowledge_item_id"])
        r["is_placeholder_content"] = r["content_status"] in PLACEHOLDER_STATUSES
    payload, truncated = _truncate_strings(rows)
    return {"results": payload, "truncated": truncated, "placeholder_included": include_placeholder}


# Columns get_requirement_matrix will ask for, in order. This is a fixed
# allowlist in code -- never derived from caller input -- checked at query
# time against information_schema so a column another agent renames or drops
# (a scope dimension is being added to this view concurrently) degrades the
# response instead of raising.
_REQUIREMENT_MATRIX_COLUMNS = [
    "knowledge_item_id", "framework_slug", "criterion_code", "criterion", "criterion_path",
    "level_ordinal", "level_code", "level_name", "statement", "target_text", "target_value",
    "unit", "comparator", "is_deliverable", "deliverable_name", "content_status",
    "review_status", "document_slug", "page_index",
]


def get_requirement_matrix(conn, *, topic: str | None = None, level: str | None = None,
                            framework: str | None = None, limit: int = 50,
                            include_placeholder: bool = False) -> dict:
    """Requirement rows from v_requirement_matrix. Coded defensively: selects
    named columns from a fixed allowlist, intersected at query time with
    whatever the view actually exposes, so a concurrent schema change to this
    view degrades rather than crashes this tool."""
    try:
        available = _existing_columns(conn, "v_requirement_matrix")
    except psycopg.Error as exc:
        return {"error": f"v_requirement_matrix unavailable: {exc}", "results": []}

    if not available:
        return {"error": "v_requirement_matrix not found", "results": []}

    columns = [c for c in _REQUIREMENT_MATRIX_COLUMNS if c in available]
    missing = [c for c in _REQUIREMENT_MATRIX_COLUMNS if c not in available]
    if "document_slug" not in columns or "page_index" not in columns:
        return {"error": "v_requirement_matrix no longer exposes a citation "
                          "(document_slug/page_index) -- refusing to return uncited rows",
                "results": [], "missing_columns": missing}

    clauses = ["page_index IS NOT NULL"]
    params: list[Any] = []

    if framework is not None:
        if "framework_slug" in available:
            clauses.append("framework_slug = %s")
            params.append(framework)
        else:
            missing.append("framework_slug (filter ignored)")

    if level is not None:
        level_cols = [c for c in ("level_code", "level_name") if c in available]
        if level_cols:
            clauses.append("(" + " OR ".join(f"{c} = %s" for c in level_cols) + ")")
            params.extend([level] * len(level_cols))
        else:
            missing.append("level_code/level_name (filter ignored)")

    if topic is not None:
        if "knowledge_item_id" in available:
            clauses.append(
                "knowledge_item_id IN (SELECT it.knowledge_item_id FROM item_term it "
                "JOIN taxonomy_term tt ON tt.id = it.term_id "
                "WHERE tt.taxonomy_id = 'topic' AND (tt.id = %s OR tt.code = %s OR tt.label ILIKE %s))"
            )
            params.extend([topic, topic, f"%{topic}%"])
        else:
            missing.append("knowledge_item_id (topic filter ignored)")

    if not include_placeholder:
        if "content_status" in available:
            clauses.append(_content_status_exclude_clause("content_status", params))
        else:
            missing.append("content_status (placeholder filter unavailable -- results NOT guaranteed real)")

    order = ", ".join(c for c in ("criterion_path", "level_ordinal") if c in available) or "1"
    sql = (f"SELECT {', '.join(columns)} FROM v_requirement_matrix WHERE "
           + " AND ".join(clauses) + f" ORDER BY {order} NULLS LAST LIMIT %s")
    params.append(_clamp(limit, default=50, maximum=200))

    rows = db.all_rows(conn, sql, params)
    for r in rows:
        if r.get("knowledge_item_id") is not None:
            r["knowledge_item_id"] = str(r["knowledge_item_id"])
        if "content_status" in r:
            r["is_placeholder_content"] = r["content_status"] in PLACEHOLDER_STATUSES
    payload, truncated = _truncate_strings(rows)
    out = {"results": payload, "truncated": truncated, "placeholder_included": include_placeholder}
    if missing:
        out["missing_columns"] = missing
    return out


def list_templates(conn, *, limit: int = 100, include_placeholder: bool = False) -> dict:
    """Calculators/checklists/matrices from v_template_catalogue.

    These are xlsx workbooks, not paginated PDFs: `citation.page_index` is
    never populated for them (there is no page to point at) so the "cite a
    page" rule doesn't literally apply. Instead this citation is
    document_slug (always present, NOT NULL, and 1:1 with the file) plus the
    sheet names its parameters live on, pulled from template_parameter --
    the closest thing this shape of document has to a page reference. If a
    row somehow lacks even a document_slug it is dropped, same as elsewhere.
    """
    clauses = ["t.document_slug IS NOT NULL"]
    params: list[Any] = []
    if not include_placeholder:
        clauses.append(_content_status_exclude_clause("ki.content_status", params))

    sql = f"""SELECT t.knowledge_item_id, t.slug, t.template_kind, t.engine,
                     t.title, t.document_slug, t.input_count, t.output_count,
                     ki.content_status
              FROM v_template_catalogue t
              JOIN knowledge_item ki ON ki.id = t.knowledge_item_id
              WHERE {' AND '.join(clauses)}
              ORDER BY t.document_slug, t.slug
              LIMIT %s"""
    params.append(_clamp(limit, default=100, maximum=300))
    rows = db.all_rows(conn, sql, params)
    for r in rows:
        r["knowledge_item_id"] = str(r["knowledge_item_id"])
        r["is_placeholder_content"] = r["content_status"] in PLACEHOLDER_STATUSES
        sheets = db.all_rows(
            conn,
            "SELECT DISTINCT sheet_name FROM template_parameter "
            "WHERE template_id = %s AND sheet_name IS NOT NULL ORDER BY sheet_name",
            (r["knowledge_item_id"],),
        )
        r["citation"] = {"document_slug": r["document_slug"],
                          "sheets": [s["sheet_name"] for s in sheets]}
    return {"results": rows, "placeholder_included": include_placeholder}


def get_citation(conn, item_id: str) -> dict:
    """Every citation recorded for one knowledge_item: document slug, PDF
    page index, printed page label, bbox. Always labels placeholder content
    rather than excluding it -- the caller already has a specific item_id in
    hand, most likely from search_knowledge, and needs to know what it is."""
    rows = db.all_rows(
        conn,
        """SELECT c.id AS citation_id, c.page_index, c.printed_page_label, c.bbox,
                  d.slug AS document_slug, d.title AS document_title,
                  ki.item_type, ki.title AS item_title, ki.content_status, ki.review_status
           FROM citation c
           JOIN source_document d ON d.id = c.document_id
           LEFT JOIN knowledge_item ki ON ki.id = c.knowledge_item_id
           WHERE c.knowledge_item_id = %s
           ORDER BY c.page_index NULLS LAST""",
        (item_id,),
    )
    if not rows:
        return {"item_id": item_id, "citations": [], "error": "no citation found for this item_id"}
    for r in rows:
        r["citation_id"] = str(r["citation_id"])
        r["is_placeholder"] = r["content_status"] in PLACEHOLDER_STATUSES
    return {"item_id": item_id, "citations": rows}


def get_document(conn, slug: str) -> dict:
    """Document metadata plus its doc_node outline. Includes a content_status
    breakdown of the document's knowledge items so a caller can see, e.g.,
    that a chunk of a document is lorem/WIP before trusting anything pulled
    from it -- exactly the typology-multifamily situation this corpus has."""
    doc = db.one(
        conn,
        """SELECT id, slug, title, doc_kind, series_ref, revision, version_label,
                  issue_date, confidentiality, content_status, language,
                  page_count, is_spread_paginated
           FROM source_document WHERE slug = %s AND is_current""",
        (slug,),
    )
    if doc is None:
        return {"slug": slug, "error": "no current document with this slug"}
    doc["id"] = str(doc["id"])

    nodes = db.all_rows(
        conn,
        """SELECT id, parent_id, node_kind, code, title, title_alt, ordinal,
                  page_from, page_to
           FROM doc_node WHERE document_id = %s ORDER BY ordinal""",
        (doc["id"],),
    )
    for n in nodes:
        n["id"] = str(n["id"])
        n["parent_id"] = str(n["parent_id"]) if n["parent_id"] else None

    breakdown = db.all_rows(
        conn,
        """SELECT content_status, count(*) AS n FROM knowledge_item
           WHERE document_id = %s GROUP BY content_status ORDER BY n DESC""",
        (doc["id"],),
    )
    placeholder_items = sum(r["n"] for r in breakdown if r["content_status"] in PLACEHOLDER_STATUSES)
    total_items = sum(r["n"] for r in breakdown)

    return {
        "document": doc,
        "outline": nodes,
        "content_status_breakdown": {r["content_status"]: r["n"] for r in breakdown},
        "placeholder_item_fraction": round(placeholder_items / total_items, 3) if total_items else None,
    }


# ─────────────────────────────────────────────────────────────────────────
# MCP wiring -- one connection per call, arch_read only.
# ─────────────────────────────────────────────────────────────────────────

def _build_server():
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        "arch-ive-retrieval",
        instructions=(
            "Read-only retrieval over the arch-ive architecture knowledge base. "
            "Every result carries a document slug and page citation; rows with "
            "no page are dropped rather than returned uncited. Placeholder/"
            "draft/wip/lorem content is excluded by default -- pass "
            "include_placeholder=true to see it, always labelled."
        ),
    )

    @server.tool(name="search_knowledge")
    def search_knowledge_tool(query: str, facets: dict[str, str] | None = None,
                               limit: int = 20, include_placeholder: bool = False) -> dict:
        """Full-text + vector search over the corpus. facets maps a label to a
        taxonomy_term id (ANDed). Every result carries a document_slug + page."""
        with read_connect() as conn:
            return search_knowledge(conn, query, facets=facets, limit=limit,
                                     include_placeholder=include_placeholder)

    @server.tool(name="get_benchmark")
    def get_benchmark_tool(metric: str | None = None, building_use: str | None = None,
                            target_year: int | None = None, limit: int = 50,
                            include_placeholder: bool = False) -> dict:
        """Numeric/text benchmarks (e.g. embodied carbon targets), optionally
        filtered by metric id, building use id and target year."""
        with read_connect() as conn:
            return get_benchmark(conn, metric=metric, building_use=building_use,
                                  target_year=target_year, limit=limit,
                                  include_placeholder=include_placeholder)

    @server.tool(name="get_requirement_matrix")
    def get_requirement_matrix_tool(topic: str | None = None, level: str | None = None,
                                     framework: str | None = None, limit: int = 50,
                                     include_placeholder: bool = False) -> dict:
        """Requirement rows (criterion x rating level), optionally filtered by
        topic, rating level and framework slug."""
        with read_connect() as conn:
            return get_requirement_matrix(conn, topic=topic, level=level, framework=framework,
                                           limit=limit, include_placeholder=include_placeholder)

    @server.tool(name="list_templates")
    def list_templates_tool(limit: int = 100, include_placeholder: bool = False) -> dict:
        """Calculators/checklists/matrices in the corpus, each with input and
        output parameter counts."""
        with read_connect() as conn:
            return list_templates(conn, limit=limit, include_placeholder=include_placeholder)

    @server.tool(name="get_citation")
    def get_citation_tool(item_id: str) -> dict:
        """All citations recorded for one knowledge_item id: document slug,
        PDF page index, printed page label, bbox."""
        with read_connect() as conn:
            return get_citation(conn, item_id)

    @server.tool(name="get_document")
    def get_document_tool(slug: str) -> dict:
        """Document metadata plus its doc_node outline and a content_status
        breakdown of its knowledge items."""
        with read_connect() as conn:
            return get_document(conn, slug)

    return server


def main() -> None:
    server = _build_server()
    server.run()  # stdio by default


if __name__ == "__main__":
    main()
