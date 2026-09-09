-- Drop the duplicate smart-city rating ladder.
--
-- Applied to: local dev, arch_test, Neon (2026-09-09, on the direct endpoint
--   with -pooler stripped). Re-applied to each afterwards to confirm the
--   second run is a notice and no rows.
--
-- One four-rung ladder (None / Minimal / Significant / Transformational) exists
-- twice in rating_scale. `smart-city-contribution` is seeded by db/seed.sql and
-- is the shared vocabulary: the twelve rating_level_crosswalk rows that let
-- 'Exemplar' compare with 'Transformational' are keyed on it, and so is
-- RATING_LEVEL_ORDINAL in tools/classify_facets.py. `smart-city-alignment-ladder`
-- was appended at extraction time by extractors/smart_city.py, carrying the same
-- four rungs under a second slug with ordinals starting at 0 instead of 1, and
-- nothing has ever referenced it — no requirement, no framework, no crosswalk
-- row. This deletes it. extractors/smart_city.py no longer mints it, so it does
-- not come back on the next re-extract.
--
-- Why a migration and not just the extractor fix: `_upsert_rating_scales` in
-- tools/write_extraction.py is INSERT ... ON CONFLICT (slug) DO UPDATE and never
-- deletes. A scale an extractor has stopped emitting simply stays where it is,
-- through any number of re-extracts, so removing one is always an explicit act.
--
-- Nothing changes in db/schema.sql. schema.sql carries no INSERT at all — the
-- rows deleted here were minted at extraction time, not seeded — so a database
-- built fresh from schema.sql + seed.sql has never had them and there is no
-- shape for the schema to be brought back into step with. The rule in
-- workflows/provision_database.md is about structure; this file is data.
--
-- Shapes used here, and why:
--   * Guarded, not unconditional. The delete only runs if no requirement and no
--     crosswalk row references a level of this scale. rating_level's FK to
--     rating_scale is ON DELETE CASCADE and rating_level_crosswalk's FKs to
--     rating_level are too, so an unguarded delete would take referencing rows
--     with it silently — exactly what "never use CASCADE" exists to prevent.
--     The corpus has zero of each today; the guard is for the corpus this is
--     applied to, which may not be that one. If it finds any, it does nothing
--     and says so, and the scale stays.
--   * Children before parents. rating_level rows are deleted explicitly first,
--     so the scale's own delete has nothing left to cascade to.
--   * Re-appliable: the whole block is conditional on the scale still existing,
--     so a second run is a notice and no rows.

BEGIN;

DO $$
DECLARE
  v_scale_id     uuid;
  v_requirements int;
  v_crosswalks   int;
  v_levels       int;
BEGIN
  -- rating_scale is ENABLE + FORCE ROW LEVEL SECURITY (db/schema.sql), so a
  -- connection with no app.account_id set sees an empty table rather than an
  -- error — and this migration would report "already absent" while changing
  -- nothing. Every database this is applied to is seeded, so an empty
  -- rating_scale means the rows are hidden, not missing. Fail loudly instead.
  IF NOT EXISTS (SELECT 1 FROM rating_scale) THEN
    RAISE EXCEPTION 'rating_scale is empty: either RLS is hiding it (run as a '
                    'role that bypasses RLS, or SET LOCAL app.account_id first) '
                    'or db/seed.sql has not been applied to this database';
  END IF;

  SELECT id INTO v_scale_id
    FROM rating_scale
   WHERE slug = 'smart-city-alignment-ladder';

  IF v_scale_id IS NULL THEN
    RAISE NOTICE 'smart-city-alignment-ladder is already gone; nothing to do';
    RETURN;
  END IF;

  SELECT count(*) INTO v_requirements
    FROM requirement r
    JOIN rating_level l ON l.id = r.rating_level_id
   WHERE l.scale_id = v_scale_id;

  SELECT count(*) INTO v_crosswalks
    FROM rating_level_crosswalk x
    JOIN rating_level l ON l.id IN (x.from_level_id, x.to_level_id)
   WHERE l.scale_id = v_scale_id;

  IF v_requirements > 0 OR v_crosswalks > 0 THEN
    RAISE NOTICE 'smart-city-alignment-ladder is referenced by % requirement(s) '
                 'and % crosswalk row(s); leaving it in place. It is no longer a '
                 'duplicate nobody uses, so decide where those rows should point '
                 'before dropping it.', v_requirements, v_crosswalks;
    RETURN;
  END IF;

  DELETE FROM rating_level WHERE scale_id = v_scale_id;
  GET DIAGNOSTICS v_levels = ROW_COUNT;
  DELETE FROM rating_scale WHERE id = v_scale_id;

  RAISE NOTICE 'dropped smart-city-alignment-ladder and its % rating_level row(s)',
               v_levels;
END $$;

COMMIT;
