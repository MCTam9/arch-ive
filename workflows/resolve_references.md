# Workflow: resolve references and link stages

**Objective.** Turn the two kinds of dangling connection into either a link or
a stated, actionable gap.

## Part 1 — external references

```sh
./.venv/bin/python tools/resolve_references.py
```

Every `external_reference` ends in one of three states, and the third is the
valuable one:

| Status | Meaning |
|---|---|
| `resolved` | the referent is in the corpus; `resolved_document_id` is set |
| `unresolved` | genuinely ambiguous — nobody has decided yet |
| `missing_source` | the referent is definitely NOT here |

The crib sheets cite `(Module 2 Chapter 4)`, `Module 1 P48` and similar: a
**six-module parent guide that was never collected**. `Module 1..6` map to
Climate Resilience / Operational Carbon / Embodied Carbon / Biodiversity+H&W /
Water / Social Value+Transport.

Recording those as `missing_source` is a deliverable, not a failure. It turns
"we have a dangling citation" into "here is the specific document to go and
find", which is something a person can act on. Leaving them `unresolved` says
only that nobody looked.

## Part 2 — work stages

```sh
./.venv/bin/python tools/link_stages.py
```

Four incompatible stage vocabularies coexist here — old RIBA `C / D&E / FGH /
JKL`, the masterplan `CMP / DMP / CD / SD / DD / Construction`, `master
planning / concept / schematic`, and calendar quarters. `stage_scheme`,
`stage` and `stage_crosswalk` model all four against one canonical spine.

**Always map through the crosswalk.** The crosswalk exists so each document's
native vocabulary stays authoritative; inferring RIBA numbers directly throws
that away and is how a deliverable ends up filed at the wrong gate.

### Two kinds of link, and why the difference is recorded

- A **stage cell or column header** names the stage for one item. Confidence
  0.9+.
- A **whole-document scope statement** in front matter ("written for concept
  and schematic design stages") is true of every item in that document, and
  much weaker. Confidence 0.5.

`item_stage` carries `assigned_by` and `confidence` for exactly this reason:
both links are true, they are not equally precise, and without the distinction
a filter for "RIBA 3" returns them at identical authority.

Where a document has no stage information at all, write nothing. An item with
no stage is honest; an item with a wrong stage sends someone to the wrong
deliverable.

## Known state

Only a handful of items in the current corpus carry a genuine per-item stage
signal. The compliance appendix, which looks like it should, does not: its
columns are `Strategy Reference Code | Compliance Requirements | KPI |
Compliance confirmation | Report reference` — no stage column. Its per-section
axis is contractor **role**, which is modelled as `requirement_scope`, not as
a stage. Do not quietly convert one into the other.
