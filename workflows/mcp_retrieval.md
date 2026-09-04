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
| `search_knowledge(query, facets?, limit?, include_placeholder?)` | `chunk.tsv` + hybrid rank (reuses `tools/search.py`) | full-text always, vector cosine fused in when embeddings exist |
| `get_benchmark(metric?, building_use?, target_year?, limit?, include_placeholder?)` | `v_benchmark` | |
| `get_requirement_matrix(topic?, level?, framework?, limit?, include_placeholder?)` | `v_requirement_matrix` | column-defensive: another agent is adding a scope dimension to this view concurrently |
| `list_templates(limit?, include_placeholder?)` | `v_template_catalogue` | calculators are xlsx, not paginated PDFs -- citation here is `{document_slug, sheets}` |
| `get_citation(item_id)` | `citation` | every citation row for one `knowledge_item_id` |
| `get_document(slug)` | `source_document` + `doc_node` | outline, plus a `content_status` breakdown of the document's knowledge items |

**Placeholder content is excluded by default.** `content_status` of `lorem`,
`template`, `wip` or `draft` never comes back unless you pass
`include_placeholder=true`, and when it does every row is labelled
(`is_placeholder` / `is_placeholder_content`). This exists because one
document in this corpus is ~24% lorem/WIP filler -- an agent that can't tell
the difference will cite fake numbers as real ones.

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
- **Placeholder pages don't reach `chunk`/`knowledge_item` at all, in the
  corpus as ingested today.** For `typology-multifamily`, the ~24%
  lorem/WIP pages are flagged at `source_page.content_status`, and the
  extraction pipeline simply never produced chunks or knowledge items citing
  those pages -- so the `content_status` filtering in this server is a
  backstop for content that *is* flagged at the chunk/item level (which the
  schema and `CONTRACT.md` both anticipate), not the only thing standing
  between an agent and that document's placeholder text today. If a future
  ingest run ever does propagate a page-level flag down to a chunk or
  knowledge item, this server already excludes it by default.
- **`v_requirement_matrix` is being changed concurrently.** `get_requirement_matrix`
  never does `SELECT *` against it -- it selects a fixed, named column
  allowlist, intersected at query time with `information_schema.columns`,
  and reports anything missing under `missing_columns` in the response
  instead of raising. If `document_slug` or `page_index` themselves go
  missing, the tool refuses to return uncited rows rather than guess.
