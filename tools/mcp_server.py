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

     A chunk inherits that status from its page, so a figure description on a
     page marked 'wip' is placeholder content even though the description row
     itself says 'real'. 141 such chunks were served as fact by both this
     server and the web before db/schema.sql took the rule over.

Neither rule is written out here any more. Both live in db/schema.sql --
`is_placeholder_status()`, `v_retrievable_chunk`, `v_retrievable_item`, and an
`is_placeholder_content` column on the four reporting views -- because
web/lib/queries.ts has to obey the same rules and cannot import Python.

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
from tools.search import search as _hybrid_search  # noqa: E402

READONLY_DEFAULT_DSN = "postgresql://arch_read:dev@localhost:55432/postgres"
DEFAULT_ACCOUNT = "00000000-0000-0000-0000-0000000000aa"

# The set of content_status values that must never be served as fact by
# default used to be a tuple here, pasted into a NOT IN clause by hand. It is
# now is_placeholder_status() in db/schema.sql, and every view that matters
# exposes the answer as a column: `is_placeholder` on v_retrievable_chunk,
# `is_placeholder_content` on v_benchmark / v_requirement_matrix /
# v_requirement_scope_matrix / v_template_catalogue. web/lib/queries.ts reads
# the same ones, which is the point -- the Python copy and the TypeScript copy
# had already drifted apart once.

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
    """Full-text/vector hybrid search over v_retrievable_chunk, via
    tools.search.search. `facets` maps an arbitrary label to a taxonomy_term
    id, ANDed together -- same contract as tools/search.py's CLI.

    One query for both paths. `include_placeholder` used to fork into a
    second, hand-written copy of the search here, on the argument that
    tools.search.search's real-only default must have no flag that could
    weaken it by accident. The copy is what went wrong instead: it had already
    drifted -- it dropped the `content_status = 'real'` check on the chunk
    itself and gated on page_from/page_to where the default path gated on
    nothing -- so the escape hatch was quietly a different search, not a wider
    one. A keyword-only argument over a view that states the rule is the safer
    shape: there is one query, the default is still the safe one, and the rule
    it relaxes is written in db/schema.sql where the web reads it too.

    Both the rejected-item exclusion and the citation rule are now
    unconditional here, on both paths."""
    limit = _clamp(limit, default=20, maximum=50)

    results = _hybrid_search(conn, query, facets=facets, limit=limit,
                              include_placeholder=include_placeholder,
                              require_citation=True)
    for r in results:
        # The MCP row key predates the view column and stays as it is: this
        # is a published tool response shape, not an internal name.
        r["is_placeholder"] = bool(r.get("is_placeholder"))

    payload, truncated = _truncate_strings(results)
    out = {"query": query, "results": payload, "truncated": truncated,
           "placeholder_included": include_placeholder}
    if include_placeholder:
        out["warning"] = ("placeholder/draft/wip/template content included on request; "
                          "check is_placeholder on each row before treating it as fact")
    return out


def get_benchmark(conn, *, metric: str | None = None, building_use: str | None = None,
                   target_year: int | None = None, limit: int = 50,
                   include_placeholder: bool = False) -> dict:
    """Benchmarks from v_benchmark, optionally narrowed by metric, building
    use and target year."""
    limit = _clamp(limit, default=50, maximum=200)
    clauses: list[str] = ["has_citation"]  # no citation, no result
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
        clauses.append("NOT is_placeholder_content")

    # is_placeholder_content, not is_placeholder: v_benchmark already carries
    # benchmark.is_placeholder, which says the *value* was printed as 'X%' in
    # the source. Two different claims, two different columns.
    sql = ("SELECT * FROM v_benchmark WHERE " + " AND ".join(clauses) +
           " ORDER BY target_year NULLS LAST, value_numeric NULLS LAST LIMIT %s")
    params.append(limit)
    rows = db.all_rows(conn, sql, params)
    for r in rows:
        r["knowledge_item_id"] = str(r["knowledge_item_id"])
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
    "review_status", "document_slug", "page_index", "is_placeholder_content",
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
            # `topic` here is a fuzzy human handle (id, code or label
            # substring), resolved to term ids first; item_has_term then does
            # the subtree matching, so a parent topic pulls its children in.
            clauses.append(
                "EXISTS (SELECT 1 FROM taxonomy_term tt "
                "WHERE tt.taxonomy_id = 'topic' "
                "AND (tt.id = %s OR tt.code = %s OR tt.label ILIKE %s) "
                "AND item_has_term(knowledge_item_id, tt.id))"
            )
            params.extend([topic, topic, f"%{topic}%"])
        else:
            missing.append("knowledge_item_id (topic filter ignored)")

    if not include_placeholder:
        # The view states the rule; this only decides whether to apply it.
        if "is_placeholder_content" in available:
            clauses.append("NOT is_placeholder_content")
        elif "content_status" in available:
            clauses.append("NOT is_placeholder_status(content_status)")
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
        # is_placeholder_content comes off the view when the view has it. When
        # it does not, the row simply goes out unlabelled and the reason is
        # already in missing_columns -- re-deriving the rule in Python here is
        # what let the two halves of this file disagree in the first place.
        if "is_placeholder_content" in r and r["is_placeholder_content"] is None:
            r["is_placeholder_content"] = False
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
        clauses.append("NOT t.is_placeholder_content")

    # No join back to knowledge_item: the view carries content_status and the
    # placeholder verdict now. It carries no has_citation and never will --
    # these are xlsx workbooks with no page, so a column that is false for
    # every row would only invite a caller to filter the catalogue away.
    sql = f"""SELECT t.knowledge_item_id, t.slug, t.template_kind, t.engine,
                     t.title, t.document_slug, t.input_count, t.output_count,
                     t.content_status, t.is_placeholder_content
              FROM v_template_catalogue t
              WHERE {' AND '.join(clauses)}
              ORDER BY t.document_slug, t.slug
              LIMIT %s"""
    params.append(_clamp(limit, default=100, maximum=300))
    rows = db.all_rows(conn, sql, params)
    for r in rows:
        r["knowledge_item_id"] = str(r["knowledge_item_id"])
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
    hand, most likely from search_knowledge, and needs to know what it is.

    Reads the base tables on purpose, not v_retrievable_item: a rejected item
    still has citations, and someone holding its id is entitled to see where
    it came from. Filtering here would make a review impossible."""
    rows = db.all_rows(
        conn,
        """SELECT c.id AS citation_id, c.page_index, c.printed_page_label, c.bbox,
                  d.slug AS document_slug, d.title AS document_title,
                  ki.item_type, ki.title AS item_title, ki.content_status, ki.review_status,
                  is_placeholder_status(ki.content_status) AS is_placeholder
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
        """SELECT content_status, is_placeholder_status(content_status) AS is_placeholder,
                  count(*) AS n
           FROM knowledge_item
           WHERE document_id = %s
           GROUP BY content_status ORDER BY n DESC""",
        (doc["id"],),
    )
    placeholder_items = sum(r["n"] for r in breakdown if r["is_placeholder"])
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
