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

**The order is `STAGES` in `tools/pipeline.py`, and only there.** It pairs each
state with the function that runs it, `tools/ingest_inbox.py` iterates it, and
this table describes that list rather than being a second copy of it. It used
to be a second copy, and the second half of it had drifted out of the code
entirely.

`discovered` and `stable` are not stages: a file is seen, then held until its
size and mtime stop moving, before any job has something to record. Everything
after that:

| Stage | What runs | Worth knowing |
|---|---|---|
| `hashed` | SHA-256 and size onto `ingest_job` | identity is content, never the filename |
| `deduped` | sha lookup against `source_document` | the only stage that can end a job early — a match files the original to `_duplicates/` and nothing after it runs |
| `classified` | `tools/classify_document.py` | its confidence is what decides `_done/` against `_review/` at the very end |
| `registered` | `ingest_document.register_document` | revision handling; sets `ingest_job.document_id` |
| `archived` | `archive_original.archive` | before extraction on purpose: an extraction failure can never lose the file |
| `pages` | `ingest_document.extract_pages` | `source_page`, `source_asset`, page renders |
| `structured` | `build_structure.build_structure` | `doc_node` from bookmarks, or heading detection |
| `extracted` | the registered extractor, then `write_extraction` | the extractor is pure; the caller writes |
| `enriched` | `pipeline.ENRICHMENTS`, in order | `classify_facets` → `link_stages` → `chunk_pages` → `chunk_figures` → `refresh_chunk_text` |
| `embedded` | `embed_chunks.embed_pending` | *skipped*, not failed, where `sentence-transformers` is absent |

Each is idempotent and keyed on `(job_id, stage)` in `ingest_stage_run`, so a
restart re-runs only what did not finish.

### Why the five enrichment tools share one stage

`ingest_state` in `db/schema.sql` has exactly one name — `enriched` — for all of
them, `ingest_job.state` and `ingest_stage_run.stage` are both that enum, and
`ingest_stage_run` is `UNIQUE (job_id, stage)`. So they are five steps inside
one stage and one transaction, not five resume points: a failure in any of them
rolls back all five and re-runs all five. Splitting one out means adding an enum
value first, in a migration, and only then moving it into `STAGES`.

`refresh_chunk_text` runs last of the five because rewriting a chunk clears its
`embedding`, and `embedded` is the stage after.

**`crop_figures`, `describe_figures` and `upload_page_images` are deliberately
not in the sequence.** They need R2, a restored original and externally-produced
model output; dropping a file into `inbox/` must not start doing network I/O.
They stay manual, and the sections below are how to run them.

## Expected outputs

- A `source_document` row with `sha256`, `page_count`, `is_spread_paginated`.
- `source_page` for every page, with `content_status` set — `lorem`,
  `template` and `wip` pages are flagged, not silently ingested.
- `doc_node` structure from bookmarks, or from heading detection where the
  document has no TOC.
- `knowledge_item` rows with subtype payloads, each resolving to a `citation`
  carrying both the PDF page index and the printed page label.
- `item_term` facet tags and `item_stage` links, from the `enriched` stage.
- `chunk` rows for the items, for every page no item was extracted from
  (windowed), and for every figure already carrying a description — with an
  embedding, unless `sentence-transformers` is absent and `embedded` skipped.

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

That is one function with three callers, deliberately: the writer runs it
inline, the `enriched` stage runs it again over the whole document once the
page and figure chunks exist, and the CLI runs it over an existing corpus after
the composer changes. All three go through the same `refresh()`, so they cannot
drift about what a typed chunk says.

The commands below are the corpus-wide form. A document coming through `inbox/`
needs neither: `refresh_chunk_text` is a step of `enriched`, and `embed_chunks`
is the stage straight after it.

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

### Page text is windowed, not stored whole

A page no item was extracted from becomes a chunk so its text stays findable.
That chunk used to be the entire page — median 2,186 characters, longest
13,642 — against an embedding model with a **512-token window**. 129 of the 398
page chunks (32%) exceeded it, so their vectors described only the top of the
page while the row looked perfectly indexed. Full-text was never affected;
`tsv` covers the whole string, which is why this hid for so long.

```sh
python3 -m tools.chunk_pages              # dry run
python3 -m tools.chunk_pages --yes
python3 -m tools.embed_chunks
python3 -m tools.chunk_pages --status
```

The `enriched` stage calls `plan()` then `apply()` for the one document it is
ingesting, so this CLI is for re-windowing the existing corpus after `split()`
changes — the `--yes` gate is the CLI's, not the function's.

- **The budget is tokens, not characters.** Prose in this corpus runs to 6.5
  characters per token; a page of dimensions and codes runs to 1.3. A fixed
  character window sized for prose still overflowed on the densest pages —
  which are exactly the pages whose numbers people search for. Each page gets a
  character budget derived from its own density, estimated without loading the
  model (`estimate_tokens`, worst measured underestimate 3%).
- **Windows overlap by ~200 characters.** Not redundancy: a sentence that
  straddles a boundary would otherwise be in neither window's vector.
- **Search groups the windows back into one result per page**, scored by the
  best window, so a long page returns once rather than flooding the list with
  its own fragments — and a match deep in a page now ranks on its own merits.
- `tools/write_extraction.py` calls the same `split()`, so a fresh ingest and a
  re-window of the existing corpus cannot drift apart about what a page chunk
  is. `chunk.ordinal` finally carries something.

### Getting the descriptions into the index

A description that stops at `source_asset` is write-only data: it sits in no
index, so the numbers inside a benchmark table that exists only as a picture
cannot be found by searching for them. `tools/chunk_figures.py` is the step that
puts them in `chunk`, where the generated `tsv` and `tools/embed_chunks.py` do
the rest. Describing figures without running it leaves the work invisible.

