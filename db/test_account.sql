-- The one row every write test depends on, and the step that was missing from
-- the documented setup.
--
-- RLS is ENABLE + FORCE across the corpus tables and the policies key on
-- has_access(), which does its own SELECT against allowed_account. With no
-- matching row, `INSERT INTO source_document` fails with
--
--   new row violates row-level security policy for table "source_document"
--
-- which reads like a grant problem and is not one. tests/conftest.py sets
-- ARCHIVE_ACCOUNT_ID to this id; the row has to exist for that to mean
-- anything. Deliberately NOT in db/seed.sql -- seed.sql runs against real
-- deployments too, and a known-id owner account is not something to create
-- there.
INSERT INTO allowed_account (id, email, role, status)
VALUES ('00000000-0000-0000-0000-0000000000aa', 'dev@local', 'owner', 'active')
ON CONFLICT (id) DO NOTHING;
