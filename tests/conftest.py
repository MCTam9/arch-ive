"""Point every test at a throwaway database.

The dev database holds the real ingested corpus. Test fixtures create and
delete documents using the same slugs the corpus uses, so sharing one database
means a test run silently deletes real rows -- which is exactly what happened
before this file existed. tools.db reads DATABASE_URL on each call, so setting
it here, before any test imports a tool, is enough to redirect everything.

Create the database once with:
    psql "$ADMIN_URL" -c 'CREATE DATABASE arch_test'
    psql "$ADMIN_URL/arch_test" -f db/schema.sql -f db/seed.sql \
        -f db/seed_synonyms.sql -f db/test_account.sql

db/test_account.sql is not optional. RLS is FORCEd on the corpus tables and
the policies call has_access(), which reads allowed_account -- so without the
row whose id matches ARCHIVE_ACCOUNT_ID below, every write test fails with
"new row violates row-level security policy", which looks like a missing
GRANT and is not one. This docstring used to omit that step, which is exactly
how CI first came up red.
"""
from __future__ import annotations

import os

TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://arch_app:dev@localhost:55432/arch_test"
)

os.environ["DATABASE_URL"] = TEST_DSN
os.environ.setdefault("ARCHIVE_ACCOUNT_ID", "00000000-0000-0000-0000-0000000000aa")
