#!/usr/bin/env bash
# Applies schema.sql to a throwaway Postgres and asserts the access rules hold.
#
# Exists because RLS failed silently the first time it was written: views
# default to security_definer, and a RETURNS-composite function yields one row
# of NULLs rather than none. Both looked fine and neither was.
#
#   ./db/test_schema.sh            # docker (pgvector/pgvector:pg17)
#   DB=postgres://... ./db/test_schema.sh --existing
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/opt/libpq/bin:$PATH"

CONTAINER=archive-pg-test
PORT=${PORT:-55433}
OWNED=0

if [ "${1:-}" != "--existing" ]; then
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$CONTAINER" -e POSTGRES_PASSWORD=test \
    -p "$PORT":5432 pgvector/pgvector:pg17 >/dev/null
  OWNED=1
  DB="postgresql://postgres:test@localhost:$PORT/postgres"
  for _ in $(seq 1 30); do pg_isready -h localhost -p "$PORT" -q && break; sleep 1; done
fi
cleanup() { [ "$OWNED" = 1 ] && docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

fail=0
check() { # name actual expected
  if [ "$2" = "$3" ]; then printf '  ok    %-42s %s\n' "$1" "$2"
  else printf '  FAIL  %-42s got %s want %s\n' "$1" "$2" "$3"; fail=1; fi
}

psql "$DB" -v ON_ERROR_STOP=1 -q -f db/schema.sql
echo "schema applied"

psql "$DB" -v ON_ERROR_STOP=1 -q <<'SQL'
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='arch_app')
  THEN CREATE ROLE arch_app LOGIN PASSWORD 'test'; END IF; END $$;
GRANT USAGE ON SCHEMA public TO arch_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO arch_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO arch_app;
INSERT INTO allowed_account (id,email,role) VALUES
 ('11111111-1111-1111-1111-111111111111','owner@example.com','owner'),
 ('22222222-2222-2222-2222-222222222222','reader@example.com','reader'),
 ('33333333-3333-3333-3333-333333333333','revoked@example.com','reader');
UPDATE allowed_account SET status='revoked' WHERE email='revoked@example.com';
INSERT INTO source_document (id,slug,doc_kind,sha256,page_count)
 VALUES ('aaaaaaaa-0000-0000-0000-000000000001','crib-embodied-carbon','crib_sheet',repeat('a',64),2);
INSERT INTO unit (id,symbol) VALUES ('kgco2e_m2_gia','kgCO2e/m2GIA');
INSERT INTO metric (id,name,default_unit_id) VALUES
 ('upfront_embodied_carbon','Upfront Embodied Carbon','kgco2e_m2_gia');
INSERT INTO knowledge_item (id,item_type,document_id,title)
 VALUES ('bbbbbbbb-0000-0000-0000-000000000001','benchmark','aaaaaaaa-0000-0000-0000-000000000001','Flats 2030');
INSERT INTO benchmark (knowledge_item_id,metric_id,value_numeric,value_text,unit_id,
                       comparator,building_use_id,target_year,standard_id)
 VALUES ('bbbbbbbb-0000-0000-0000-000000000001','upfront_embodied_carbon',380,'380',
         'kgco2e_m2_gia','lte','residential_flats',2030,'uknzcbs');
INSERT INTO citation (knowledge_item_id,document_id,page_index)
 VALUES ('bbbbbbbb-0000-0000-0000-000000000001','aaaaaaaa-0000-0000-0000-000000000001',1);
SQL

APP="postgresql://arch_app:test@localhost:$PORT/postgres"
q() { psql "$APP" -t -A -q -c "$1" 2>&1 | tail -1; }
as() { psql "$APP" -t -A -q -c "set app.account_id='$1';" -c "$2" 2>&1 | tail -1; }
OWNER=11111111-1111-1111-1111-111111111111
READER=22222222-2222-2222-2222-222222222222
GONE=33333333-3333-3333-3333-333333333333

