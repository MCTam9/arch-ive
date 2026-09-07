"""The page store: a private Cloudflare R2 bucket, reached with S3 credentials.

The Python twin of `web/lib/pages-bucket.ts`. Postgres has `tools/db.py`, so a
tool cannot reach the database without RLS coming with it; object storage had
no equivalent and the client was built from scratch in four places, each
restating the endpoint shape, the credentials and the bucket name. This is the
one place that knows any of it.

It holds two kinds of object, both under opaque keys because neither is
client-side encrypted the way the originals are, so a bucket listing must not
describe what it holds:

    pages/<document uuid>/<page>.webp     page renders
    figures/<document uuid>/<asset>.webp  cropped figures

**The read-only-token rule** (commit `2b77769`, `workflows/deploy_web.md`).
Two different credentials reach this bucket and they are not interchangeable:

  - `.env` holds the account-scoped token. It can write, which is why
    `upload_page_images` and `crop_figures` read that file, and it is the
    ambient token this module hands out.
  - Production holds a token scoped **Object Read only** on the page bucket and
    nothing else. Anything that writes therefore runs from a machine with
    `.env`, never from a deployed function -- and a write that starts failing
    with AccessDenied means it picked up the wrong pair, not that R2 is down.

`tools/check_r2_token.py` proves a candidate pair is read-only in both
directions before it ships; that is why `key_id`/`secret` overrides exist here
at all. Do not widen the production token to make a write succeed.

One function returning both the client and the bucket name, rather than a
`client()` and a `bucket()`: every caller today needs the pair together, and
splitting them would let a client built from override credentials be paired
with a bucket read from somewhere else. Callers still call `env.load_env()`
themselves, as they do for `tools.db`.
"""
from __future__ import annotations

import threading
from typing import Any, NamedTuple

from tools.env import require

_local = threading.local()


class PagesBucket(NamedTuple):
    # `s3` is Any because boto3 builds its clients at runtime and ships no
    # stub; naming the type would be a lie, not a check.
    s3: Any
    name: str


def pages_bucket(
    *,
    key_id: str | None = None,
    secret: str | None = None,
    max_attempts: int | None = None,
) -> PagesBucket:
    """The S3 client for the page bucket, and the bucket's name.

    `key_id`/`secret` test a credential pair that is not the ambient one --
    see the read-only-token rule above. `max_attempts` caps botocore's retries,
    which is worth doing only when a denial is the expected answer rather than
    a transient failure.
    """
    if (key_id is None) != (secret is None):
        raise SystemExit("pages_bucket: pass both key_id and secret, or neither")

    if key_id is None:
        account, key_id, secret, bucket = require(
            "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_PAGES"
        )
        # Only the plain ambient client is cached. Anything customised is a
        # one-off by nature, and caching it would let one caller's overrides
        # leak into the next caller's client.
        if max_attempts is None:
            return PagesBucket(_ambient_client(account, key_id, secret), bucket)
    else:
        account, bucket = require("R2_ACCOUNT_ID", "R2_BUCKET_PAGES")

    return PagesBucket(_build(account, key_id, secret, max_attempts), bucket)


def _ambient_client(account: str, key_id: str, secret: str) -> Any:
    """One client per thread: boto3 does not document its clients as safe to
    share across threads for every operation, and `upload_page_images` runs an
    8-thread pool against this bucket. A second client costs far less than a
    wrong answer under load."""
    if not hasattr(_local, "s3"):
        _local.s3 = _build(account, key_id, secret, None)
    return _local.s3


def _build(account: str, key_id: str, secret: str, max_attempts: int | None) -> Any:
    # Imported here rather than at module scope: boto3 is a slow import and
    # most code paths that touch this module never reach the network.
    import boto3

    kwargs: dict[str, Any] = {}
    if max_attempts is not None:
        from botocore.config import Config

        kwargs["config"] = Config(retries={"max_attempts": max_attempts})
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
        **kwargs,
    )
