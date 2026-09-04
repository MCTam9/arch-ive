# Workflow: keep the repo public and the corpus private

**Objective.** The code is public; the material it indexes is third-party,
copyrighted, and names clients, consultants and individuals. None of that
enters git — ever, including history, filenames, branch names and commit
messages.

## The five gates

**1. `.gitignore`, written before `git init`.** Excludes `private/`, `inbox/`,
`.tmp/`, the source folders, `*.pdf`, `*.xlsx`, `.env*` and `rclone.conf`.
This is the only control that cannot be applied retroactively, which is why it
was commit zero.

**2. The forbidden-term scanner.** `scripts/scan_forbidden.py` reads terms from
`$SCAN_DENYLIST` — `private/denylist.txt` locally, a repository secret in CI.
It scans **content**, not just filenames, and **fails closed**: no denylist,
exit 2. The public repo therefore enforces the rule without ever stating it.

Three match modes, because one does not fit:

| Mode | Use for |
|---|---|
| `ci` | case-insensitive substring — multi-word names |
| `word` | case-insensitive whole word |
| `cs` | case-sensitive whole word — acronyms that collide with code |

**3. Commit identity.** The same `pre-commit` hook refuses any commit whose
author email is not this repo's. A repo-local `user.email` silently overrode
the correct global one and 29 commits were authored and pushed under the wrong
address before anyone noticed — GitHub attributes a commit by its author
email, so the history showed a contributor who had never been invited. Fixing
it meant rewriting every commit and force-pushing. Set
`ARCHIVE_ALLOWED_EMAIL` to commit under a different identity deliberately.

**4. WAT layering.** `scripts/check_wat.py` enforces the architecture in
CLAUDE.md mechanically: every runnable tool is named by a workflow, every
extractor appears in the registry in `workflows/add_extractor.md`, no workflow
points at nothing, execution stays in `tools/` `extractors/` `scripts/`
`web/scripts/` `tests/`, and env files stay in the sanctioned set. Written
because the workflow layer had visibly drifted — deployment and database
provisioning both reached production as tools plus ad-hoc shell with no SOP at
all, so each run was reconstructed from memory. Runs in the hook and in CI.

**5. Policy backstop.** `git add -f` bypasses `.gitignore`, so the hook also
rejects source-material file extensions and blobs over 5 MB regardless of
ignore rules. This was found by red-teaming the gate, not by reasoning about
it — a 21 MB PDF staged cleanly before it existed.

## Steps

```sh
./scripts/setup_hooks.sh                              # once, per clone
python3 scripts/scan_forbidden.py --paths <files>     # before writing
python3 scripts/scan_forbidden.py --staged            # what the hook runs
git log -p | python3 scripts/scan_forbidden.py --stdin   # standing history check
```

## When the hook blocks you

Use the slug. `crib-water`, `framework-vol-e1`, `typology-multifamily`,
`calc-fees`, `org-consult-engineering`. Real names resolve at runtime from the
`organisation` table and `private/documents.yaml`, both outside git.

Do not add a term to the denylist to get past a block — the denylist is the
policy, not the obstacle. If a term genuinely collides with legitimate code
(an acronym that is also a variable name), switch that rule to `cs` mode.

## What CI runs (`.github/workflows/scan.yml`)

Three jobs on every push and PR:

| Job | What it proves |
|---|---|
| `forbidden-terms` | no denylisted term in any tracked file, or anywhere in history |
| `secrets` | gitleaks over the whole repo |
| `tests` | `db/test_schema.sh` plus the pytest suite |

The `tests` job took some setting up and the details are load-bearing:

- **The corpus tests self-skip.** `Excel/`, `PDF/`, `Report - Guidance/` and
  `Table - PDF/` are gitignored, so `CORPUS_PRESENT` is false on a CI checkout
  and ~25 tests report skipped. That is the point — a public runner must never
  have the material — but it means CI covers the synthetic-fixture and
  database tests only. `pytest -ra` prints the skip reasons so a test quietly
  becoming a no-op is visible rather than counted as a pass.
- **`sentence-transformers` is dropped** from the CI install: it pulls torch,
  `tools/embed_chunks.py` imports it lazily, and `tests/test_inbox.py` stubs
  the module outright.
- **`db/test_account.sql` is not optional.** RLS is FORCEd and the policies
  call `has_access()`, so with no matching `allowed_account` row every write
  test fails with *"new row violates row-level security policy"* — which reads
  like a missing GRANT and is not one. This step was missing from the setup
  documented in `tests/conftest.py`, which is precisely how CI first came up
  red against a database that looked correctly provisioned.

## Edge cases

- **Fork PRs get a weaker scan.** Actions secrets are unavailable to forks.
  Acceptable for a solo repo; add a hashed-term fallback list before accepting
  outside contributors.
- **Commit messages are scanned too**, via the `commit-msg` hook. Branch names
  and issue titles are not — those are on you.
- **Prove the gate rather than trusting it.** Attempt a commit with a known
  forbidden term in a file, in a message, and in a filename; assert all three
  are blocked. Re-run this after any change to the scanner.
