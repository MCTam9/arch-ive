"""Restore one original document from the archive, hash-verified.

    ./.venv/bin/python -m tools.fetch_original <slug> [--out DIR] [--remote-only]

This is the download path, and it is deliberately a local tool rather than a
route in the web app.

The archive is `rclone crypt`: the bytes in R2 are ciphertext and so are the
object names. Decrypting them inside a Vercel function would mean putting the
crypt password in that function's environment -- and the whole point of
client-side encryption here is that the key never sits next to the data or in
the most exposed surface of the system. A web download route would trade the
strongest property of the archive for a convenience. So the app serves page
renders and extracted knowledge, and whole originals come back through this,
run by someone who already has the config and the password.

Two sources, tried in order:
  1. SOURCE_DIR -- the plaintext working copy. No key, no network, no egress.
  2. The R2 crypt remote, via rclone.

Either way the restored file's SHA-256 is checked against the database before
the path is printed. A file that downloads and opens can still be truncated;
hash equality is the only acceptable evidence.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from psycopg.types.json import Jsonb

from tools import db
from tools.archive_original import sha256_of_file


def _source_dir() -> Path:
    return Path(os.environ.get("SOURCE_DIR", "~/arch-ive-source")).expanduser()


def _lookup(conn, slug: str) -> dict:
    row = db.one(
        conn,
        """
        SELECT id, slug, sha256, r2_key, page_count, is_current
          FROM source_document
         WHERE slug = %s
         ORDER BY is_current DESC, ingested_at DESC
         LIMIT 1
        """,
        (slug,),
    )
    if row is None:
        # Either the slug is wrong or RLS is hiding it -- both look identical
        # from here, which is the intended behaviour, so say both.
        raise SystemExit(
            f"no document with slug {slug!r} visible to this account "
            f"(check the slug, and that ARCHIVE_ACCOUNT_ID is set to an active allowlist row)"
        )
    return row


def _log_download(conn, document_id: str, source: str) -> None:
    """Record the restore. Owners running a local tool are still downloads.

    Written in the same connection as the lookup, and non-fatal: failing to
    log must not lose you the file you just restored.
    """
    try:
        db._exec(
            conn,
            "INSERT INTO audit_log (account_id, action, document_id, detail) "
            "VALUES (%s, 'download', %s, %s)",
            (db.account_id(), document_id, Jsonb({"source": source, "via": "tools.fetch_original"})),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 -- logging must never be the failure
        print(f"warning: could not write audit_log row: {exc}", file=sys.stderr)


def _from_local(sha256: str, slug: str) -> Path | None:
    candidates = sorted((_source_dir() / slug).glob(f"{sha256}.*"))
    return candidates[0] if candidates else None


def _from_r2(r2_key: str, out: Path) -> None:
    remote = os.environ.get("RCLONE_REMOTE")
    config = os.environ.get("RCLONE_CONFIG")
    if not remote or not config:
        raise SystemExit("RCLONE_REMOTE and RCLONE_CONFIG must be set in .env to restore from R2")
    if shutil.which("rclone") is None:
        raise SystemExit("rclone is not installed: brew install rclone")

    result = subprocess.run(
        ["rclone", "--config", str(Path(config).expanduser()), "copyto", f"{remote}{r2_key}", str(out)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"rclone restore failed: {result.stderr.strip()[:300]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("slug", help="source_document.slug, e.g. crib-water")
    ap.add_argument("--out", default=".tmp/restored", help="directory to write into")
    ap.add_argument(
        "--remote-only",
        action="store_true",
        help="ignore SOURCE_DIR and pull from R2 -- this is what actually exercises the archive",
    )
    args = ap.parse_args(argv)

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    with db.connect() as conn:
        doc = _lookup(conn, args.slug)
        sha256 = doc["sha256"]

        local = None if args.remote_only else _from_local(sha256, doc["slug"])
        if local is not None:
            dest = out_dir / local.name
            shutil.copy2(local, dest)
            source = "source_dir"
        else:
            if not doc["r2_key"]:
                raise SystemExit(
                    f"{args.slug} has no r2_key -- it was ingested before R2 was configured. "
                    f"Run `python -m tools.archive_backfill` first."
                )
            dest = out_dir / Path(doc["r2_key"]).name
            _from_r2(doc["r2_key"], dest)
            source = "r2"

        restored = sha256_of_file(dest)
        if restored != sha256:
            dest.unlink(missing_ok=True)
            raise SystemExit(
                f"SHA-256 mismatch restoring {args.slug} from {source}: "
                f"expected {sha256[:12]}..., got {restored[:12]}... -- the copy was NOT kept"
            )

        _log_download(conn, doc["id"], source)

    print(f"{args.slug}: restored from {source}, sha256 verified -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
