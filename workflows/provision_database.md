# Workflow: provision a database

**Objective.** Stand up a Postgres that the pipeline, the tests and the web app
can all use, with row-level security actually enforced rather than merely
declared.

## Tools

| | |
|---|---|
| `db/schema.sql` | the four layers, RLS policies, ingest job tables |
| `db/seed.sql`, `db/seed_synonyms.sql` | taxonomy, units, metrics, standards |
| `db/test_account.sql` | the one allowlist row the write tests need |
| `db/roles.sql` | `arch_read` (MCP) and `arch_auth` (sign-in lookup) |
| `db/test_schema.sh` | asserts the access rules hold |
| `scripts/load_neon.sh` | migrate a loaded local database to Neon |
| `tools/manage_allowlist.py` | who may sign in |

## Three roles, and why

| Role | Can do | Used by |
|---|---|---|
| `arch_app` | CRUD under RLS, `app.account_id` per request | pipeline, web app |
| `arch_read` | SELECT only | `tools/mcp_server.py` |
| `arch_auth` | SELECT `allowed_account`, UPDATE `last_seen_at` | the sign-in lookup only |

`arch_auth` exists because `allowed_account`'s policy is `id =
current_account_id()` — you can only see your own row — which makes the *first*
lookup impossible for the app role. The obvious workaround is to run that one
query as a superuser. Don't: the web app is the most exposed surface here, and
a superuser DSN in its environment defeats every RLS control in the schema.
The privilege needed is one SELECT on one table.

## Local

```sh
docker run -d --name archive-dev -e POSTGRES_PASSWORD=dev -p 55432:5432 pgvector/pgvector:pg17
psql "$ADMIN_URL" -f db/schema.sql -f db/seed.sql -f db/seed_synonyms.sql
psql "$ADMIN_URL" -f db/roles.sql
psql "$ADMIN_URL" -c "ALTER ROLE arch_read PASSWORD 'dev'; ALTER ROLE arch_auth PASSWORD 'dev'"
```

`db/roles.sql` carries **no PASSWORD clause on purpose**. It used to set `'dev'`
unconditionally, which a managed Postgres rejects outright and which against a
real deployment would silently downgrade a strong password to a known one.
Passwords are per-environment.

## The test database

```sh
psql "$ADMIN_URL" -c 'CREATE DATABASE arch_test'
psql "$ADMIN_URL/arch_test" -f db/schema.sql -f db/seed.sql \
    -f db/seed_synonyms.sql -f db/test_account.sql
```

`db/test_account.sql` is **not optional**. RLS is FORCEd and the policies call
`has_access()`, so with no matching row every write test fails with *"new row
violates row-level security policy"* — which reads like a missing GRANT and is
not one.

Never point the tests at the corpus database. `tests/conftest.py` redirects
both `DATABASE_URL` and `DATABASE_URL_READONLY` for exactly that reason; a
second DSN defaulted anywhere else walks around it, which is how the MCP tests
spent a while running against whatever was listening on the dev port.

## Verify before trusting it

```sh
./db/test_schema.sh                     # docker, throwaway
DB=postgres://... ./db/test_schema.sh --existing
```

The assertions are structural, not counted against a hardcoded number: **no
view may lack `security_invoker`** and **no table may lack RLS** except an
explicit exemption list. A view defaults to `security_definer` and then serves
every row to anonymous callers — that happened here, looked fine, and is what
this catches.

## Neon

```sh
./scripts/load_neon.sh
```

Four failures worth not repeating:

- **The pooled endpoint cannot carry DDL.** Strip `-pooler` from the host for
  migration and for every Python tool. The web app uses the pooled DSN.
- **`SET LOCAL` inside a transaction is pooler-safe; session-scoped
  `set_config(..., false)` is not.** Transaction pooling hands you a different
  backend next statement. This is why `tools/db.py` is direct-only.
- **`. ./.env` breaks on Neon DSNs** — `&channel_binding` is shell syntax.
  Parse the file, do not source it.
- **`pg_dump --clean` emits view stubs that reference enums before creating
  them.** Reset the schema explicitly instead.

Verify by comparing row counts per table, not just that the load exited zero.

## Access

```sh
ADMIN_DATABASE_URL=... python -m tools.manage_allowlist list
ADMIN_DATABASE_URL=... python -m tools.manage_allowlist add someone@example.com --role reader
ADMIN_DATABASE_URL=... python -m tools.manage_allowlist revoke someone@example.com
```

The allowlist is managed **out of band, never by the app role**. The tool
refuses to run as `arch_app`, `arch_read` or `arch_auth`, and writes the change
and its `audit_log` row in one transaction — an access change without an audit
row is worse than a failed one.

Revocation takes effect at the next sign-in check, not instantly for a live
session. If that matters, shorten the session lifetime.
