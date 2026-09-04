# Workflow: archive originals, and prove you can get them back

**Objective.** Keep an offsite, client-side-encrypted copy of every original,
and verify by restoring it — an encrypted backup you have never restored is
not a backup.

## Inputs

- `private/rclone.conf` — the R2 remote plus the `crypt` wrapper. Gitignored.
- `private/rclone-passwords.txt` — the plaintext crypt passwords. **These
  belong in a password manager.** Lose them and the archive is permanently
  unreadable; that is what client-side encryption means.
- `.env` — `SOURCE_DIR`, `RCLONE_CONFIG`, `RCLONE_REMOTE`.

## Archiving

New documents are archived by the ingest pipeline, early and deliberately: the
original is filed and pushed as soon as it has an identity, so a later
extraction failure can never lose the file. If R2 is unconfigured the stage
logs and skips rather than failing — ingestion must never block on a bucket.

Documents ingested before R2 existed have no offsite copy. Fill that gap with:

```sh
./.venv/bin/python -m tools.archive_backfill
```

Idempotent: anything already carrying an `r2_key` is skipped.

## The restore drill — run it, do not assume it

From a scratch directory, with only the config and the token:

```sh
rclone --config private/rclone.conf copy archive-crypt: /tmp/restore-drill
```

Then assert every restored file's SHA-256 matches `source_document.sha256`.
Hash equality is the only acceptable evidence: a file that downloads and opens
can still be truncated or silently re-encoded.

Also check the bucket listing shows nothing:

```sh
rclone --config private/rclone.conf ls r2:<bucket>
```

Every key must be opaque. `filename_encryption = standard` is not decoration —
the originals' filenames carry client and project names, and a bucket listing
would otherwise leak exactly what the repo is careful never to publish.

## Known-good baseline (recorded so a later drift is visible)

- 14 documents, 256 MB, full restore in ~43 s over a domestic connection.
- The largest single document, ~194 MB, restores in ~33 s.
- All 14 hash-verified against the database, 0 mismatches, 0 unmatched files.

## Edge cases

- **R2's egress is free**, which is what makes re-ingesting the whole corpus
  after an extractor improvement cost nothing. Use it.
- **Two independent copies exist**: the encrypted bucket, and the plaintext
  working copy under `SOURCE_DIR`. One needs the password, the other does not.
  Do not let both live on the same disk.
- `no_check_bucket = true` is set because the scoped API token can write
  objects but cannot list or create buckets. Without it every operation fails
  on a permission check that has nothing to do with the transfer.
