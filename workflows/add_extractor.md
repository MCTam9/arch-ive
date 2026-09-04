# Workflow: add an extractor for a new document shape

**Objective.** Teach the pipeline a document shape it does not yet understand,
without touching the daemon, the stage runner or any other extractor.

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
