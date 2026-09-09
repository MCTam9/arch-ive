#!/usr/bin/env bash
# Provision a Neon project from the local database: roles, schema, data.
#
# This is the STAND-IT-UP script, not the keep-it-current one. It drops the
# schema and restores a full dump, which is right for a first load or a
# rebuild and much too big for the thing that actually keeps happening --
# local dev moves on and Neon does not. For that, `python3 -m tools.sync_neon
# --push` replaces the corpus rows alone: no DDL, no roles, no passwords,
# nothing to re-copy into Vercel afterwards. Run --check first either way.
#
# Reads NEON_ADMIN_URL from .env (gitignored). Nothing here echoes a
# connection string or a generated password to stdout -- passwords are written
# straight into .env for you to copy into Vercel.
#
# Idempotent enough to re-run: roles are created only if absent, and the
# restore targets an empty schema, so re-running means dropping first.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { echo "no .env -- see the header of this script"; exit 2; }

# Read .env by parsing, not by sourcing. A Neon connection string carries
# `&channel_binding=...`, and `. ./.env` hands that to the shell as a job
# control operator -- the whole file fails to parse and every value is empty.
# `|| true`: a miss must return empty, not fail. Without it `set -e` kills the
# script on the first optional key, before its default can apply.
envget() { grep -E "^$1=" .env | head -1 | cut -d= -f2- || true ; }

NEON_ADMIN_URL=$(envget NEON_ADMIN_URL)
: "${NEON_ADMIN_URL:?set NEON_ADMIN_URL in .env}"

# Neon gives you two endpoints. The -pooler one runs PgBouncer in transaction
# mode, which cannot carry session state or a pg_dump restore, so all DDL and
# the migration go over the DIRECT endpoint. Strip the suffix if it is there.
DIRECT_URL="${NEON_ADMIN_URL/-pooler./.}"
POOLED_HOST=$(printf '%s' "$NEON_ADMIN_URL" | sed -E 's|.*@([^/]+)/.*|\1|')
DIRECT_HOST=$(printf '%s' "$DIRECT_URL"     | sed -E 's|.*@([^/]+)/.*|\1|')

LOCAL_DSN=$(envget DATABASE_URL_LOCAL)
# 5432, not the host-side 55432: pg_dump runs INSIDE the container, where
# localhost is the container and Postgres listens on its own port.
LOCAL_DSN="${LOCAL_DSN:-postgresql://postgres:dev@localhost:5432/postgres}"
# psql/pg_dump run inside the dev container: no local postgres client, and the
# container can reach Neon over the network just as well.
PG() { docker exec -i archive-dev "$@"; }

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/6  version check (pg_dump must not be older than the server)"
PG pg_dump --version
PG psql "$DIRECT_URL" -tAc "select version()" | cut -c1-40

say "2/6  roles"
# Rotating on every run is a trap, not a precaution. A data reload has no
# reason to change a credential, and each rotation leaves Vercel holding a
# password that no longer works until someone copies four strings across by
# hand. On 2026-09-08 nobody did, and the failure surfaced days later as a
# sign-in screen telling the owner their own account was not on the allowlist
# -- the lookup could not connect at all. So: set passwords when a role is
# being CREATED here (there is no other way to learn one), or when --rotate
# says to mean it. Otherwise leave all three alone and say so.
ROTATE=""
# `case`, not `[ ... ] && ROTATE=yes`: under `set -e` that leaves the loop with
# the exit status of the last failed test, which kills the script whenever the
# final argument is not --rotate.
for arg in "$@"; do case "$arg" in --rotate) ROTATE=yes ;; esac; done
MISSING=$(PG psql "$DIRECT_URL" -tAc \
  "select count(*) from (values ('arch_app'),('arch_read'),('arch_auth')) v(n)
    where not exists (select 1 from pg_roles where rolname = v.n)" | tr -d ' ')
if [ "$MISSING" != "0" ] || [ -n "$ROTATE" ]; then
  SET_PW=yes
  APP_PW=$(openssl rand -hex 24); READ_PW=$(openssl rand -hex 24); AUTH_PW=$(openssl rand -hex 24)
else
  SET_PW=""
  echo "  all three roles exist -- passwords left as they are (--rotate to change them)"
fi
PG psql "$DIRECT_URL" -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='arch_app')  THEN CREATE ROLE arch_app  LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='arch_read') THEN CREATE ROLE arch_read LOGIN NOINHERIT; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='arch_auth') THEN CREATE ROLE arch_auth LOGIN NOINHERIT; END IF;
END \$\$;
SQL
if [ -n "$SET_PW" ]; then
  PG psql "$DIRECT_URL" -v ON_ERROR_STOP=1 -q <<SQL
