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

`tools/pipeline.py` — the contract:

- `State` — the ingest state machine, in order.
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
