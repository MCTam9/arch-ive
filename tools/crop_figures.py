"""Crop each located figure out of its source PDF and push it to R2.

    python3 -m tools.crop_figures --document typology-multifamily
    python3 -m tools.crop_figures --limit 20 --no-upload   # local only
    python3 -m tools.crop_figures --verify

`source_asset` holds 1,763 rows -- every raster figure tools/ingest_document.py
found on a page, with its bounding box -- and `image_key` has been NULL on
every one since the table was created. The figures are located and never
cropped, so nothing downstream can look at one. This produces the images a
description step reads, and stores them the way page renders are stored.

**The size filter is the important argument.** Of the 1,763 rows only 898 are
at least --min-pt on both sides; 654 are under 40pt on a side. Those are
bullets, rules, icons and logos -- `framework-vol-e2` alone contributes 426 of
them out of 453 assets. Cropping and describing a bullet costs money and puts
"a small dark circle" into a corpus people search for guidance. The default is
deliberately not 0.

Coordinates line up with no conversion: `source_asset.bbox`,
`source_page.width_pt/height_pt` and `pymupdf.Page.rect` are all
top-left-origin PDF points, so the crop is `get_pixmap(clip=Rect(*bbox))`
with no flip. Unlike the page renderer, the zoom here is fixed rather than
derived from page width -- a figure should arrive at a predictable resolution
whether it came off an A3 crib sheet or an A4 appendix.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tools import db
from tools.env import load_env, require

FIGURES_DIR = Path(".tmp") / "figures"
PREFIX = "figures/"
# 4x gives a 100pt figure ~400px on its short side, comfortably above the
# 200px floor below which vision models start guessing, and well under the
# per-image byte limits. Fixed, not page-relative: see the module docstring.
ZOOM = 4.0
WEBP_QUALITY = 80
MIN_PT_DEFAULT = 100


def _object_key(document_id: str, asset_id: str) -> str:
    """Opaque by construction, like the page keys: these objects are not
    client-side encrypted, so a bucket listing must not describe what it holds."""
    return f"{PREFIX}{document_id}/{asset_id}.webp"


def pending(conn, document: str | None, min_pt: float, limit: int | None,
            redo: bool) -> list[dict]:
    """Assets worth cropping, largest first.

    Largest first because a partial run should leave the most useful figures
    done rather than an arbitrary slice, and because the biggest figures are
    the ones most likely to be a real drawing.
    """
    sql = """
        SELECT a.id::text        AS asset_id,
               a.bbox,
               a.image_key,
               p.page_index,
               d.id::text        AS document_id,
               d.slug            AS document_slug,
               d.sha256          AS document_sha256
          FROM source_asset a
          JOIN source_page p     ON p.id = a.page_id
          JOIN source_document d ON d.id = p.document_id
         WHERE a.bbox IS NOT NULL
           AND (a.bbox[3] - a.bbox[1]) >= %s
           AND (a.bbox[4] - a.bbox[2]) >= %s
    """
    params: list = [min_pt, min_pt]
    if not redo:
        sql += " AND a.image_key IS NULL"
    if document:
        sql += " AND d.slug = %s"
        params.append(document)
    sql += " ORDER BY (a.bbox[3] - a.bbox[1]) * (a.bbox[4] - a.bbox[2]) DESC"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    return db.all_rows(conn, sql, tuple(params))


RESTORE_DIR = Path(".tmp") / "restored"


def _source_pdf(row: dict) -> Path | None:
    """The original PDF, restored if it is not already local.

    Cropping needs the PDF itself, not the page render: a render is ~1400px
    across a whole A3 sheet, so a figure cut out of it arrives at a resolution
    no model can read.

    Restoring goes through tools/fetch_original.py rather than reaching for the
    files directly. That tool already knows the two places an original can be
    (SOURCE_DIR, then the rclone crypt remote), verifies the SHA-256 before
    handing the path back, and writes the audit_log row that says a document
    left the archive. Reimplementing any of that here would mean a second,
    unlogged download path -- which is exactly the thing the archive workflow
    exists to prevent.
    """
    existing = sorted(RESTORE_DIR.glob(f"{row['document_sha256']}.*"))
    if existing:
        return existing[0]

    from tools import fetch_original

    try:
        fetch_original.main([row["document_slug"], "--out", str(RESTORE_DIR)])
    except SystemExit as exc:
        print(f"crop_figures: could not restore {row['document_slug']}: {exc}", file=sys.stderr)
        return None

    restored = sorted(RESTORE_DIR.glob(f"{row['document_sha256']}.*"))
    return restored[0] if restored else None


def crop(rows: list[dict], quiet: bool = False) -> list[dict]:
    """Write a WebP per asset under .tmp/figures/<document>/. Returns what was
    written, each row gaining `path` and `key`."""
    import pymupdf

    written: list[dict] = []
    by_document: dict[str, list[dict]] = {}
    for r in rows:
        by_document.setdefault(r["document_id"], []).append(r)

    for document_id, group in by_document.items():
        pdf = _source_pdf(group[0])
        if pdf is None:
            print(
                f"crop_figures: no source PDF for {group[0]['document_slug']} -- "
                f"set SOURCE_DIR, or restore it with tools/fetch_original.py",
                file=sys.stderr,
            )
            continue
        out_dir = FIGURES_DIR / document_id
        out_dir.mkdir(parents=True, exist_ok=True)
        with pymupdf.open(pdf) as doc:
            for row in group:
                x0, y0, x1, y1 = (float(v) for v in row["bbox"])
                page = doc[row["page_index"] - 1]
                try:
                    pix = page.get_pixmap(
                        matrix=pymupdf.Matrix(ZOOM, ZOOM),
                        clip=pymupdf.Rect(x0, y0, x1, y1),
                    )
                except Exception as exc:  # noqa: BLE001 -- one bad figure is not a failed run
                    print(f"crop_figures: {row['asset_id']}: {type(exc).__name__} {exc}",
                          file=sys.stderr)
                    continue
                out_path = out_dir / f"{row['asset_id']}.webp"
                try:
                    pix.pil_save(out_path, format="WEBP", quality=WEBP_QUALITY)
                except Exception:
                    out_path = out_path.with_suffix(".png")
                    pix.save(out_path)
                written.append({**row, "path": out_path,
                                "key": _object_key(document_id, row["asset_id"])})
                if not quiet:
                    print(f"  {row['document_slug']} p{row['page_index']} "
                          f"{int(x1 - x0)}x{int(y1 - y0)}pt -> {out_path.name}")
    return written


def upload(written: list[dict]) -> int:
    """Push to the page bucket under the figures/ prefix. Reuses the R2 client
    shape of tools/upload_page_images.py, and the same account-scoped
    credentials from .env -- production's token is read-only by design and
    cannot write here."""
    import boto3

    account, key_id, secret = require("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    bucket = require("R2_BUCKET_PAGES")[0]
    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
    )
    n = 0
    for row in written:
        s3.upload_file(str(row["path"]), bucket, row["key"],
                       ExtraArgs={"ContentType": "image/webp"})
        row["uploaded"] = True
        n += 1
        if n % 50 == 0:
            print(f"  uploaded {n}/{len(written)}")
    return n


def record(conn, written: list[dict]) -> int:
    """Record only the keys that actually reached the bucket.

    A key in the database with no object behind it is the one failure this tool
    can cause that nothing else will catch: `--verify` finds it later, but in
    the meantime the row claims a crop exists. So the upload marks each row as
    it succeeds and this writes only those.
    """
    rows = [r for r in written if r.get("uploaded")]
    for row in rows:
        conn.execute("UPDATE source_asset SET image_key = %s WHERE id = %s",
                     (row["key"], row["asset_id"]))
    return len(rows)


def verify(conn) -> int:
    """Every image_key in the database resolves to an object. Returns missing."""
    import boto3

    account, key_id, secret = require("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    bucket = require("R2_BUCKET_PAGES")[0]
    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
    )
    keys = [r["image_key"] for r in db.all_rows(
        conn, "SELECT image_key FROM source_asset WHERE image_key IS NOT NULL")]
    missing = 0
    for key in keys:
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except Exception:
            print(f"  missing: {key}", file=sys.stderr)
            missing += 1
    print(f"crop_figures: {len(keys)} key(s) checked, {missing} missing")
    return missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--document", help="restrict to one document slug")
    ap.add_argument("--min-pt", type=float, default=MIN_PT_DEFAULT,
                    help=f"skip figures smaller than this on either side (default {MIN_PT_DEFAULT})")
    ap.add_argument("--limit", type=int, help="crop at most this many, largest first")
    ap.add_argument("--redo", action="store_true", help="include assets that already have a key")
    ap.add_argument("--no-upload", action="store_true", help="write locally, do not touch R2 or the DB")
    ap.add_argument("--verify", action="store_true", help="check every DB key resolves in the bucket")
    args = ap.parse_args()

    load_env()
    with db.connect() as conn:
        if args.verify:
            return 1 if verify(conn) else 0

        rows = pending(conn, args.document, args.min_pt, args.limit, args.redo)
        total = db.scalar(conn, "SELECT count(*) FROM source_asset")
        print(f"crop_figures: {len(rows)} of {total} asset(s) to crop "
              f"(>= {args.min_pt:g}pt on both sides)")
        if not rows:
            return 0

        written = crop(rows)
        print(f"crop_figures: wrote {len(written)} file(s) under {FIGURES_DIR}")
        if args.no_upload:
            print("--no-upload: nothing sent to R2, nothing recorded in the database")
            return 0

        # Upload first, then record, then commit: a crash between the two
        # leaves objects in the bucket with no row pointing at them, which the
        # next run simply overwrites. The other order leaves rows claiming a
        # crop that was never stored.
        try:
            n = upload(written)
        finally:
            recorded = record(conn, written)
            conn.commit()
        print(f"crop_figures: uploaded {n}, recorded {recorded} image_key(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
