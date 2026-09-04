"""Original-file custody: local filing plus an encrypted off-site copy.

`archive(path, sha256, slug) -> str | None` (CONTRACT.md):

  1. Copy the original into SOURCE_DIR, organised by slug, named by content
     hash so a real filename is never written to disk here.
  2. Verify the copy's hash before trusting it.
  3. Push it to Cloudflare R2 through an rclone crypt remote.

Local filing must never be blocked on cloud storage: if rclone or the remote
is unconfigured, this logs it and returns None. The file is already safe on
local disk by that point.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path


def _source_dir() -> Path:
    return Path(os.environ.get("SOURCE_DIR", "~/arch-ive-source")).expanduser()


def sha256_of_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def archive(path: Path, sha256: str, slug: str) -> str | None:
    """File to SOURCE_DIR and push encrypted to R2.

    Returns the r2_key, or None if R2 is unconfigured -- local filing still
    happened and ingest must never be blocked on cloud storage.
    """
    path = Path(path)
    dest_dir = _source_dir() / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{sha256}{path.suffix.lower()}"

    if dest.exists():
        # resume path: already filed locally on a prior run. Trust it only
        # if its content still matches -- otherwise something is wrong and
        # we should not silently push the wrong bytes.
        if sha256_of_file(dest) != sha256:
            raise ValueError(f"archive_original: existing local copy for slug {slug!r} does not match sha256")
    else:
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.copy2(path, tmp)
        copied_hash = sha256_of_file(tmp)
        if copied_hash != sha256:
            tmp.unlink(missing_ok=True)
            raise ValueError(f"archive_original: copy hash mismatch for slug {slug!r}")
        tmp.replace(dest)

    return _push_to_r2(dest, sha256, slug)


def _rclone_config() -> Path | None:
    raw = os.environ.get("RCLONE_CONFIG")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.exists() else None


def _push_to_r2(dest: Path, sha256: str, slug: str) -> str | None:
    remote = os.environ.get("RCLONE_REMOTE")
    config = _rclone_config()
    if not remote or config is None:
        print(f"archive_original: R2 unconfigured, local-only for slug {slug!r}")
        return None
    if shutil.which("rclone") is None:
        print(f"archive_original: rclone binary not found, local-only for slug {slug!r}")
        return None

    r2_key = f"{slug}/{sha256}{dest.suffix}"
    remote_target = f"{remote}{r2_key}"

    push = subprocess.run(
        ["rclone", "--config", str(config), "copyto", str(dest), remote_target],
        capture_output=True,
        text=True,
    )
    if push.returncode != 0:
        print(f"archive_original: rclone push failed for slug {slug!r}: {push.stderr.strip()[:200]}")
        return None

    # read back the pushed object's size to confirm the push actually landed
    check = subprocess.run(
        ["rclone", "--config", str(config), "size", remote_target, "--json"],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        print(f"archive_original: rclone size check failed for slug {slug!r}: {check.stderr.strip()[:200]}")
        return None

    try:
        import json

        remote_bytes = json.loads(check.stdout).get("bytes")
    except (ValueError, AttributeError):
        remote_bytes = None

    local_bytes = dest.stat().st_size
    if remote_bytes != local_bytes:
        print(
            f"archive_original: R2 size mismatch for slug {slug!r} "
            f"(local {local_bytes}, remote {remote_bytes}); not trusting the push"
        )
        return None

    return r2_key