```sh
python3 -m tools.chunk_figures            # dry run
python3 -m tools.chunk_figures --yes
python3 -m tools.embed_chunks             # always the second step
python3 -m tools.chunk_figures --status
```

- **`chunk.asset_id` is the key**, and the reason re-running is safe: a new
  description is inserted, a changed one is rewritten *and has its embedding
  cleared*, and one that was withdrawn or downgraded to decorative has its
  chunk removed. A stale chunk left behind stays searchable and describes a
  figure nobody would describe that way now.
- **Decorative figures never reach the index** — the `Decorative image — `
  prefix is what excludes them, which is the whole reason it is a literal
  convention. 784 of the 898 descriptions are indexed.
- **`content_status` stays `'real'`, and that is not a shrug.** It describes
  how finished the *source* is, and the figure genuinely appears in the
  document. Who wrote the sentence is the other axis, carried by `vlm_model`
  and recoverable through `asset_id` — which is what the web uses to stamp
  every description it renders. A page or figure result is reachable at
  `/page/<page id>`, since neither has an `/item/<id>` of its own.
- Run it against **both** databases, like every other write in this workflow.
  Roughly 30-45s against Neon, almost all of it per-row UPDATEs rather than the
  model.
- It is also a step of the `enriched` stage, which is what makes a re-ingest
  (`--force <sha>`) pick up descriptions written since the last run. Describing
  is still manual; only the indexing of what has been described is automatic.

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

## Re-extracting documents already in the corpus

Extraction improvements land at **write time** — `parse_value` recovering a
range or a percentage target, `is_placeholder` applied at every benchmark site,
`printed_page_label` and `bbox` composed onto a citation by
`tools/write_extraction.py`. None of it reaches a document that was written
before the change, and nothing re-reads old rows on its own. Reach for
`tools/reextract.py` when an extractor or the writer has improved and the
corpus should have the benefit:

```sh
python3 -m tools.reextract --status                        # what the corpus holds now
python3 -m tools.reextract                                 # dry run, per document
python3 -m tools.reextract --document crib-water --yes     # one document
python3 -m tools.reextract --yes                           # the corpus pass
```

`--status` is the "is this worth running" view: items and citations per
document, how many of those citations carry a printed page label or a bbox,
how many requirements hold a numeric `target_text` that did not parse, and
whether the original is on local disk at all.

**It rewrites knowledge items in place.** Per document it rebuilds the
`DocumentContext` the `extracted` stage builds, calls the registered extractor,
and hands the result to `write_extraction` — which clears that document's prior
`knowledge_item` rows first. So this is not additive: the items, their subtype
rows, their citations and their chunks are new rows with new ids.

**Enrichment and embedding follow automatically, and that is the point.** The
cascade from `knowledge_item` takes `item_term`, `item_stage`, citations and
item chunks with it, so a re-extract that stopped at the writer would leave a
document untagged, unlinked and unembedded — worse off than before it ran.
Each document therefore gets `pipeline.ENRICHMENTS` in order and then
`embed_chunks.embed_pending`, the same `extracted → enriched → embedded`
sequence a fresh ingest runs. Pass `--no-embed` to defer the last step, and run
`python3 -m tools.embed_chunks` yourself afterwards.

Two things it cannot rebuild, and both are reasons to read the dry run first:

- **Human review decisions.** `review_status` and `reviewed_by` are columns on
  the rows being deleted; every item comes back `pending`. The plan says how
  many decisions each document would reset.
- **Reference resolution.** `external_reference` rows are rewritten
  unresolved — finish with `python3 -m tools.resolve_references --yes`.

What survives untouched: `source_page`, `source_asset` and page renders (this
never runs the `pages` stage), `doc_node` (the writer upserts on `code`), the
lookup tables, and **figure chunks** — they hang off `source_asset`, not
`knowledge_item`, and are not this stage's to delete.

Three safety properties, because this rewrites the corpus:

- **Dry run by default**, `--yes` to write, exactly like `chunk_pages`.
- **One transaction per document**, committed before the next starts. A
  failure part-way through leaves the other thirteen documents as they were,
  and the failed one rolled back to what it held.
- **An extractor returning zero items for a document that currently has some
  is refused**, per document, unless `--allow-empty` says otherwise. That is
  the failure mode that quietly empties a document while leaving it looking
  ingested.

Every document is reported with its before and after counts — items,
citations, chunks, facet tags, stage links — so a run that loses content says
so while you are watching it rather than a week later.

**The original file has to be on local disk.** It is looked up as
`SOURCE_DIR/<slug>/<sha256>.<ext>` and then `.tmp/restored/`; a document with
neither is skipped by name, with the rest of the run continuing.
`tools/reextract.py` never pulls from R2 — a restore decrypts an archived
original and writes the `audit_log` row that says a document left the archive,
which stays a deliberate act with `tools/fetch_original.py` behind it.

## Edge cases and what to do

- **A large file is picked up mid-copy.** Should not happen: a file is only
  nominated once its size and mtime have been unchanged across sweeps
  (`.ingest_stability.json`). If it does, the job fails at `pages`; delete the
  job row and re-drop.
- **Same file dropped twice.** Second lands in `_duplicates/`. Identity is the
  SHA-256, not the filename.
- **Edited copy under the same name.** Registered as a new revision:
  `supersedes_id` set, previous row `is_current = false`. Old citations still
  resolve. Three of the six crib sheets are unissued drafts -- the three
  carrying no `version_label` in `private/documents.yaml` -- so expect this.
  This said four until 2026-09-08. Only the manifest records which: the Draft
  stamp is a graphic and reaches the text layer of exactly one of the six, so
  counting them off the files is not a check that works.
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
