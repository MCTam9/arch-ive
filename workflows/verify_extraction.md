# Workflow: verify extracted values against the source

**Objective.** Establish that a number in the database is the number on the
page. Extraction from a PDF text layer reads plausibly and is wrong often
enough that "it looks right" is not evidence.

## When this applies

Before trusting any new extractor, after changing an existing one, and any
time a figure is about to be quoted to a person or published.

## The failure this exists to prevent

A benchmark table was read from unordered text spans and produced EUI targets
of 35 for one use class and 55 for another. The coordinate-aligned table shows
39 and 72 for the same cells — the text layer returns spans in drawing order,
not reading order, so a row and a column silently swapped. The wrong figures
were reported before anyone rendered the page.

## Steps

**1. Pull the extracted value with its citation.**

```sql
SELECT b.value_text, b.value_numeric, b.unit_id, b.building_use_id,
       b.target_year, c.page_index, c.printed_page_label
FROM benchmark b
JOIN knowledge_item k ON k.id = b.knowledge_item_id
JOIN citation c ON c.knowledge_item_id = k.id
WHERE b.metric_id = 'eui';
```

**2. Re-read that page by coordinate, not by text order.**

```python
words = page.get_text("words")          # (x0, y0, x1, y1, word, ...)
# group by y0 to recover rows, then by x0 to recover columns
```

Row/column position is the evidence. Reading order is not.

**3. Render the page and look at it.**

```python
page.get_pixmap(dpi=150).save(".tmp/check.png")
```

Then actually open it. This catches the cases coordinate grouping also gets
wrong — merged cells, rotated labels, values that live in a figure with no
text layer at all.

**4. Run the standing check.**

```sh
./.venv/bin/python tools/integration_check.py
```

`GROUND_TRUTH` in that file holds hand-verified values. When you verify a new
figure against the source, add it there — that is what turns one verification
into a permanent regression test.

## Expected output

Either the database matches the page, or a defect with the page number, the
extracted value and the true value.

## Edge cases

- **A value that looks like an error may be verbatim.** One sheet lists a
  rising energy target where every comparable series falls. It is what the
  source says. Preserve it and record the doubt in `caveat_text`; do not
  correct the source.
- **Placeholder values** (`X%`, `Xkm`) are data, flagged with
  `is_placeholder`, not missing values to be filled in.
- **Spread-paginated documents** carry two printed page numbers per PDF page.
  A citation needs both `page_index` and `printed_page_label` or the reader
  cannot find the figure.
- **Never quote a figure you have only read via an agent's summary.** Confirm
  it against the page yourself, at the coordinate level.
