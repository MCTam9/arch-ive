"""Give each document a human title, so the UI stops labelling everything by slug.

    python -m tools.set_document_titles            # show what would change
    python -m tools.set_document_titles --apply

Every `source_document.title` was NULL, so browse, the matrix, the review queue
and every citation read `crib-water` and `framework-vol-e1`. Slugs are the
right *identifier* -- CONTRACT.md makes them the only one permitted in code,
commits and issues -- but they are a poor label to read a corpus through.

Titles come from `private/documents.yaml` (gitignored, alongside the real
filenames) so the public repo never carries them. A slug with no `title:` there
falls back to a title derived from the slug and doc_kind, which is why a fresh
clone still gets something readable.

Nothing here may contain a client, consultant or project name -- these strings
are rendered in the UI and would be the easiest possible way to undo the whole
point of the split. `scripts/scan_forbidden.py` checks the repo, not the
database, so this one is on the author.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from tools import db
from tools.env import load_env

PRIVATE_YAML = Path("private/documents.yaml")

# Fallback wording when the private file has no title for a slug.
KIND_SUFFIX = {
    "crib_sheet": "crib sheet",
    "framework": "framework",
    "implementation_plan": "implementation plan",
    "solutions_framework": "solutions framework",
    "guideline_report": "report",
    "deck": "deck",
    "calculator": "calculator",
    "standard": "standard",
}


def derive(slug: str, doc_kind: str) -> str:
    """A readable label from the slug alone.

    `crib-water` -> `Water — crib sheet`. Deliberately dumb: the private file
    is where a better title belongs, and a derived one should never look
    authoritative enough to stop someone writing the real one.
    """
    parts = slug.split("-")
    if parts and parts[0] in ("crib", "framework", "calc", "typology", "deck"):
        parts = parts[1:]
    stem = " ".join(parts).replace("vol ", "Volume ").strip()
    stem = stem[:1].upper() + stem[1:] if stem else slug
    suffix = KIND_SUFFIX.get(doc_kind)
    return f"{stem} — {suffix}" if suffix else stem


def titles_from_private() -> dict[str, str]:
    if not PRIVATE_YAML.is_file():
        print(f"{PRIVATE_YAML} not found — deriving every title from its slug")
        return {}
    try:
        import yaml
    except ImportError:
        raise SystemExit("PyYAML is needed to read the private metadata: pip install pyyaml")
    data = yaml.safe_load(PRIVATE_YAML.read_text()) or {}
    out: dict[str, str] = {}
    for entry in data.get("documents", []):
        if isinstance(entry, dict) and entry.get("slug") and entry.get("title"):
            out[entry["slug"]] = str(entry["title"]).strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the titles (otherwise dry run)")
    args = ap.parse_args(argv)
    load_env()

    supplied = titles_from_private()
    changed = 0

    with db.connect() as conn:
        docs = db.all_rows(
            conn,
            "SELECT id, slug, doc_kind::text AS doc_kind, title FROM source_document WHERE is_current ORDER BY slug",
        )
        for doc in docs:
            want = supplied.get(doc["slug"]) or derive(doc["slug"], doc["doc_kind"])
            if doc["title"] == want:
                print(f"  {doc['slug']:<30} unchanged")
                continue
            source = "private" if doc["slug"] in supplied else "derived"
            print(f"  {doc['slug']:<30} -> {want}   [{source}]")
            changed += 1
            if args.apply:
                db._exec(
                    conn,
                    "UPDATE source_document SET title = %s WHERE id = %s",
                    (want, doc["id"]),
                )
        if args.apply:
            conn.commit()

    if not changed:
        print("every title already matches")
    elif args.apply:
        print(f"updated {changed} title(s)")
    else:
        print(f"{changed} title(s) would change. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
