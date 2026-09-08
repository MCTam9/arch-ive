# Build contract

Read this before writing code. It is the interface every part of the pipeline
shares; deviating from it breaks somebody else's module.

## Ground rules

- **This repo is public. The corpus is not.** Never write a real company,
  client, consultant or person name into any file, filename or commit message.
  Use document slugs (`crib-water`, `framework-vol-e1`, `typology-multifamily`,
  `calc-fees`) and organisation ids (`org-consult-engineering`). A pre-commit
  hook and CI will reject you otherwise. Real names live only in
  `private/documents.yaml` and the `organisation` table.
- Python 3.12+, standard library plus `requirements.txt`. Run everything with
  `./.venv/bin/python`.
- Type hints on public functions. No framework, no ORM: `psycopg` and SQL.
- Comments explain *why*, not *what*. No banner comments, no docstring padding.
- Never print a real filename to a log line that could end up in the repo.

## Environment

A live Postgres with the schema applied is already running:

```
DATABASE_URL=postgresql://arch_app:dev@localhost:55432/postgres
ARCHIVE_ACCOUNT_ID=00000000-0000-0000-0000-0000000000aa
```

Row-level security is ON and FORCED. `tools/db.py` sets `app.account_id` on
every connection; without it you will see zero rows and think the table is
empty. Always go through `tools.db`.

Reset the database at any time:
```sh
psql "postgresql://postgres:dev@localhost:55432/postgres" -c 'drop schema public cascade; create schema public;'
psql "postgresql://postgres:dev@localhost:55432/postgres" -f db/schema.sql
```
(then re-grant: see `db/test_schema.sh` for the role and seed account SQL)

**Changing an existing database needs a migration, not just a schema edit.**
`db/schema.sql` is the current shape, applied whole to a fresh database; it is
not a history. A change to a database that already exists goes in
`db/migrate/<YYYY-MM-DD>_<what>.sql` **and** into `db/schema.sql` in the same
commit, and gets applied to local dev, `arch_test` and Neon in the same sitting.
The rules for writing one — including what `CREATE OR REPLACE VIEW` can and
cannot change, and why a `DROP` needs a re-`GRANT` — are in
`workflows/provision_database.md`.

## The source corpus

14 files, currently in `Excel/`, `PDF/`, `Report - Guidance/`, `Table - PDF/`.
`private/documents.yaml` maps each to its slug and metadata. Nothing in those
folders is tracked by git.

| slug | shape | notes |
|---|---|---|
| `crib-*` (6) | 2-page A3 landscape grid, 1190×842 pt | page 1 reference, page 2 a level matrix |
| `framework-vol-e1` / `-e2` / `-a10-smart-city` | compliance framework | **spread-paginated**: 1 PDF page = 2 printed pages |
| `typology-multifamily` | 413pp catalogue, 261-entry TOC | ~24% lorem/WIP — must be flagged, not ingested as fact |
| `deck-early-stage-design` | 38-slide PowerPoint export | 26% image-only |
| `calc-*` (3) | xlsx calculators | formulas matter more than values |

## Interfaces

`tools/db.py` — `connect()`, `transaction()`, `one()`, `all_rows()`, `scalar()`,
`insert_returning_id()`. One stage, one transaction.

`db/schema.sql` — **the retrieval policy is a cross-module interface, and it is
SQL.** Three adapters read the corpus — `tools/search.py`, `tools/mcp_server.py`
and `web/lib/queries.ts`, in two languages — and the rules deciding what a
reader may be shown live in one place so they cannot drift apart. They did
drift: the taxonomy subtree rule was fixed twice, once per language, in two
separate commits.

| Object | Holds |
|---|---|
| `is_placeholder_status(content_status)` | the four placeholder statuses, once |
| `v_item_term_subtree` | the only place ltree `<@` is written |
| `item_has_term(item_id, term_id)` | the facet predicate. Returns **false, not NULL**, for a NULL item — a page or figure chunk has none, and callers count what they suppress |
| `v_term_item_count` | facet counts that cannot disagree with the filter |
| `v_retrievable_chunk` | the search surface. Rejected items and sub-floor page text are excluded in its `WHERE`; `is_placeholder` and `has_citation` are columns a caller opts into |
| `v_retrievable_item` | the browse surface, same column names, one citation per item |

Two rules are unconditional and already applied: `review_status = 'rejected'`
never appears (a human rejected that extraction), and a chunk **inherits its
page's `content_status`** — figures cropped from a WIP page are not real
content, which is what `CONTRACT`'s "flagged, never silently ingested" means
one layer down.

Placeholder visibility is where the two surfaces deliberately differ: MCP
excludes it by default, the web app shows it labelled. That is a parameter,
not an accident. Do not add a policy clause to an adapter — add it to the view
and let all three inherit it.

`tools/pipeline.py` — the contract:

