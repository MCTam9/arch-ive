# SOP: local MCP retrieval

## Objective

Let Claude (or any agent running locally) answer questions against the
ingested corpus without ever holding write credentials, and without any
public endpoint. Retrieval runs entirely over a local stdio MCP server
(`tools/mcp_server.py`) on a read-only Postgres role (`arch_read`).

## What it exposes

Six tools, every one of them returning a document slug + page citation on
every row (a row with neither is dropped before it's returned):

| Tool | Backed by | Notes |
|---|---|---|
| `search_knowledge(query, facets?, limit?, include_placeholder?)` | `v_retrievable_chunk` + hybrid rank (one call into `tools/search.py`) | full-text always, vector cosine fused in when embeddings exist |
| `get_benchmark(metric?, building_use?, target_year?, limit?, include_placeholder?)` | `v_benchmark` | |
| `get_requirement_matrix(topic?, level?, framework?, limit?, include_placeholder?)` | `v_requirement_matrix` | column-defensive: selects a named allowlist, degrades rather than raising |
| `list_templates(limit?, include_placeholder?)` | `v_template_catalogue` | calculators are xlsx, not paginated PDFs -- citation here is `{document_slug, sheets}` |
| `get_citation(item_id)` | `citation` | every citation row for one `knowledge_item_id` |
| `get_document(slug)` | `source_document` + `doc_node` | outline, plus a `content_status` breakdown of the document's knowledge items |

**Placeholder content is excluded by default.** `content_status` of `lorem`,
`template`, `wip` or `draft` never comes back unless you pass
`include_placeholder=true`, and when it does every row is labelled
(`is_placeholder` / `is_placeholder_content`). This exists because one
document in this corpus is ~24% lorem/WIP filler -- an agent that can't tell
the difference will cite fake numbers as real ones.

**Where that rule lives.** In `db/schema.sql`, not here.
`is_placeholder_status()` holds the four status values and
`v_retrievable_chunk` exposes `is_placeholder` and `has_citation` as columns,
so this server, `tools/search.py` and the web app read one rule rather than
keeping three copies of it in step. `search_knowledge` is now a single call
into `tools.search.search` with `include_placeholder` passed through; the
separate hand-written query that used to serve the `include_placeholder=true`
case is gone, having quietly drifted from the default path it was supposed to
mirror. The same views also drop `review_status = 'rejected'` — a human
rejected that extraction, so it should not come back as an answer — while
`get_citation` still returns rejected and placeholder rows, labelled, because
the caller already has a specific `item_id` in hand.

## Required inputs

- The live (or test) Postgres from `CONTRACT.md`, schema already applied.
- The `arch_read` role, applied once per database:
  ```sh
  docker exec -e PGPASSWORD=dev archive-dev psql -U postgres -d postgres  -f db/roles.sql
  docker exec -e PGPASSWORD=dev archive-dev psql -U postgres -d arch_test -f db/roles.sql
  ```
  (roles are cluster-wide but grants are per-database -- rerun against any
  new database, e.g. after a schema reset, or if another agent's DDL adds a
  table this role needs `SELECT` on beyond what `ALTER DEFAULT PRIVILEGES`
  already covers for future tables owned by `postgres`.)
- `mcp` installed in the project venv: `./.venv/bin/pip install mcp`.
- Environment (add to `.env`, never commit real values):
  ```
  DATABASE_URL_READONLY=postgresql://arch_read:dev@localhost:55432/postgres
  ARCHIVE_ACCOUNT_ID=00000000-0000-0000-0000-0000000000aa
  ```

## Registering with Claude Code

```sh
claude mcp add arch-ive-retrieval \
  --env DATABASE_URL_READONLY=postgresql://arch_read:dev@localhost:55432/postgres \
  --env ARCHIVE_ACCOUNT_ID=00000000-0000-0000-0000-0000000000aa \
  -- ./.venv/bin/python tools/mcp_server.py
```

Run that from the repo root so the relative venv path resolves. Verify with
`claude mcp list` / `/mcp` inside a session. There is no `claude mcp add
--transport http` step here on purpose -- this server only speaks stdio, and
is only ever launched as a local subprocess of the Claude Code process that
registered it. It is not reachable over the network.

## How to verify it's working

```sh
# from the repo root
./.venv/bin/python -m compileall tools/mcp_server.py
./.venv/bin/python -m pytest tests/test_mcp_server.py -q
```

The test file spins up fixture rows under document slug `h-mcp-fixture`
(idempotent -- safe to rerun) and calls every tool function directly against
the test database's `arch_read` role, including one placeholder-content row
to prove the default-exclude / explicit-include-and-label behaviour.

## Edge cases learned from the corpus

- **xlsx calculators have no PDF page.** `citation.page_index` is never
  populated for `calc-*` documents -- there is no page to point at. Rather
  than fail the "every result carries a citation" rule outright,
  `list_templates` treats `document_slug` (always present, 1:1 with the
  file) plus the sheet names from `template_parameter` as the citation for
  that shape of document.
- **Placeholder pages did reach `chunk` -- as figures.** This section used to
  say they did not, and for page text and knowledge items that was true: the
  ~24% lorem/WIP pages of `typology-multifamily` are flagged at
  `source_page.content_status` and produced no page chunks and no items. But
  `tools/crop_figures.py` and `tools/describe_figures.py` cropped and described
  the figures on those pages regardless, and the resulting chunks carry
  `chunk.content_status = 'real'` because nothing propagated the page's flag
  down to them. 141 chunks -- 139 on `typology-multifamily`, 2 on
  `framework-vol-e2` -- were served as fact by both this server and the web
  app.

  `v_retrievable_chunk` now folds `source_page.content_status` into
  `is_placeholder`, so a chunk inherits the status of the page it came from.
  The default MCP surface dropped from 2,827 chunks to 2,686 the day that
  landed. Those 141 come back with `include_placeholder=true`, labelled; the
  web app shows them stamped rather than hiding them, which is the difference
  between the two surfaces and is deliberate. `CONTRACT.md` asks for
  placeholder content to be flagged, not ingested as fact — the flag existed,
  and only the text path was reading it.
- **`get_requirement_matrix` never does `SELECT *`.** It selects a fixed,
  named column allowlist, intersected at query time with
  `information_schema.columns`, and reports anything missing under
  `missing_columns` instead of raising. If `document_slug` or `page_index`
  themselves go missing, it refuses to return uncited rows rather than guess.

  This was written while the view was being changed under it, and the scope
  dimension it was bracing for landed as the `requirement_scope` /
  `requirement_scope_applicability` tables and `v_requirement_scope_matrix`
  rather than as columns here. The defensiveness is kept anyway: it costs one
  `information_schema` read per call and it is the reason a view edit degrades
  this tool instead of breaking it.
