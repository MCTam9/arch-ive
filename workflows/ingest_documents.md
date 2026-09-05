# Workflow: ingest documents

**Objective.** Get a new reference document from a file on disk into the
knowledge base, fully cited and correctly flagged, without ever putting the
file or its client's name into git.

## Inputs

- One or more files (`.pdf`, `.xlsx`) dropped into `inbox/`.
- Optional `<name>.meta.yaml` sidecar next to a file, supplying `slug`,
  `doc_kind`, `client_org`, `version_label`, `confidentiality`. **This sidecar
  is the sanctioned channel for the names that must never reach the repo** —
  it is gitignored, and nothing it contains is written to a tracked file.
- Optional `inbox/_defaults.yaml` applying the same metadata to a whole batch.

## Steps

```sh
./.venv/bin/python tools/ingest_inbox.py --once --dry-run   # see the plan first
./.venv/bin/python tools/ingest_inbox.py --once             # do it
./.venv/bin/python tools/ingest_status.py                   # what happened
```

`--once` is the reconciliation sweep and is the source of truth. The daemon
(`tools/watch_inbox.py`) only reduces latency; it nominates files, the sweep
decides. Run the sweep after any daemon downtime.

The pipeline moves files, never deletes them:

| Folder | Meaning |
|---|---|
| `_processing/` | picked up; a crash here resumes from this path |
| `_done/YYYY-MM-DD/` | ingested, filed to `SOURCE_DIR`, pushed to R2 |
| `_failed/` | plus `<name>.error.json` naming the stage and error |
| `_duplicates/` | content hash already known; no database write |
| `_review/` | ingested, but classification or extraction confidence was low |

## Stages

`discovered → stable → hashed → deduped → classified → registered → archived →
pages → structured → extracted → enriched → embedded → done`

Each is idempotent and keyed on `(job_id, stage)` in `ingest_stage_run`, so a
restart re-runs only what did not finish. `archived` comes early and
deliberately: the original is filed and pushed before extraction, so an
extraction failure can never lose the file.

## Expected outputs

- A `source_document` row with `sha256`, `page_count`, `is_spread_paginated`.
- `source_page` for every page, with `content_status` set — `lorem`,
  `template` and `wip` pages are flagged, not silently ingested.
- `doc_node` structure from bookmarks, or from heading detection where the
  document has no TOC.
- `knowledge_item` rows with subtype payloads, each resolving to a `citation`
  carrying both the PDF page index and the printed page label.

## Edge cases and what to do

- **A large file is picked up mid-copy.** Should not happen: a file is only
  nominated once its size and mtime have been unchanged across sweeps
  (`.ingest_stability.json`). If it does, the job fails at `pages`; delete the
  job row and re-drop.
- **Same file dropped twice.** Second lands in `_duplicates/`. Identity is the
  SHA-256, not the filename.
- **Edited copy under the same name.** Registered as a new revision:
  `supersedes_id` set, previous row `is_current = false`. Old citations still
  resolve. Four of the six crib sheets are stamped Draft, so expect this.
- **Unrecognised document shape.** The generic extractor still runs and the
  document is still full-text searchable; the file lands in `_review/`.
  Nothing is ever silently discarded.
- **Re-running an extractor after improving it.** `write_extraction` is
  idempotent per document — it clears that document's prior knowledge items
  first. Use `--force <sha>` to re-run every stage for one job.
- **Zero items from a document.** Not automatically a bug. Check the persisted
  warnings before assuming so:
  ```sql
  SELECT stats->'warnings' FROM ingest_stage_run WHERE stage = 'extracted';
  ```
  An extractor declining a document it does not fit, with a warning, is
  correct behaviour.

## Learned constraints

- RLS is FORCED. A `psql` session without `set app.account_id` sees zero rows
  and looks like an empty database. Always go through `tools/db.py`.
- Never print a real filename into a log line that could reach the repo.

## Pruning the job table

```sh
python -m tools.ingest_status --prune          # list what would go
python -m tools.ingest_status --prune --yes    # delete it
```

Only jobs with `document_id IS NULL` are eligible. A job that registered a
document *is* that document's provenance — when it was picked up, what the
classifier thought it was, which stages ran — so deleting it would throw away
the only record of how the corpus got here. A job that produced nothing has
nothing to lose.

Worth knowing when reading the Ingest view: the original 14 documents were
loaded through the tools directly rather than dropped into `inbox/`, so they
have no `ingest_job` row and never appear there. The view describes what came
through the folder, not what is in the corpus.
