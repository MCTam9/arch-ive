"""Write figure descriptions into source_asset, from a JSONL a model produced.

    python3 -m tools.describe_figures --file .tmp/figures/pilot.jsonl --yes
    python3 -m tools.describe_figures --status
    python3 -m tools.describe_figures --sample 20 --out .tmp/figures/todo.jsonl

**This tool is a writer, not an API client, and that is the design.** The
producer of the descriptions is undecided: the pilot runs them through a Claude
Code session at no cost and with no new account, and the full run may later go
through the Claude API, Bedrock in an EU region, or a local model. Every one of
those produces the same thing -- an asset id and a paragraph -- so the part
that touches the database should not care which. Swapping the model later is
then a change to the producer and nothing else.

Each line of the input is one object:

    {"asset_id": "…", "description": "…", "model": "claude-opus-5"}

`--sample` writes the other half: the list of assets still wanting a
description, with their local crop paths, so a producer knows what to look at.

**Provenance.** A description is *not* something the document says, and this
corpus's whole discipline is that every answer points at a page. `content_status`
cannot carry the distinction -- its values ('real', 'wip', 'lorem', …) describe
how finished the *source* is, not who wrote the text -- so the model and the
timestamp are recorded on the row, and anything rendering a description is
expected to say where it came from.

Every run also writes `audit_log` rows -- one per document -- because
describing a figure means its image was sent to whatever produced the text.
`tools/fetch_original.py` logs a document leaving the archive; sending several
hundred crops of that document's contents is the same kind of event, and was
for one release the larger of the two going unrecorded. The row names the
producer and the count and references the document by id, so the log keeps the
property that makes it safe to read: no titles, no slugs, no paths.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from psycopg.types.json import Jsonb

from tools import db
from tools.env import load_env

MIN_LENGTH = 20


def status(conn) -> int:
    rows = db.all_rows(
        conn,
        """
        SELECT d.slug,
               count(*)::int                                          AS assets,
               count(a.image_key)::int                                AS cropped,
               count(a.vlm_description)::int                          AS described
          FROM source_asset a
          JOIN source_page p     ON p.id = a.page_id
          JOIN source_document d ON d.id = p.document_id
         GROUP BY d.slug
         HAVING count(a.image_key) > 0
         ORDER BY count(a.image_key) DESC
        """,
    )
    if not rows:
        print("describe_figures: nothing cropped yet -- run tools/crop_figures.py first")
        return 0
    print(f"  {'document':34} {'assets':>7} {'cropped':>8} {'described':>10}")
    for r in rows:
        print(f"  {r['slug']:34} {r['assets']:>7} {r['cropped']:>8} {r['described']:>10}")
    models = db.all_rows(
        conn,
        "SELECT coalesce(vlm_model, '(unrecorded)') AS model, count(*)::int AS n "
        "FROM source_asset WHERE vlm_description IS NOT NULL GROUP BY 1 ORDER BY 2 DESC",
    )
    for m in models:
        print(f"  by {m['model']}: {m['n']}")
    return 0


def sample(conn, n: int, document: str | None) -> list[dict]:
    """Cropped assets still wanting a description, spread across documents.

    Spread deliberately: taking the first N by size would hand back twenty
    figures from whichever document happens to hold the biggest ones, which
    tells you how a model handles one document rather than this corpus.
    """
    sql = """
        SELECT a.id::text AS asset_id, a.image_key, d.slug AS document_slug,
               p.page_index, p.printed_page_label,
               row_number() OVER (PARTITION BY d.slug ORDER BY a.id) AS rn
          FROM source_asset a
          JOIN source_page p     ON p.id = a.page_id
          JOIN source_document d ON d.id = p.document_id
         WHERE a.image_key IS NOT NULL AND a.vlm_description IS NULL
    """
    params: tuple = ()
    if document:
        sql += " AND d.slug = %s"
        params = (document,)
    rows = db.all_rows(conn, f"SELECT * FROM ({sql}) s ORDER BY rn, document_slug LIMIT %s",
                       (*params, n))
    return [{k: v for k, v in r.items() if k != "rn"} for r in rows]


def load(conn, path: Path) -> tuple[list[str], list[str]]:
    """Apply a JSONL of descriptions. Returns (asset ids written, problems)."""
    written: list[str] = []
    problems: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"line {lineno}: not JSON ({exc.msg})")
            continue
        asset_id = row.get("asset_id")
        description = (row.get("description") or "").strip()
        model = row.get("model")
        if not asset_id or not description:
            problems.append(f"line {lineno}: needs both asset_id and description")
            continue
        # A one-word description is worse than none: it is unfalsifiable, it
        # dilutes whatever the chunk it lands in already says, and it marks the
        # asset done so nothing will revisit it.
        if len(description) < MIN_LENGTH:
            problems.append(f"line {lineno}: description is {len(description)} chars, too short to be useful")
            continue
        if not model:
            problems.append(f"line {lineno}: no model recorded -- provenance is not optional here")
            continue
        result = conn.execute(
            "UPDATE source_asset SET vlm_description = %s, vlm_model = %s, "
            "vlm_described_at = now() WHERE id = %s AND image_key IS NOT NULL",
            (description, model, asset_id),
        )
        if result.rowcount == 0:
            problems.append(f"line {lineno}: no cropped asset {asset_id}")
            continue
        written.append(asset_id)
    return written, problems


def audit(conn, asset_ids: list[str]) -> int:
    """One audit_log row per document: what was described, by what, how many.

    Grouped by document rather than per asset because the event worth recording
    is "figures from this document were sent to a model"; 898 rows saying so
    individually would bury the twelve download rows sharing the table.

    Never raises. A log that can fail the write it is logging is worse than no
    log -- the descriptions are committed by the time this runs, and a warning
    that the row is missing is recoverable in a way a lost run is not.
    """
    if not asset_ids:
        return 0
    try:
        rows = db.all_rows(
            conn,
            """
            SELECT p.document_id::text AS document_id, a.vlm_model AS model,
                   count(*)::int AS figures
              FROM source_asset a
              JOIN source_page p ON p.id = a.page_id
             WHERE a.id = ANY(%s::uuid[])
             GROUP BY 1, 2
            """,
            (asset_ids,),
        )
        for r in rows:
            conn.execute(
                "INSERT INTO audit_log (account_id, action, document_id, detail) "
                "VALUES (%s, 'describe', %s, %s)",
                (db.account_id(), r["document_id"],
                 Jsonb({"via": "tools.describe_figures", "producer": r["model"],
                        "figures": r["figures"]})),
            )
        conn.commit()
        return len(rows)
    except Exception as exc:  # noqa: BLE001 -- logging must never be the failure
        print(f"warning: could not write audit_log rows: {exc}", file=sys.stderr)
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true", help="what is cropped and what is described")
    ap.add_argument("--sample", type=int, help="write N assets still needing a description")
    ap.add_argument("--document", help="restrict --sample to one document slug")
    ap.add_argument("--out", default=".tmp/figures/todo.jsonl", help="where --sample writes")
    ap.add_argument("--file", help="JSONL of {asset_id, description, model} to apply")
    ap.add_argument("--yes", action="store_true", help="write; without it --file is a dry run")
    args = ap.parse_args()

    load_env()
    with db.connect() as conn:
        if args.status or not (args.sample or args.file):
            return status(conn)

        if args.sample:
            rows = sample(conn, args.sample, args.document)
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            print(f"describe_figures: {len(rows)} asset(s) -> {out}")
            return 0

        path = Path(args.file)
        if not args.yes:
            written, problems = 0, []
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                if line.strip():
                    written += 1
            print(f"describe_figures: {written} line(s) in {path}; pass --yes to write")
            return 0

        written, problems = load(conn, path)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        if problems and not written:
            print("describe_figures: nothing written", file=sys.stderr)
            return 1
        conn.commit()
        logged = audit(conn, written)
        print(f"describe_figures: wrote {len(written)} description(s), "
              f"{len(problems)} rejected, {logged} audit row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