- `State` — the ingest state machine, in order.
- `STAGES` — the ordered sequence, and the only place it exists. Each entry
  pairs a `State` with the function that runs it; `tools/ingest_inbox.py` binds
  those with `@runs_stage` at import and iterates the list. `stage_sequence()`
  raises if any entry is unbound, and `runs_stage` raises on a state not in the
  list, so neither a typo nor a missing import can silently drop a stage. A
  stage returns `None` to continue or a `Halt` to end the job early, and only a
  stage declared `ends_run` may return one.
- `ENRICHMENTS` — the ordered steps inside `State.ENRICHED`, all normalised to
  `run(conn, *, document_id, slug)`. They share one stage and one transaction
  because `ingest_state` has a single name for them; splitting one out means
  adding an enum value first.
- `run_stage(job_id, stage, fn)` — runs `fn(conn)` once. Idempotency is enforced
  by `UNIQUE (job_id, stage)` on `ingest_stage_run`. Returns True if the stage
  succeeded or had already succeeded. Raise `StageSkipped` for "nothing to do".
- `@register` + `for_doc_kind(kind)` — the extractor registry. `for_doc_kind`
  always returns something; an unrecognised shape falls back to `unknown`.
- `DocumentContext` — what an extractor is given (read-only).
- `Extraction` — what an extractor returns.

**Extractors are pure.** No database, no network, no writes outside `.tmp/`.
They take a `DocumentContext` and return an `Extraction`. The caller writes it.
This is what makes them testable and keeps a failed write from leaving half a
document behind.

Cross-record links use `ref`: set `Node.ref = "sec-3"` and point at it with
`Item.node_ref = "sec-3"`. The writer resolves refs to real ids.

`Item.payload` holds the subtype table's columns exactly as named in
`db/schema.sql`, minus `knowledge_item_id`. For `item_type="benchmark"` that is
`metric_id`, `value_numeric`, `value_text`, `unit_id`, `comparator`,
`is_placeholder`, `caveat_text`, `building_use_id`, `target_year`, ...

## Extraction rules that came out of reading the corpus

- **Keep the verbatim string.** `benchmark.value_text` and
  `requirement.target_text` are NOT NULL. Parse into `value_numeric` when you
  can, set `is_placeholder` for `X%` / `Xkm` / `X no of`, use
  `value_min`/`value_max` for ranges like `700-800ppm`, and put asterisk
  footnotes in `caveat_text`. Never drop what you could not parse.
- **A matrix cell holds 0..n statements, not one string.** Many stack two or
  three; many intersections are empty.
- **Sections get renamed between page 1 and page 2** of the same sheet. Store
  both as `title` and `title_alt`, and key on `code`.
- **Placeholder content must be flagged, never silently ingested.** Detect
  lorem-ipsum, `TEMPLATE ONLY` and `WIP` stamps and set
  `content_status` accordingly.
- **Spread pagination**: record both the PDF `page_index` and the
  `printed_page_label` the page shows for itself.
- Column bands in the grids are recovered from drawing rects, per file. Five of
  the six sheets share a geometry; `crib-climate-resilience` does not, and has
  no sub-criteria column. Do not hardcode one geometry.

## Definition of done

- `./.venv/bin/python -m compileall <your files>` clean.
- Your module runs against the live DB and does what it claims.
- A short test under `tests/` that a fresh clone can run.
- No forbidden names anywhere. Check with:
  `python3 scripts/scan_forbidden.py --paths <your files>`

## Cross-module signatures — code to these exactly

Different people own these files. These signatures are the seam between them;
do not change one without changing this document.

```python
# tools/classify_document.py
def classify(path: Path) -> tuple[str, float]:
    """(doc_kind, confidence 0-1) from cheap signals; never raises."""

# tools/ingest_document.py
def register_document(conn, *, path: Path, sha256: str, slug: str,
                      doc_kind: str, meta: dict) -> str:
    """Upsert source_document (+ spreadsheet_sheet/cell for xlsx). Returns document_id."""

def extract_pages(conn, document_id: str, path: Path) -> int:
    """Fill source_page + source_asset, render page images. Returns page count."""

# tools/build_structure.py
def build_structure(conn, document_id: str, path: Path) -> int:
    """Fill doc_node from bookmarks or heading detection. Returns node count."""

# tools/write_extraction.py
def write_extraction(conn, document_id: str, extraction: Extraction) -> dict:
    """Persist an Extraction: resolves refs, upserts lookups, writes items,
    citations, chunks and external_references. Returns counts. Idempotent per
    document: it clears that document's prior knowledge_items first."""

# tools/archive_original.py
def archive(path: Path, sha256: str, slug: str) -> str | None:
    """File to SOURCE_DIR and push encrypted to R2. Returns r2_key, or None if
    R2 is unconfigured (local filing still happens — never block ingest on it)."""

# tools/embed_chunks.py
def embed_pending(conn, document_id: str | None = None) -> int:
    """Embed chunks whose embedding IS NULL. Returns count."""
```