echo "structure"
# Counted relatively, not against a hardcoded number: a hardcoded count goes
# stale on the next schema change and gets bumped rather than investigated.
# The property that matters is that NOTHING is left out.
#
# A view without security_invoker bypasses RLS entirely and serves every row
# to anonymous callers. That happened here; this is what catches it.
check "views all invoker" \
  "$(psql "$DB" -t -A -c "select count(*) from information_schema.views v where v.table_schema='public' and not exists (select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='v' and c.relname=v.table_name and 'security_invoker=true'=any(c.reloptions))")" 0


# Every table holding corpus-derived content must have RLS. The exemptions are
# generic reference data seeded from public standards, not from the corpus --
# list them explicitly so adding a table without a policy fails loudly.
check "rls on content tables" \
  "$(psql "$DB" -t -A -c "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r' and not c.relrowsecurity and c.relname not in ('stage_crosswalk','rating_level_crosswalk')")" 0

# The retrieval-policy functions must run as the caller. item_has_term reads
# item_term and taxonomy_term through a view, so SECURITY DEFINER on it would
# hand an anonymous connection the whole tagging graph and, worse, would make
# every facet filter built on it bypass RLS silently.
check "policy fns not definer" \
  "$(psql "$DB" -t -A -c "select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.proname in ('is_placeholder_status','item_has_term') and p.prosecdef")" 0

echo "access"
check "anon base table"   "$(q 'select count(*) from benchmark;')" 0
check "anon view"         "$(q 'select count(*) from v_benchmark;')" 0
# The retrieval surfaces are what an agent and the web read. If one of them
# lost security_invoker these would serve the entire corpus to a connection
# with no account set at all -- which is the exact failure this file was
# written for, one view further down the stack.
check "anon retrievable chunk" "$(q 'select count(*) from v_retrievable_chunk;')" 0
check "anon retrievable item"  "$(q 'select count(*) from v_retrievable_item;')" 0
check "anon term subtree"      "$(q 'select count(*) from v_item_term_subtree;')" 0
# LEFT JOIN from taxonomy_term, so an escaping row here shows up as a term with
# a count rather than as nothing -- count the rows, not the sum.
check "anon term counts"       "$(q 'select count(*) from v_term_item_count;')" 0
# Non-strict on purpose: a page or figure chunk passes NULL and needs false
# back, or the web's suppressed-count arithmetic goes NULL and reports 0.
check "item_has_term null item" "$(q "select item_has_term(NULL,'x')::text;")" false
check "revoked base"      "$(as $GONE 'select count(*) from benchmark;')" 0
check "revoked view"      "$(as $GONE 'select count(*) from v_benchmark;')" 0
check "reader base"       "$(as $READER 'select count(*) from benchmark;')" 1
check "reader view"       "$(as $READER 'select count(*) from v_benchmark;')" 1
check "reader sees 1 acct" "$(as $READER 'select count(*) from allowed_account;')" 1
check "reader cannot write" \
  "$(as $READER "insert into unit(id,symbol) values('bad','bad');" | grep -c 'row-level security' || true)" 1
# a reader's UPDATE matches no rows rather than erroring: silent, but harmless
check "reader update hits 0 rows" \
  "$(as $READER 'update benchmark set value_numeric=1 returning 1;' | grep -c '^1$' || true)" 0
check "value untouched"   "$(psql "$DB" -t -A -c 'select value_numeric from benchmark;')" 380
check "owner can write"   "$(as $OWNER "insert into unit(id,symbol) values('lpd','l/p/day'); select 'ok';")" ok

echo "data"
check "benchmark via view" \
  "$(as $READER "select value_numeric||' '||unit from v_benchmark where metric_id='upfront_embodied_carbon';")" \
  "380 kgCO2e/m2GIA"
# The two is_placeholder columns on v_benchmark are different claims:
# benchmark.is_placeholder says the printed value was 'X%'; is_placeholder_content
# says the item is lorem/template/wip/draft. Neither is true of this fixture row.
check "benchmark placeholder cols" \
  "$(as $READER "select is_placeholder::text||'/'||is_placeholder_content::text||'/'||has_citation::text from v_benchmark;")" \
  "false/false/true"

[ "$fail" = 0 ] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
