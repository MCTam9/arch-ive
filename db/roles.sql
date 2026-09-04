-- Read-only Postgres role for the local MCP retrieval server (tools/mcp_server.py).
--
-- Mirrors how arch_app is provisioned (see db/test_schema.sh) minus every
-- write grant. RLS policies key on has_access()/current_account_id(), both
-- STABLE SQL functions that run with the *calling* role's privileges (they
-- are not SECURITY DEFINER), and has_access() does its own SELECT against
-- allowed_account -- so arch_read needs the same SELECT-only visibility into
-- allowed_account that arch_app has. Function EXECUTE privilege is PUBLIC by
-- default in Postgres and schema.sql never revokes it, so no separate grant
-- is needed for current_account_id()/has_access()/can_edit().
--
-- Roles are cluster-wide but grants are per-database, so apply this to every
-- database that needs it:
--   docker exec -e PGPASSWORD=dev archive-dev psql -U postgres -d postgres  -f db/roles.sql
--   docker exec -e PGPASSWORD=dev archive-dev psql -U postgres -d arch_test -f db/roles.sql
--
-- Password is 'dev' to match the existing local convention (arch_app uses
-- the same password against the same dev cluster). Never used outside local
-- dev/test -- there is no public endpoint for this role.

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'arch_read') THEN
    CREATE ROLE arch_read LOGIN PASSWORD 'dev';
  END IF;
END $$;

-- NOINHERIT: arch_read must stand on its own grants, never pick up privilege
-- via membership in some other role later.
ALTER ROLE arch_read NOINHERIT LOGIN PASSWORD 'dev';

DO $$ BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO arch_read', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO arch_read;

-- SELECT only -- on tables and views alike; ALL TABLES matches both.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO arch_read;

-- Covers tables/views another agent adds after this script runs, as long as
-- they're created by the same role (postgres) that owns the rest of the
-- schema -- matching how schema.sql itself is applied.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO arch_read;

-- No INSERT/UPDATE/DELETE grants anywhere, and no sequence grants (a
-- read-only role has no reason to consume nextval()). RLS (ENABLE + FORCE in
-- db/schema.sql) still applies to arch_read as a non-owner role regardless,
-- so this is defence in depth, not the only thing standing between arch_read
-- and a write.


-- ── arch_auth: the sign-in lookup role ───────────────────────────────────
--
-- allowed_account's policy is `USING (id = current_account_id())` -- you can
-- only ever see your own row. Correct for normal operation, but it makes the
-- *first* lookup impossible for the app role: matching an OAuth email to an
-- account id would require already knowing the id.
--
-- The obvious workaround is to do that one query as a superuser. Don't. The
-- web app is the most exposed surface in this system, and a superuser DSN
-- sitting in its environment means any compromise of it -- SSRF, an injected
-- query, a leaked .env -- hands over write access to the entire corpus and
-- defeats every RLS control in db/schema.sql. The privilege needed is one
-- SELECT on one table.
--
-- So: a role that can read the allowlist and touch a timestamp, and nothing
-- else. No SELECT on any other table, no BYPASSRLS, not a superuser.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'arch_auth') THEN
    CREATE ROLE arch_auth LOGIN PASSWORD 'dev' NOINHERIT;
  END IF;
END $$;

DO $$ BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO arch_auth', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO arch_auth;
GRANT SELECT ON allowed_account TO arch_auth;
GRANT UPDATE (last_seen_at) ON allowed_account TO arch_auth;

-- RLS is ENABLE (not FORCE) on allowed_account, so a non-owner role still
-- needs a policy of its own. Scoped TO arch_auth so it cannot widen what any
-- other role sees.
DROP POLICY IF EXISTS allowed_account_auth_lookup ON allowed_account;
CREATE POLICY allowed_account_auth_lookup ON allowed_account
  FOR SELECT TO arch_auth USING (true);

DROP POLICY IF EXISTS allowed_account_auth_touch ON allowed_account;
CREATE POLICY allowed_account_auth_touch ON allowed_account
  FOR UPDATE TO arch_auth USING (true) WITH CHECK (true);
