# Workflow: facet classification

**Objective.** Tag knowledge items against the nine-facet vocabulary so the
corpus is navigable by topic, scale, stage, use, level and provenance rather
than only by document.

## Inputs

- `taxonomy_term` — 87 terms across 9 taxonomies, with `synonyms[]`.
- `knowledge_item.statement`, its `doc_node` path, its criterion code, its
  rating level, and the document it came from.

## Steps

```sh
./.venv/bin/python tools/classify_facets.py            # whole corpus
./.venv/bin/python tools/classify_facets.py <doc_id>   # one document
```

Idempotent: for every item in scope it replaces that item's tag set rather
than adding to it.

## Expected output

`item_term` rows carrying `confidence` and `assigned_by`. Coverage is reported
per taxonomy, along with the untagged count — read that number, don't skip it.

## The rules that make this safe

- **Precision over recall.** A wrong facet is worse than a missing one: it
  files a hotel benchmark under Residential and someone designs to it. An item
  whose statement names no metric, use class or scale should come back with
  nothing inferred about its content.
- **Confidence is not decoration.** A structural signal (this item came from
  the Water crib sheet, therefore `topic.water`) is worth ~0.95. A single
  keyword in a long statement is worth ~0.4. That number is what a reviewer
  sorts by, and what the UI should surface — never write 1.0 for a guess.
- **Provenance is not inference.** `authority` is a property of the document,
  true of every item in it. Tagging it universally is correct and is not
  over-tagging; content facets are the ones that must be earned.
- **Placeholder content is skipped.** Items flagged `lorem`, `template`, `wip`
  or `draft` are not filed under a real facet — otherwise the facet browser
  serves placeholder text as guidance.

## Maintaining the lexicon

The corpus uses its own words, and the vocabulary lives in
`taxonomy_term.synonyms` (seeded by `db/seed_synonyms.sql`). It has five
spellings of building scale alone, plus `EUI`, `BNG`, `UGF`/`BAF`, `SCI`,
`WWR`, `UTCI`, `l/p/day`, `kgCO2e/m2GIA`. When coverage for a facet looks
thin, the fix is almost always a missing synonym rather than a cleverer rule —
derive new ones from `knowledge_item.statement` and `chunk.text`, not from
what you assume architects say.

## Edge cases

- **One item, many facets** is normal: a topic, a scale, a use and a level.
- **A term with no synonyms will not match anything** beyond its own label.
  42 of 87 terms currently carry synonyms; the rest are the gap.
- Re-run after any extractor change — new items arrive untagged.
