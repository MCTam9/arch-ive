# Workflow: add an extractor for a new document shape

**Objective.** Teach the pipeline a document shape it does not yet understand,
without touching the daemon, the stage runner or any other extractor.

## The registry — what is already handled

Check here before writing anything. Loosening an existing extractor to swallow
a new shape is how one silently starts claiming another's documents.

| Module | `doc_kinds` | Shape |
|---|---|---|
| `extractors/crib_sheet.py` | `crib_sheet` | the 2-page graded assessment grids; column bands recovered per file |
| `extractors/compliance_table.py` | `implementation_plan`, `framework` | coded `strategy → KPI + target + stage` tables |
| `extractors/smart_city.py` | `solutions_framework` | solution-scoring matrices — shares a page geometry and a family with the compliance volumes but not an atomic unit, which is why it needed its own `doc_kind` |
| `extractors/typology_catalogue.py` | `guideline_report` | typologies, prototypes and clusters against a declared design-variable vocabulary |
| `extractors/spreadsheet.py` | `calculator` | sheets, cells and formulas → `template_parameter` |
| `extractors/deck.py` | `deck` | slide decks, heavily image-only |
| `extractors/generic.py` | `unknown`, `guideline_report`, `deck`, `standard` | fallback: pages, chunks, full-text. Still searchable, never silently discarded |
| `extractors/support.py` | — | shared helpers, not an extractor: `parse_value`, `slugify`, `clean`, `is_placeholder_value`, `page_is_real`. Registers nothing and must stay import-safe |

`scripts/check_wat.py` fails if a module in `extractors/` is missing from this
table, so the registry cannot quietly go stale.

## When this applies

A document ingests but yields few or no typed knowledge items, and inspection
shows a repeating structure the existing extractors do not recognise. Adding a
module is the answer; loosening an existing extractor usually is not — that is
how one extractor silently starts claiming another's documents.

## Steps

**1. Read the document before writing any code.** This is the step people skip
and it is the one that determines whether the extractor works.

```python
import pymupdf
doc = pymupdf.open(path)
doc.get_toc()                      # bookmarks, if any
doc[i].get_text("dict")            # blocks, lines, spans, font sizes
doc[i].get_text("words")           # (x0,y0,x1,y1,word,...) — for tables
doc[i].get_drawings()              # ruled lines and fills → column bands
doc[i].get_pixmap()                # render and LOOK at it
```

Establish empirically: which page ranges hold the repeating structure, what
the columns are, and where the geometry changes. Column bands are recovered
per file, never hardcoded — five of the six crib sheets share a geometry and
the sixth does not.

**2. Write `extractors/<shape>.py`.** Copy the shape of an existing module.
It must:

- declare `doc_kinds: tuple[str, ...]` and end with an instance plus
  `pipeline.register(...)`;
- be **pure** — no database, no network, no writes outside `.tmp/`. Take a
  `DocumentContext`, return an `Extraction`. The caller writes it, in one
  transaction, so a failed write never leaves half a document behind;
- use only the payload keys `tools/write_extraction.py` accepts. Read it
  first;
- link records with caller-local `ref` strings, not ids that do not exist yet.

**3. Route it.** Add a signal to `tools/classify_document.py` if the classifier
cannot already reach your `doc_kind`. Cheap signals first (page size, producer,
page count, bookmark depth); the VLM fallback costs real time.

**4. Verify against the source.** Follow `workflows/verify_extraction.md`. Do
not skip this — text-layer extraction reads plausibly and is wrong often.

**5. Test.** `tests/test_<shape>.py` with synthetic fixtures. Tests run against
a separate database via `tests/conftest.py`; do not defeat that redirect.

## Rules that came out of getting this wrong

- **The fallback extractor must import first.** `generic.py` claims several
  `doc_kinds` so unrecognised documents still ingest, and with plain
  alphabetical import order it silently replaced the deck extractor. See
  `load_extractors()` in `tools/pipeline.py`.
- **A matrix cell holds 0..n statements, not one.** Many stack two or three;
  many intersections are empty.
- **Keep the verbatim string.** `value_text` and `target_text` are NOT NULL.
  Parse into `value_numeric` where you can; set `is_placeholder` for `X%` and
  `Xkm`; use `value_min`/`value_max` for `700-800ppm`; put asterisk footnotes
  in `caveat_text`. Never drop what you could not parse.
- **Warn rather than guess.** A warning is persisted and queryable; a guess is
  indistinguishable from data.
- **Flag placeholder content.** Lorem, `TEMPLATE ONLY` and `WIP` stamps set
  `content_status`. Serving placeholder text as guidance is this system's
  worst failure mode.
- **Obey `content_status`, and share the rule if you check it twice.**
  `extractors/support.py:page_is_real` reads what ingest decided and stored,
  and most extractors should do only that. `generic.py` also re-checks the
  page's own text, deliberately: it takes the documents nobody wrote a shape
  for, so it is the one that meets a document ingest got wrong, and
  `test_generic_marks_wip_page_as_skipped_when_upstream_missed_it` pins that.
  What it must not do — and did — is re-check with a *different* rule. It
  carried three-of-six stock Latin tokens against ingest's word-fraction test
  above a 150-word floor. Both agreed on all 806 pages, which was luck rather
  than design. `page_status_from_text` in `support.py` is now that rule, once,
  and ingest and the extractor both reach it.
- **Name a seeded rating scale; do not mint one.** `db/seed.sql` owns the
  scales because they are shared vocabulary: `rating_level_crosswalk` is what
  lets an "Exemplar" on one ladder compare with a "Transformational" on
  another, and `RATING_LEVEL_ORDINAL` in `tools/classify_facets.py` is keyed on
  the same slugs. `smart_city.py` appended a scale of its own carrying the same
  four rungs under a second slug, ordinals starting at 0 instead of 1, and
  nothing ever referenced it. An extractor that stops emitting a scale does not
  remove it either — `_upsert_rating_scales` is `ON CONFLICT DO UPDATE` and
  never deletes — so the cleanup is always a migration.
- **A framework needs its `rating_scale_ref`.** It is what
  `framework.rating_scale_id` becomes, and the web matrix reads that to work
  out which level columns exist. `compliance_table.py` passed `None`, so all 62
  criteria of `masterplan-sustainability` rendered as "Nothing in this sheet" —
  a populated framework that read as an empty one.
