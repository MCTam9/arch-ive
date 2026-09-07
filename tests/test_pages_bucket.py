"""The page-store seam: one client per thread, and one place that fails.

None of these touch the network -- boto3 builds a client without talking to
R2 -- so they can assert on the thread-local behaviour `upload_page_images`
depends on without uploading anything.
"""
from __future__ import annotations

import os
import threading

import pytest

from tools import pages_bucket as pb

ENV = {
    "R2_ACCOUNT_ID": "acct",
    "R2_ACCESS_KEY_ID": "key",
    "R2_SECRET_ACCESS_KEY": "secret",
    "R2_BUCKET_PAGES": "bucket-under-test",
}


@pytest.fixture
def configured(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    # The cache is per thread and this test process reuses its main thread.
    if hasattr(pb._local, "s3"):
        del pb._local.s3
    yield
    if hasattr(pb._local, "s3"):
        del pb._local.s3


def test_returns_client_and_bucket(configured):
    s3, bucket = pb.pages_bucket()
    assert bucket == "bucket-under-test"
    assert s3.meta.endpoint_url == "https://acct.r2.cloudflarestorage.com"


def test_one_client_per_thread(configured):
    """upload_page_images runs an 8-thread pool: same client within a thread,
    never shared across threads."""
    mine = pb.pages_bucket().s3
    assert pb.pages_bucket().s3 is mine

    theirs: list[object] = []
    t = threading.Thread(target=lambda: theirs.append(pb.pages_bucket().s3))
    t.start()
    t.join()
    assert theirs[0] is not mine


def test_overrides_are_not_cached(configured):
    """A client built from a candidate token must not become the ambient one
    -- check_r2_token deliberately tests credentials that are not in .env."""
    ambient = pb.pages_bucket().s3
    other = pb.pages_bucket(key_id="candidate", secret="pair", max_attempts=1).s3
    assert other is not ambient
    assert pb.pages_bucket().s3 is ambient


def test_half_a_credential_pair_is_refused(configured):
    with pytest.raises(SystemExit):
        pb.pages_bucket(key_id="candidate")


def test_unconfigured_names_every_missing_variable(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit) as exc:
        pb.pages_bucket()
    assert all(name in str(exc.value) for name in ENV)


def test_bucket_alone_is_enough_to_fail(monkeypatch):
    """The web app's failure mode: credentials present, bucket name forgotten."""
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("R2_BUCKET_PAGES")
    with pytest.raises(SystemExit) as exc:
        pb.pages_bucket(key_id="k", secret="s")
    assert "R2_BUCKET_PAGES" in str(exc.value)
    assert os.environ.get("R2_BUCKET_PAGES") is None
