# Workflow: archive originals, and prove you can get them back

**Objective.** Keep an offsite, client-side-encrypted copy of every original,
and verify by restoring it — an encrypted backup you have never restored is
not a backup.

## Tools

| | |
|---|---|
| `tools/archive_original.py` | called by the ingest stage runner: files locally, pushes encrypted |
| `tools/archive_backfill.py` | fills the gap for documents ingested before R2 existed |
| `tools/fetch_original.py` | restores one document, hash-verified |

## Inputs

- `private/rclone.conf` — the R2 remote plus the `crypt` wrapper. Gitignored.
  It holds the crypt passwords in rclone's obscured form, which is reversible
  and therefore **not** encryption: treat the file as the key material it is.
  The plaintext passwords belong in a password manager and nowhere on disk —
  lose them and the archive is permanently unreadable, which is what
  client-side encryption means.
- `.env` — `SOURCE_DIR`, `RCLONE_CONFIG`, `RCLONE_REMOTE`. These were missing
  for a while and both `archive_original` and `fetch_original` degrade quietly
  to local-only when they are unset, so check them before concluding that
  something did not reach R2.

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

## Getting one document back

```sh
./.venv/bin/python -m tools.fetch_original <slug> [--out DIR] [--remote-only]
```

Tries `SOURCE_DIR` first (no key, no network), falls back to the crypt remote,
and verifies SHA-256 against the database either way — a restore that is not
hash-checked is a guess. `--remote-only` skips the local copy, which is the
only variant that actually exercises the archive; the default will happily
succeed forever while R2 is broken. Every run writes an `audit_log` row.

**Why this is a CLI tool and not a route in the web app.** Decrypting a crypt
object needs the crypt password, so a download route would put that password
in the Vercel environment — the most exposed surface in the system — and the
archive's headline property is that the key does not live next to the data. The
app serves page renders and extracted knowledge; whole originals come back
through this tool, run by someone who already holds the config. Reversing that
decision means accepting that a compromise of the web app yields the plaintext
corpus.

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
