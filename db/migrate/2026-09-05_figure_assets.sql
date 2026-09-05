-- Figure assets: give source_asset an identity, and room to record who
-- described it.
--
-- Applied to: local dev, Neon. See workflows/provision_database.md.
--
-- Why an identity. source_asset had no natural key, so tools/ingest_document.py
-- rebuilt it wholesale on every run of the `pages` stage:
--     DELETE FROM source_asset WHERE page_id = %s
-- which is correct for a table nothing writes to but the extractor, and
-- destructive the moment anything else does. A description that took a model
-- call to produce would be gone on the next re-ingest, silently, with the row
-- re-created empty beside it. The sha256 of the decoded image bytes is the
-- natural key: same bytes, same asset, whatever the extraction run.
--
-- It also gives dedup for free. There are 1,763 asset rows over 1,414 distinct
-- bounding boxes -- the difference is one graphic placed on many pages.

ALTER TABLE source_asset ADD COLUMN IF NOT EXISTS sha256 text;

-- Provenance for generated text. content_status describes *source* fidelity
-- ('real', 'wip', 'lorem', ...) and has no value meaning "a model wrote this",
-- which is a different claim and a more dangerous one to get wrong: a figure
-- description is not something the document says. Recording the model and the
-- time is the minimum that lets a later reader tell the two apart, and lets a
-- re-run target only what an older model produced.
ALTER TABLE source_asset ADD COLUMN IF NOT EXISTS vlm_model text;
ALTER TABLE source_asset ADD COLUMN IF NOT EXISTS vlm_described_at timestamptz;

-- Partial, because the 1,763 rows that predate this column are all NULL and
-- Postgres treats NULLs as distinct anyway. New rows get a real key; old rows
-- keep working until the next ingest gives them one.
CREATE UNIQUE INDEX IF NOT EXISTS source_asset_page_sha
  ON source_asset (page_id, sha256) WHERE sha256 IS NOT NULL;
