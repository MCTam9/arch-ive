# Workflow: keep the repo public and the corpus private

**Objective.** The code is public; the material it indexes is third-party,
copyrighted, and names clients, consultants and individuals. None of that
enters git — ever, including history, filenames, branch names and commit
messages.

## The three gates

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

**3. Policy backstop.** `git add -f` bypasses `.gitignore`, so the hook also
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

## Edge cases

- **Fork PRs get a weaker scan.** Actions secrets are unavailable to forks.
  Acceptable for a solo repo; add a hashed-term fallback list before accepting
  outside contributors.
- **Commit messages are scanned too**, via the `commit-msg` hook. Branch names
  and issue titles are not — those are on you.
- **Prove the gate rather than trusting it.** Attempt a commit with a known
  forbidden term in a file, in a message, and in a filename; assert all three
  are blocked. Re-run this after any change to the scanner.
