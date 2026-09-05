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

## What a chunk says

A chunk is the unit both legs of search see: `chunk.tsv` is generated from
`chunk.text`, and `chunk.embedding` is computed from it. Anything not in that
column is unreachable, however well modelled it is elsewhere.

For most item types the chunk is the item's title and statement, which is the
whole record. The two typed shapes are different — a benchmark's substance
lives in `benchmark` (metric, value, unit, building use, target year) and a
requirement's context lives in the join to `criterion` and `rating_level`. So
`tools/write_extraction.py` writes the title-and-statement chunk first, then
calls `tools/refresh_chunk_text.py` to compose the typed facts in.

That is one function with two callers, deliberately: the writer runs it per
document at ingest, and the CLI runs it over an existing corpus after the
composer changes.

```sh
python3 -m tools.refresh_chunk_text                    # dry run, prints before/after
python3 -m tools.refresh_chunk_text --document crib-water --yes
python3 -m tools.embed_chunks                          # re-embed what changed
```

Rewriting a chunk sets its `embedding` to NULL, which is exactly the state
`tools/embed_chunks.py` resumes from — so the re-embed is always the second
command and never needs a list of ids. Never leave a stale vector on rewritten
text: the row stays findable, at coordinates that no longer describe it, with
nothing to show that it is wrong.

`chunk.tsv` is a generated column and needs no separate step.

## Figures

`tools/ingest_document.py` locates every raster figure on a page and writes a
`source_asset` row with its bounding box. It does not crop them, and until
`tools/crop_figures.py` existed nothing ever had: `image_key`, `caption` and
`vlm_description` were NULL on all 1,763 rows.

```sh
python3 -m tools.crop_figures --document typology-multifamily
python3 -m tools.crop_figures --limit 20 --no-upload   # local only, nothing recorded
python3 -m tools.crop_figures --verify                 # every DB key resolves in R2
python3 -m tools.describe_figures --status
```

**`--min-pt` is the argument that matters.** Only 898 of the 1,763 assets are
at least 100pt on both sides; 654 are under 40pt on a side, and
`framework-vol-e2` contributes 426 of those out of 453 assets. Those are
bullets, rules, icons and logos. Cropping them costs storage and describing
them puts "a small dark circle" into a corpus people search for guidance. The
default is 100pt on purpose.

Crops come from the **original PDF**, not the page render — a render is
~1400px across a whole A3 sheet, so a figure cut out of it is unreadable. The
original is restored through `tools/fetch_original.py`, which is the only
sanctioned download path: it tries SOURCE_DIR, falls back to the rclone crypt
remote, verifies the SHA-256, and writes the `audit_log` row that says a
document left the archive. Never reach for the files directly; a second,
unlogged download path is exactly what `workflows/archive_and_restore.md`
exists to prevent.

Coordinates need no conversion. `source_asset.bbox`,
`source_page.width_pt/height_pt` and `pymupdf.Page.rect` are all top-left-origin
PDF points.

### Descriptions are generated text, and must stay marked as such

`tools/describe_figures.py` is a **writer, not an API client**, deliberately:
the producer may be a Claude Code session, the Claude API, Bedrock in an EU
region, or a local model, and all four hand back the same thing. It takes a
JSONL of `{asset_id, description, model}` and refuses a line with no model —
provenance is not optional for text no document contains.

`content_status` cannot carry this distinction: its values describe how
finished the *source* is, not who wrote the text. That is why `vlm_model` and
`vlm_described_at` live on `source_asset`, and why anything rendering a
description has to say where it came from. Rule 4 in `web/app/globals.css` —
provenance is visible — applies with more force here than anywhere else in the
system, because a plausible sentence about a drawing is far easier to mistake
for guidance than lorem ipsum is.

### Running the descriptions: fan out, then validate before writing

All 898 cropped figures were described inside a Claude Code session — 20 by
Opus in the pilot, the remaining 878 by a fan-out of Sonnet subagents. What
made that reliable, and what to repeat:

- **Batch by document and page, 40 to an agent.** Consecutive figures share a
  page and a caption scheme, so an agent reading them in order builds context
  a shuffled sample would not give it. Forty crops sit comfortably inside one
  agent's window at ~1,300 visual tokens each.
- **Give each agent the page text, and say what it is for.** It is how an
  agent works out which prototype a drawing belongs to when the label sits
  outside the crop. It is *not* a source of content: a description must not
  borrow a number the image does not show. Agents held that line well when the
  brief stated it explicitly — several flagged "label clipped, geometry only"
  rather than guessing a figure code.
- **One shared brief, one line per figure.** Write the standing instructions
  once to a scratch file (`.tmp/figures/DESCRIBER.md`) and give each agent only
  its batch number. `.tmp/` is disposable, so treat the brief as regenerated
  per run — everything about it that matters is in this section. Agents write
  JSONL directly and report counts, never the descriptions themselves;
  otherwise the orchestrator's context fills with text it is about to load
  from disk anyway.
- **Validate against the manifest before loading.** Check every asset id
  present, no duplicates, valid JSON, nothing under the length floor. All 22
  batches came back 40/40, but the check is what makes that a fact rather than
  an assumption.

**Clean up after a run.** The batch manifests carry page-text excerpts and the
JSONL carries the descriptions, both in plaintext, both client-identifying, and
both redundant the moment `describe_figures` reports `0 rejected` and dev and
Neon agree. Verify the database holds every line, then delete them. The crops
themselves are worth keeping if another pass is likely; `crop_figures`
regenerates them from the archive, at the cost of a restore and its audit rows.

**`Decorative image — ` is a data convention, not a stylistic note.** 114 of
the 898 figures are stock photography, brand covers, slide dividers and
planting photographs: real images carrying no information. Written up as
ordinary descriptions they would be 114 plausible paragraphs diluting search.
The literal prefix (em dash, with a space either side) makes them one `LIKE`
away from being excluded whenever figures reach the index. The share varies
enormously by section — 0 in the typology drawing chapters, 35 of 40 in
landscape materials — so it cannot be predicted per document.

### Every run leaves an audit row

`describe_figures` writes one `audit_log` row per document per run:
`{"via": "tools.describe_figures", "producer": …, "figures": N}`, with the
document referenced by id. Describing a figure means its image was sent to
whatever produced the text, which is the same class of event as a document
leaving the archive — and for one release it was the larger of the two going
unrecorded, since `fetch_original` logged the restore and nothing logged the
898 crops that followed.

Two properties are pinned by `tests/test_describe_figures.py` and should stay
that way. The log **references documents by id and carries no slug, title or
path**: every other column is a uuid, so `detail` is the only place identifying
text could reach the table, and nothing reading `audit_log` should be able to
learn what the corpus is about. And the write **never raises** — the
descriptions are committed before it runs, so a broken log degrades to a
warning rather than failing a run whose expensive half already succeeded.

### Two idempotency traps this area used to have

Both are fixed; both would silently destroy generated content, which is the
worst failure mode available here because the row survives, empty.

- `ingest_document.extract_pages` rebuilt `source_asset` wholesale
  (`DELETE FROM source_asset WHERE page_id`) on every `pages` run. It now
  upserts on `(page_id, sha256)` — the hash of the decoded image bytes is the
  asset's identity across runs. Rows predating the column have a NULL hash,
  cannot be matched, and are replaced.
- `write_extraction` cleared every chunk for a document on re-extraction. It
  now spares `asset_id IS NOT NULL`, because figure chunks are not that
  stage's to delete.

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