ALTER ROLE arch_app  PASSWORD '$APP_PW';
ALTER ROLE arch_read PASSWORD '$READ_PW';
ALTER ROLE arch_auth PASSWORD '$AUTH_PW';
SQL
fi

say "3/6  who may sign in -- saved before the reset, restored after"
# `allowed_account` and `audit_log` belong to the ENVIRONMENT, not to a corpus
# snapshot. Everything else here is "make Neon look like local dev", and for
# those two that is exactly wrong: local dev's allowlist is one placeholder row
# (`dev@local`, no google_sub, unusable for sign-in), so restoring it over
# production replaces the real accounts with an account nobody can log in as.
# That happened on 2026-09-08 and locked the owner out of their own deployment.
# The audit_log goes with it because the record of who was granted access is
# the one thing that makes a lost allowlist recoverable, and it was overwritten
# in the same pass, so it could not.
#
# Excluding them from the dump is not enough on its own: DROP SCHEMA below
# destroys the target's rows before the restore runs. They have to be carried
# across it.
# NOT pg_dump. The container's pg_dump is 17.11 and Neon is 18.6, and pg_dump
# refuses to read a server newer than itself ("aborting because of server
# version mismatch"). That asymmetry is easy to miss because the restore
# direction below is fine -- a 17 dump loads into an 18 server happily. Reading
# BACK off the target needs something without a version gate, and psql's \copy
# is just a query.
ACCESS_DIR=$(mktemp -d -t arch_access) || exit 1
# One trap for both temporaries. `trap ... EXIT` REPLACES the previous handler
# rather than adding to it, so the .env rewrite's trap further down used to
# silently cancel this one and leave a directory of email addresses in /tmp.
ENV_TMP=".env.tmp.$$"
trap 'rm -rf "$ACCESS_DIR"; rm -f "$ENV_TMP"' EXIT
ACCESS_TABLES="allowed_account audit_log"
SAVED_ANY=""
for t in $ACCESS_TABLES; do
  if ! PG psql "$DIRECT_URL" -tAc "select to_regclass('public.$t') is not null" | grep -q '^t$'; then
    echo "  no $t on the target yet -- first load, nothing to carry"
    continue
  fi
  # Column list read from the target, not hardcoded, so a schema change does
  # not silently shift values into the wrong columns on the way back.
  cols=$(PG psql "$DIRECT_URL" -tAc \
    "select string_agg(quote_ident(column_name), ',' order by ordinal_position)
       from information_schema.columns
      where table_schema='public' and table_name='$t'")
  echo "$cols" > "$ACCESS_DIR/$t.cols"
  PG psql "$DIRECT_URL" -v ON_ERROR_STOP=1 -q \
     -c "\copy (select $cols from $t) to stdout" > "$ACCESS_DIR/$t.tsv"
  echo "  saved $(wc -l < "$ACCESS_DIR/$t.tsv" | tr -d ' ') row(s) from $t"
  SAVED_ANY=yes
done

say "4/6  schema + data"
# Reset the target explicitly instead of using pg_dump --clean. That flag
# emits CREATE OR REPLACE VIEW stubs in its cleanup section which reference
# enum types before creating them -- harmless against a populated database,
# fatal against an empty one, which is exactly the case here.
PG psql "$DIRECT_URL" -v ON_ERROR_STOP=1 -q -c \
  'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;'
# Full dump rather than schema.sql + a data-only pass: --data-only gives no
# guarantee of foreign-key-safe ordering, and --disable-triggers needs a
# superuser Neon does not hand out. Roles already exist above, which is what
# the RLS policies (TO arch_auth) need in order to restore.
#
# --exclude-table-data, not --exclude-table: the tables must still be CREATEd
# here, or the app has nowhere to look them up. Only local dev's ROWS are
# unwanted.
PG pg_dump "$LOCAL_DSN" --no-owner --no-privileges \
    --exclude-table-data=allowed_account --exclude-table-data=audit_log \
  | PG psql "$DIRECT_URL" -v ON_ERROR_STOP=1 -q

if [ -n "$SAVED_ANY" ]; then
  for t in $ACCESS_TABLES; do
    [ -s "$ACCESS_DIR/$t.tsv" ] || continue
    PG psql "$DIRECT_URL" -v ON_ERROR_STOP=1 -q \
       -c "\copy $t ($(cat "$ACCESS_DIR/$t.cols")) from stdin" < "$ACCESS_DIR/$t.tsv"
  done
  echo "  restored $(PG psql "$DIRECT_URL" -tAc 'select count(*) from allowed_account' | tr -d ' ') allowlist row(s)"
fi
if [ "$(PG psql "$DIRECT_URL" -tAc 'select count(*) from allowed_account' | tr -d ' ')" = "0" ]; then
  # A load onto a target that had no allowlist leaves one with no allowlist.
  # Say so: `tools/manage_allowlist.py add` is the only thing that fixes it,
  # and finding out at the sign-in screen is a bad time to learn.
  echo "  NOTE: no allowlist rows on the target -- nobody can sign in until"
  echo "        ADMIN_DATABASE_URL=... python -m tools.manage_allowlist add <email> --role owner"
fi

say "5/6  grants and policies for the three roles"
PG psql "$DIRECT_URL" -v ON_ERROR_STOP=1 -q < db/roles.sql
PG psql "$DIRECT_URL" -v ON_ERROR_STOP=1 -q <<'SQL'
GRANT USAGE ON SCHEMA public TO arch_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO arch_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO arch_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO arch_app;
SQL

say "6/6  verify against the local corpus"
for t in source_document source_page doc_node knowledge_item citation chunk item_term; do
  L=$(PG psql "$LOCAL_DSN" -tAc "set app.account_id='00000000-0000-0000-0000-0000000000aa'; select count(*) from $t" | tail -1)
  R=$(PG psql "$DIRECT_URL" -tAc "set app.account_id='00000000-0000-0000-0000-0000000000aa'; select count(*) from $t" | tail -1)
  [ "$L" = "$R" ] && printf "  ok    %-18s %s\n" "$t" "$R" || printf "  FAIL  %-18s local=%s neon=%s\n" "$t" "$L" "$R"
done
# allowed_account and audit_log are deliberately absent from that list: they are
# the two tables this script is supposed to leave DIFFERENT from local dev, so
# comparing them would report the correct outcome as a failure.
printf "  kept  %-18s %s allowlist row(s) (not compared -- environment, not corpus)\n" \
  "allowed_account" "$(PG psql "$DIRECT_URL" -tAc 'select count(*) from allowed_account' | tr -d ' ')"
# The row counts above cover no VIEWS, so they cannot tell you the view layer
# survived the reset. Run ./db/test_schema.sh --existing for that.

DBN=$(printf '%s' "$DIRECT_URL" | sed -E 's|.*/([^?]+)(\?.*)?$|\1|')

# Drop any earlier copy of the four keys about to be written. This used to
# append unconditionally, which is a slow trap on the SECOND run: the passwords
# above are freshly generated every time, so .env ends up holding a dead
# credential and a live one under the same name -- and both readers take the
# FIRST match (tools/env.py sets a key only `if key not in os.environ`;
# envget() here is `grep | head -1`). Every Python tool pointed at Neon would
# then authenticate with the password this run just rotated away, and say so as
# an authentication failure with nothing to suggest the cause.
#
# Written via a temp file and an atomic mv, so an interrupted rewrite cannot
# leave .env truncated -- there is no other copy of these credentials anywhere.
# The temp name matches .gitignore's `.env.*` so it can never be committed, and
# the trap removes it on any exit, because it would otherwise fail
# scripts/check_wat.py, which allows exactly five env files by name.
if [ -z "$SET_PW" ]; then
  say "done -- corpus reloaded; .env untouched because no password changed"
  echo "  the four NEON_* strings already in .env and in Vercel still work"
  exit 0
fi

if [ -f .env ]; then
  grep -vE '^(NEON_DATABASE_URL|NEON_DATABASE_URL_READONLY|NEON_AUTH_DB_URL|NEON_DIRECT_URL_ARCH_APP)=' .env > "$ENV_TMP" || true
  mv "$ENV_TMP" .env
fi
{
  echo ""
  echo "# generated by scripts/load_neon.sh -- copy these into Vercel"
  # The web app sets app.account_id with SET LOCAL inside a transaction, which
  # is safe through the pooler. tools/db.py uses session-scoped set_config, so
  # the Python side must NOT go through it -- hence two shapes here.
  echo "NEON_DATABASE_URL=postgresql://arch_app:$APP_PW@$POOLED_HOST/$DBN?sslmode=require"
  echo "NEON_DATABASE_URL_READONLY=postgresql://arch_read:$READ_PW@$POOLED_HOST/$DBN?sslmode=require"
  echo "NEON_AUTH_DB_URL=postgresql://arch_auth:$AUTH_PW@$POOLED_HOST/$DBN?sslmode=require"
  echo "NEON_DIRECT_URL_ARCH_APP=postgresql://arch_app:$APP_PW@$DIRECT_HOST/$DBN?sslmode=require"
} >> .env
say "done -- four connection strings written to .env (gitignored), replacing any earlier pair"
