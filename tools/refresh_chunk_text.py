"""Rebuild `chunk.text` for benchmark and requirement items from their typed data.

`tools/write_extraction.py` composes a chunk as title + statement. For most
item types that is the whole record, but for the two typed shapes it throws
away exactly the part people search for:

    Flats 2030 target                     <- the entire chunk, before
    Upfront Embodied Carbon, 380 kgCO2e/m2GIA, flats, 2030   <- in benchmark

so the corpus's best answer to "embodied carbon 2030" was unreachable by both
legs of search at once -- the value never reached `chunk.tsv`, and the
embedding was computed over a title that names no metric. Requirements had the
same hole from the other side: their statement is the target text, with the
criterion and rating level they belong to living only in the join.

One composer, two callers. `write_extraction` calls `refresh()` at the end of
a document write so new ingests are correct; this module's CLI runs the same
composer over an existing corpus. Rows whose text is unchanged are left alone;
rows that change have `embedding` set to NULL, which is precisely the state
`tools/embed_chunks.py` resumes from -- so a backfill is always:

    python3 -m tools.refresh_chunk_text --yes
    python3 -m tools.embed_chunks

`chunk.tsv` is a generated column, so full-text updates itself.
"""
from __future__ import annotations

import argparse
import re

from tools import db
from tools.env import load_env

# Spelled out rather than symbolic: '<=' does not survive to_tsvector and does
# not embed, but "at most" does both.
COMPARATOR_WORD = {
    "lt": "less than",
    "lte": "at most",
    "gt": "more than",
    "gte": "at least",
}

ITEM_TYPES = ("benchmark", "requirement")


def _humanise(token: str | None) -> str | None:
    """'office_shell_core' -> 'office shell core'.

    Lookup ids are slugs, and a slug is one token to the text-search parser.
    Splitting them is what lets a search for 'office' reach a row whose only
    mention of it is a building_use_id.
    """
    if not token:
        return None
    return re.sub(r"[_-]+", " ", token).strip() or None


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _value_phrase(comparator: str | None, value: str | None,
                  unit: str | None) -> tuple[str, str] | None:
    """(phrase to write, the raw value it was built from).

    The raw value comes back too because it, not the phrase, is what decides
    whether the fact is already said elsewhere in the chunk. A requirement's
    target_text is usually its statement verbatim -- but 'at least X %' is not
    a substring of X, so comparing the assembled phrase would never notice.
    """
    if value is None or not str(value).strip():
        return None
    value = str(value).strip()
    # A value that already ends in its own unit ('15%', '2.7m') does not get a
    # second one: the source writes the unit inline about half the time.
    if unit and re.search(re.escape(unit) + r"\s*$", value, re.I):
        unit = None
    parts = [COMPARATOR_WORD.get(comparator or "none"), value, unit]
    return " ".join(str(p) for p in parts if p).strip(), value


def _facts(row: dict) -> list[tuple[str, str, str]]:
    """The typed facts for one item, in reading order.

    Labels are words, not punctuation, because they are indexed too: 'Building
    use: flats' answers a search for 'building use' as well as for 'flats'.
    """
    out: list[tuple[str, str | None] | tuple[str, tuple[str, str] | None]] = []
    if row["item_type"] == "benchmark":
        out += [
            ("Metric", row.get("metric_name") or _humanise(row.get("metric_id"))),
            # A placeholder benchmark's value is literally 'X%' or 'Xkm' -- the
            # source sheet's blank to be filled in. Naming the metric is useful;
            # indexing 'X%' is not.
            ("Value", None if row.get("is_placeholder") else _value_phrase(
                row.get("comparator"), row.get("value_text"), row.get("unit_symbol"))),
            ("Building use", _humanise(row.get("building_use_id"))),
            ("Target year", str(row["target_year"]) if row.get("target_year") else None),
            ("Region", _humanise(row.get("region_id"))),
            ("Standard", row.get("standard_name") or _humanise(row.get("standard_id"))),
            ("Caveat", row.get("caveat_text")),
        ]
    else:
        criterion = " ".join(dict.fromkeys(
            p for p in (row.get("criterion_code"), row.get("criterion_title")) if p))
        level = " ".join(dict.fromkeys(
            p for p in (row.get("level_code"), row.get("level_name")) if p))
        out += [
            ("Criterion", criterion or None),
            ("Level", level or None),
            ("Metric", row.get("metric_name") or _humanise(row.get("metric_id"))),
            ("Target", _value_phrase(
                row.get("comparator"), row.get("target_text"), row.get("unit_symbol"))),
            ("Deliverable", row.get("deliverable_name")),
        ]
    # Normalise to (label, phrase, the text that decides duplication). Only
    # _value_phrase carries a separate key; everything else dedupes on itself.
    facts: list[tuple[str, str, str]] = []
    for label, value in out:
        if not value:
            continue
        phrase, key = value if isinstance(value, tuple) else (value, value)
        facts.append((label, str(phrase).strip(), str(key).strip()))
    return facts


