# arch-ive

A queryable knowledge base built from architectural reference packages —
sustainability frameworks, design guidelines, typology catalogues and
practice-management calculators.

**This repository contains code only.** The corpus it indexes is third-party
material under copyright and lives entirely outside git: originals in
encrypted object storage, extracted content in Postgres, both reachable only
by approved accounts.

## Why it isn't a single RAG table

The source material is four structurally different things wearing the same
"PDF" coat, and flattening them to text chunks destroys what makes them
useful:

| Shape | Atomic unit |
|---|---|
| Graded assessment matrix | `(criterion × performance level) → requirement` |
| Compliance framework | `strategy code → KPI + target + submission stage` |
| Typology catalogue | `pattern described by a fixed design-variable vocabulary` |
| Parameterised calculator | `named input → formula → output`, phased by work stage |

So the schema keeps four layers — source bytes, document structure, typed
knowledge entities, and a facet/search layer over the top — and every record
cites back to a page.

## Layout

```
db/           schema.sql — the four layers, RLS policies, ingest job tables
tools/        pipeline stages and the inbox watcher
extractors/   one module per document shape, dispatched by classifier
scripts/      leak gate: forbidden-term scanner and git hooks
workflows/    markdown SOPs (see CLAUDE.md)
inbox/        drop zone — gitignored, never committed
private/      real names and mappings — separate private repo, gitignored
```

## Setup

```sh
git clone <this repo> && cd arch-ive
git clone <private repo> private          # required: hooks fail closed without it
./scripts/setup_hooks.sh
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env                      # then fill it in
psql "$DATABASE_URL" -f db/schema.sql
```

## Use

Drop a file into `inbox/`. It is stabilised, hashed, deduplicated, classified,
archived, extracted and indexed without further action. `_failed/`,
`_duplicates/` and `_review/` explain anything that didn't go cleanly.

```sh
python3 tools/ingest_inbox.py --once     # backfill / recovery
python3 tools/watch_inbox.py             # daemon
python3 tools/ingest_status.py           # what happened
```

## Contributing

Identifiers in this repo are opaque by policy: documents are referred to by
slug, organisations by id. A pre-commit hook and a CI job reject commits —
including commit messages and filenames — containing names from a denylist
held outside the repo. If a commit is blocked, use the slug.
