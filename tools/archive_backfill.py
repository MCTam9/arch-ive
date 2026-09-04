"""Archive originals that predate the R2 configuration.

The archive stage no-ops when R2 is unconfigured -- by design, so a missing
bucket never blocks ingestion -- which means every document ingested before
setup has no offsite copy. This walks the corpus and fills that gap.

Idempotent: documents that already carry an r2_key are skipped.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from tools import db
from tools.archive_original import archive

DOCS_YAML = Path("private/documents.yaml")


def _slug_to_file() -> dict[str, str]:
    """Minimal reader for the slug -> file mapping. Avoids a yaml dependency
    for two keys, and never logs a real filename."""
    mapping: dict[str, str] = {}
    slug = None
    for line in DOCS_YAML.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("- slug:"):
            slug = stripped.split(":", 1)[1].strip().strip('"\'')
        elif stripped.startswith("file:") and slug:
            mapping[slug] = stripped.split(":", 1)[1].strip().strip('"\'')
    return mapping


def backfill(conn) -> dict:
    files = _slug_to_file()
    rows = db.all_rows(
        conn,
        "SELECT id, slug, sha256, r2_key FROM source_document ORDER BY slug",
    )
    done = skipped = failed = 0
    for row in rows:
        if row["r2_key"]:
            skipped += 1
            continue
        rel = files.get(row["slug"])
        if not rel or not Path(rel).exists():
            print(f"  no local file for slug {row['slug']!r}")
            failed += 1
            continue
        key = archive(Path(rel), row["sha256"], row["slug"])
        if key is None:
            print(f"  archive returned no key for slug {row['slug']!r}")
            failed += 1
            continue
        conn.execute(
            "UPDATE source_document SET r2_key = %s, archived_at = now() WHERE id = %s",
            (key, row["id"]),
        )
        print(f"  ok  {row['slug']}")
        done += 1
    return {"archived": done, "already": skipped, "failed": failed}


if __name__ == "__main__":
    with db.connect() as conn:
        result = backfill(conn)
        conn.commit()
    print(f"archive_backfill: {result}")
    sys.exit(1 if result["failed"] else 0)