def compose(row: dict) -> str:
    """The chunk text for one typed item. Never returns empty.

    Falls back exactly the way write_extraction always has, so an item with
    nothing but a type still produces a NOT NULL text.
    """
    base_parts = [p for p in (row.get("title"), row.get("statement")) if p]
    base = "\n\n".join(base_parts)
    seen = _norm(base)

    lines = []
    for label, phrase, key in _facts(row):
        # A requirement's target_text is usually its statement verbatim, and a
        # benchmark's building use and target year are usually in its title.
        # Repeating either under a label inflates the term frequency of a
        # phrase already present and tells the reader nothing.
        if _norm(key) and _norm(key) in seen:
            continue
        lines.append(f"{label}: {phrase}")

    parts = base_parts + ([". ".join(lines) + "."] if lines else [])
    return "\n\n".join(parts) or row.get("summary") or f"[{row['item_type']}]"


SELECT_SQL = """
SELECT c.id            AS chunk_id,
       c.text          AS current_text,
       ki.item_type::text AS item_type,
       ki.title, ki.statement, ki.summary,
       d.slug          AS document_slug,
       coalesce(b.metric_id, r.metric_id)   AS metric_id,
       m.name                                AS metric_name,
       coalesce(bu.symbol, ru.symbol)        AS unit_symbol,
       coalesce(b.comparator, r.comparator)::text AS comparator,
       b.value_text, b.is_placeholder, b.building_use_id, b.target_year,
       b.region_id, b.standard_id, b.caveat_text,
       st.name         AS standard_name,
       r.target_text, r.deliverable_name,
       cr.code         AS criterion_code,
       cr.title_primary AS criterion_title,
       rl.code         AS level_code,
       rl.name         AS level_name
  FROM chunk c
  JOIN knowledge_item ki ON ki.id = c.knowledge_item_id
  JOIN source_document d ON d.id = ki.document_id
  LEFT JOIN benchmark   b  ON b.knowledge_item_id  = ki.id
  LEFT JOIN requirement r  ON r.knowledge_item_id  = ki.id
  LEFT JOIN unit        bu ON bu.id = b.unit_id
  LEFT JOIN unit        ru ON ru.id = r.unit_id
  LEFT JOIN metric      m  ON m.id  = coalesce(b.metric_id, r.metric_id)
  LEFT JOIN standard    st ON st.id = b.standard_id
  LEFT JOIN criterion   cr ON cr.id = r.criterion_id
  LEFT JOIN rating_level rl ON rl.id = r.rating_level_id
 WHERE ki.item_type = ANY(%s)
"""


def plan(conn, document: str | None = None, document_id: str | None = None) -> list[dict]:
    """Every typed chunk whose composed text differs from what is stored."""
    sql, params = SELECT_SQL, [list(ITEM_TYPES)]
    if document_id:
        sql += " AND ki.document_id = %s"
        params.append(document_id)
    elif document:
        sql += " AND d.slug = %s"
        params.append(document)

    changed = []
    for row in db.all_rows(conn, sql, tuple(params)):
        text = compose(row)
        if text != row["current_text"]:
            changed.append({**row, "new_text": text})
    return changed


def refresh(conn, document: str | None = None, document_id: str | None = None) -> int:
    """Rewrite the changed chunks and clear their embeddings. Returns the count.

    Clearing `embedding` is the whole re-embed protocol: `embed_chunks` selects
    on `embedding IS NULL`, so it picks up exactly these rows and nothing else.
    Leaving a stale vector attached to rewritten text is worse than no vector --
    the row stays findable, at the wrong coordinates, with nothing to show it.
    """
    rows = plan(conn, document, document_id)
    for row in rows:
        conn.execute(
            "UPDATE chunk SET text = %s, embedding = NULL WHERE id = %s",
            (row["new_text"], row["chunk_id"]),
        )
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--document", help="restrict to one document slug")
    ap.add_argument("--sample", type=int, default=3, help="before/after pairs to print")
    ap.add_argument("--yes", action="store_true", help="write; without it this is a dry run")
    args = ap.parse_args()

    load_env()
    with db.connect() as conn:
        rows = plan(conn, args.document)
        by_type: dict[str, int] = {}
        for row in rows:
            by_type[row["item_type"]] = by_type.get(row["item_type"], 0) + 1

        for row in rows[: args.sample]:
            print(f"\n--- {row['document_slug']} · {row['item_type']} ---")
            print(f"  before: {row['current_text']!r}")
            print(f"  after:  {row['new_text']!r}")

        summary = ", ".join(f"{n} {t}" for t, n in sorted(by_type.items())) or "nothing"
        print(f"\nrefresh_chunk_text: {len(rows)} chunk(s) to rewrite ({summary})")
        if not rows:
            return 0
        if not args.yes:
            print("dry run -- pass --yes to write, then run: python3 -m tools.embed_chunks")
            return 0

        n = refresh(conn, args.document)
        conn.commit()
    print(f"refresh_chunk_text: rewrote {n} chunk(s); their embeddings are now NULL")
    print("next: python3 -m tools.embed_chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
