"""Push rendered page images to Cloudflare R2.

    python -m tools.upload_page_images            # upload what is missing
    python -m tools.upload_page_images --verify   # every DB key present in R2?

The renders are produced into `.tmp/pages/`, which CLAUDE.md defines as
disposable, so until they are in object storage the deployed app cannot show a
page at all and the review queue has nothing to compare against.

**Why R2 and not Vercel Blob.** Blob counts `put()`, `copy()` and `list()` as
"Advanced Operations" and the free tier includes 2,000 a month. Uploading this
corpus once is 1,512 puts plus the listing -- 76% of the month's allowance for
a single run, with the store cut off entirely if the cap is passed. R2 gives
roughly a million writes and ten million reads a month with zero egress, which
also makes re-rendering the corpus after a pipeline change free rather than a
budgeting decision.

The bucket is private: no public access, no custom domain. Keys are
`pages/<document uuid>/<page>.webp` -- opaque, so a bucket listing leaks
nothing even though these objects are not client-side encrypted the way the
originals are. They are reachable only through the authenticated proxy at
`web/app/api/page-image/[...key]/route.ts`, which checks the session *and*
that RLS lets that account see the page row.

Keys are byte-identical to the Vercel Blob pathnames they replace, so
`source_page.page_image_key` needs no rewrite.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools import db
from tools.env import load_env, require

PAGES_DIR = Path(".tmp/pages")
PREFIX = "pages/"
CONCURRENCY = 8

_local = threading.local()


def _client():
    """One boto3 client per thread -- clients are not documented as safe to
    share across threads for every operation, and the cost of a second one is
    negligible next to a wrong answer under load."""
    if not hasattr(_local, "s3"):
        import boto3

        account, key_id, secret = require("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
        _local.s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            region_name="auto",
        )
    return _local.s3


def _bucket() -> str:
    return os.environ.get("R2_BUCKET_PAGES") or require("R2_BUCKET_PAGES")[0]


def existing_keys() -> dict[str, int]:
    """Key -> size for everything already under the prefix."""
    s3, bucket, found = _client(), _bucket(), {}
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": PREFIX, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents", []):
            found[obj["Key"]] = obj["Size"]
        if not page.get("IsTruncated"):
            return found
        token = page["NextContinuationToken"]


def _local_files() -> list[Path]:
    return sorted(p for p in PAGES_DIR.rglob("*.webp") if p.is_file())


def _key_for(path: Path) -> str:
    return PREFIX + path.relative_to(PAGES_DIR).as_posix()


def upload() -> int:
    files = _local_files()
    if not files:
        print(f"no renders under {PAGES_DIR}/ -- nothing to upload")
        return 0

    already = existing_keys()
    todo = [f for f in files if _key_for(f) not in already]
    print(f"{len(files)} rendered pages, {len(already)} already in R2, {len(todo)} to send")
    if not todo:
        return 0

    done = threading.Semaphore(0)
    sent = 0
    lock = threading.Lock()

    def send(path: Path) -> int:
        nonlocal sent
        _client().put_object(
            Bucket=_bucket(),
            Key=_key_for(path),
            Body=path.read_bytes(),
            ContentType="image/webp",
            CacheControl="max-age=31536000",
        )
        with lock:
            sent += 1
            if sent % 200 == 0:
                print(f"  {sent}/{len(todo)}")
        return path.stat().st_size

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        total = sum(pool.map(send, todo))
    del done
    print(f"uploaded {sent} page image(s), {total / 1e6:.1f} MB")
    return 0


def verify() -> int:
    """Every page_image_key in the database must resolve to a real object.

    A missing render is invisible until someone opens that record in the
    review queue, which is the worst moment to discover it.
    """
    with db.connect() as conn:
        keys = [
            r["page_image_key"]
            for r in db.all_rows(conn, "SELECT page_image_key FROM source_page WHERE page_image_key IS NOT NULL")
        ]
    present = existing_keys()
    missing = [k for k in keys if k not in present]
    empty = [k for k in keys if present.get(k) == 0]
    print(f"{len(keys)} page_image_key rows, {len(present)} objects under {PREFIX} in R2")
    if missing:
        print(f"MISSING {len(missing)}: {', '.join(missing[:5])}{' ...' if len(missing) > 5 else ''}", file=sys.stderr)
    if empty:
        print(f"ZERO-BYTE {len(empty)}: {', '.join(empty[:5])}", file=sys.stderr)
    if missing or empty:
        return 1
    print("every page render resolves")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true", help="check the database's keys against the bucket")
    args = ap.parse_args(argv)
    load_env()
    return verify() if args.verify else upload()


if __name__ == "__main__":
    raise SystemExit(main())
