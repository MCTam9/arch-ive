"""Prove an R2 credential pair is read-only on the page bucket before it ships.

The web app only ever *reads* page renders, so the token in the Vercel
environment should be able to do only that. It started life as the
account-scoped R2 token -- the same credentials that can write the page bucket
and reach the originals bucket, whose contents are client-side encrypted but
are still not something a web function has any business fetching.

Swapping a token is the kind of change that fails silently in the direction
that matters: a token with too little scope takes every page render off the
site, and a token with too much looks perfectly healthy. So this checks both
directions before the value goes anywhere near production.

    python3 -m tools.check_r2_token --env-file .tmp/r2-read.env

The env file holds the new credentials and nothing else:

    R2_ACCESS_KEY_ID=...
    R2_SECRET_ACCESS_KEY=...

Everything else (account id, bucket names) comes from `.env`. Keep the file in
`.tmp/`, which is gitignored and disposable, and delete it once the value is in
Vercel -- `vercel env add` reads from stdin so the secret need not be typed.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

from tools import db
from tools.env import load_env, require


def _load_candidate(env_file: str | None) -> tuple[str, str]:
    """The credentials under test: the file's if given, otherwise the ambient
    ones -- which is how you check what is *currently* configured."""
    if not env_file:
        key_id, secret = require("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
        return key_id, secret

    values: dict[str, str] = {}
    for line in Path(env_file).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        values[k.strip()] = v.strip().strip("'\"")
    missing = [k for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY") if not values.get(k)]
    if missing:
        raise SystemExit(f"check_r2_token: {env_file} is missing {', '.join(missing)}")
    return values["R2_ACCESS_KEY_ID"], values["R2_SECRET_ACCESS_KEY"]


def _client(key_id: str, secret: str):
    import boto3
    from botocore.config import Config

    (account,) = require("R2_ACCOUNT_ID")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
        # A denied call is the expected outcome of three of these checks;
        # retrying it three times just makes the run slower.
        config=Config(retries={"max_attempts": 1}),
    )


def _sample_key() -> str:
    """A key the app really serves, taken from the database rather than guessed.

    Checking read access against an invented key cannot distinguish "denied"
    from "not found", and both come back as a 404 from R2.
    """
    with db.connect() as conn:
        row = db.one(
            conn,
            "SELECT page_image_key FROM source_page "
            "WHERE page_image_key IS NOT NULL ORDER BY page_image_key LIMIT 1",
        )
    if not row:
        raise SystemExit("check_r2_token: no source_page has a page_image_key to test with")
    return row["page_image_key"]


def _denied(exc: Exception) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return code in ("AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "Forbidden")


def run_checks(key_id: str, secret: str) -> list[tuple[bool, str]]:
    """(passed, description) for each check, in the order they are reported."""
    s3 = _client(key_id, secret)
    pages = os.environ.get("R2_BUCKET_PAGES") or require("R2_BUCKET_PAGES")[0]
    originals = os.environ.get("R2_BUCKET")
    results: list[tuple[bool, str]] = []

    # 1. must read -- this is the whole job of the token
    sample = _sample_key()
    try:
        body = s3.get_object(Bucket=pages, Key=sample)["Body"].read(64)
        results.append((len(body) > 0, f"read a real page render ({len(body)} bytes of {sample})"))
    except Exception as exc:  # noqa: BLE001 -- the message is the finding
        results.append((False, f"CANNOT read {sample}: {type(exc).__name__} {exc}"))

    # 2 and 3. must not write, must not delete. Both act on a probe key that
    #    does not exist, never on a real render: a delete check aimed at a live
    #    key would destroy a page render the moment it *passed* the wrong way,
    #    which is a strange way to find out a token has too much scope. R2
    #    answers a permitted delete of a missing key with a plain success, so
    #    the probe distinguishes allowed from denied just as well.
    probe = f"_token-check/{uuid.uuid4()}"
    wrote = False
    try:
        s3.put_object(Bucket=pages, Key=probe, Body=b"x")
        wrote = True
        results.append((False, f"CAN write {pages} -- token is not read-only"))
    except Exception as exc:  # noqa: BLE001
        results.append((_denied(exc), f"cannot write {pages} ({type(exc).__name__})"))

    try:
        s3.delete_object(Bucket=pages, Key=probe)
        results.append((False, f"CAN delete from {pages} -- token is not read-only"))
    except Exception as exc:  # noqa: BLE001
        results.append((_denied(exc), f"cannot delete from {pages} ({type(exc).__name__})"))
        if wrote:
            results.append((False, f"and {probe} is still there; delete it by hand"))

    # 4. must not reach the originals bucket at all
    if originals:
        try:
            s3.list_objects_v2(Bucket=originals, MaxKeys=1)
            results.append((False, "CAN list the originals bucket -- scope it to the page bucket"))
        except Exception as exc:  # noqa: BLE001
            results.append((_denied(exc) or "NoSuchBucket" in str(exc),
                            f"cannot reach the originals bucket ({type(exc).__name__})"))
    else:
        results.append((True, "R2_BUCKET unset; no originals bucket to check"))

    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env-file", help="file holding the candidate R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY")
    args = ap.parse_args()

    load_env()
    key_id, secret = _load_candidate(args.env_file)
    source = args.env_file or ".env"
    print(f"check_r2_token: testing the credentials in {source} (…{key_id[-4:]})\n")

    results = run_checks(key_id, secret)
    for ok, description in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {description}")

    failed = [d for ok, d in results if not ok]
    if failed:
        print(f"\ncheck_r2_token: {len(failed)} check(s) failed -- do not ship this token")
        return 1
    print("\ncheck_r2_token: read-only on the page bucket, and nothing else")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