Extractor modules end with:
```python
CRIB_SHEET = CribSheetExtractor()
pipeline.register(CRIB_SHEET)
```
with `doc_kinds: tuple[str, ...]` on the class.

`extractors/support.py` holds the rules above as code — `parse_value`,
`is_placeholder_value`, `page_is_real`, `slugify`, `clean` — so a shape does not
get its own dialect of them. It is pure, registers nothing, and must stay
import-safe: `pipeline.load_extractors()` imports every module in the package on
every run. Reach for it before writing a second parser; the eight sites that
hardcoded `is_placeholder: False` are what its absence cost.
`workflows/add_extractor.md` lists it, because `scripts/check_wat.py` requires
every file in `extractors/` to appear in that registry.

## Testing

Tests run against a **separate** database, created once:

```sh
psql "$ADMIN_URL" -c 'CREATE DATABASE arch_test'
psql "$ADMIN_URL/arch_test" -f db/schema.sql
psql "$ADMIN_URL/arch_test" -f db/seed.sql     # needs SET app.account_id first
./.venv/bin/python -m pytest tests/ -q
```

`tests/conftest.py` redirects `DATABASE_URL` before any test imports a tool.
This is not optional hygiene: test fixtures create and delete documents using
the same slugs the corpus uses, so sharing one database means a test run
silently deletes real rows. That happened twice before conftest.py existed.

## MCP surface

`tools/mcp_server.py` is a local stdio MCP server for Claude/agent retrieval.
It connects as `arch_read` (see `db/roles.sql`) on `DATABASE_URL_READONLY`,
never as `arch_app`, and never over a public endpoint. Every tool function
also exists as a plain `(conn, ...)` function of the same name so it can be
called directly in tests without going over the MCP protocol.

Two rules apply across all of them: every result carries a document slug +
page citation (a row with neither is dropped, not returned with a null
citation), and `content_status` in `{lorem, template, wip, draft}` is
excluded by default -- pass `include_placeholder=True` to see it anyway,
always labelled `is_placeholder`/`is_placeholder_content` on the row. Both are
enforced by the views above rather than restated here; `get_citation` is the
documented exception, returning rejected and placeholder rows labelled because
the caller already holds a specific `item_id`.

```python
# tools/mcp_server.py
def search_knowledge(conn, query: str, *, facets: dict[str, str] | None = None,
                      limit: int = 20, include_placeholder: bool = False) -> dict:
    """Hybrid full-text + vector search over v_retrievable_chunk, via
    tools.search.search -- one call, both paths. Each result carries a
    `citation` dict with document_slug + page_from/page_to."""

def get_benchmark(conn, *, metric: str | None = None, building_use: str | None = None,
                   target_year: int | None = None, limit: int = 50,
                   include_placeholder: bool = False) -> dict:
    """Rows from v_benchmark, each carrying document_slug + page_index."""

def get_requirement_matrix(conn, *, topic: str | None = None, level: str | None = None,
                            framework: str | None = None, limit: int = 50,
                            include_placeholder: bool = False) -> dict:
    """Rows from v_requirement_matrix. Selects a fixed column allowlist,
    intersected at query time with information_schema -- a column this view
    loses (it's under active change elsewhere) degrades the response instead
    of raising, and is reported back under `missing_columns`."""

def list_templates(conn, *, limit: int = 100, include_placeholder: bool = False) -> dict:
    """Rows from v_template_catalogue. These are xlsx workbooks with no PDF
    page to cite, so `citation` here is {document_slug, sheets: [...]} from
    template_parameter instead of a page index."""

def get_citation(conn, item_id: str) -> dict:
    """Every citation row for one knowledge_item id: document slug, PDF
    page_index, printed_page_label, bbox. Not filtered by content_status --
    the caller already has a specific item_id in hand -- but every row is
    labelled `is_placeholder`."""

def get_document(conn, slug: str) -> dict:
    """source_document metadata + its doc_node outline, plus a
    content_status breakdown of the document's knowledge items (so, e.g., a
    24%-lorem document shows that fraction rather than hiding it)."""
```

`db/roles.sql` creates `arch_read`: `LOGIN`, `NOINHERIT`, `CONNECT` on the
database, `USAGE` on schema public, `SELECT` only (tables and views, via
`ALTER DEFAULT PRIVILEGES` for future ones too) -- no write grants anywhere.
RLS (`ENABLE`+`FORCE` in `db/schema.sql`) still applies to it like any
non-owner role, so an unset `app.account_id` sees zero rows same as
`arch_app` does. Apply it once per database (roles are cluster-wide, grants
are not):
```sh
docker exec -e PGPASSWORD=dev archive-dev psql -U postgres -d postgres  -f db/roles.sql
docker exec -e PGPASSWORD=dev archive-dev psql -U postgres -d arch_test -f db/roles.sql
```
