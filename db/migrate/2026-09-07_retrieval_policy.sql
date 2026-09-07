-- Retrieval policy moves into SQL.
--
-- Applied to: local dev, arch_test. NOT applied to Neon — that changes
-- production and needs an explicit human go-ahead. See
-- workflows/provision_database.md.
--
-- Five rules that decide what a reader may be shown were written by hand in
-- tools/search.py, tools/mcp_server.py and web/lib/queries.ts — two to four
-- copies each, in two languages — and a sixth (exclude review_status
-- 'rejected' from browse and search) was written nowhere. This puts all six in
-- the database so the three adapters cross one seam instead of restating the
-- corpus's rules in three dialects.
--
-- Shapes used here, and why:
--   * CREATE OR REPLACE for the two functions and the four existing views.
--     Appending columns at the end of a select list is the one change REPLACE
--     allows, and it keeps the grants — DROP would take them with it, and
--     db/roles.sql is not re-run on every deploy.
--   * DROP + CREATE for the four new views and for v_search, because removing
--     a column (or creating something for the first time) is impossible with
--     REPLACE. Drops are ordered child-first and there is no CASCADE anywhere:
--     CASCADE here would silently take out anything else that had come to
--     depend on these.
--   * WITH (security_invoker = true) restated on every view rather than
--     trusted to survive REPLACE. RLS is FORCED, and a view that loses
--     security_invoker serves the whole corpus to an unauthenticated
--     connection — that has happened here before (db/test_schema.sh:78).
--
-- v_search is dropped, not replaced. Its array_agg(term_ids) cannot express
-- subtree matching, so it looked like a facility for the filter that matters
-- most and was a trap. v_retrievable_item supersedes it.
--
-- Re-appliable: every statement is CREATE OR REPLACE, DROP IF EXISTS or
-- guarded, so running this twice is a no-op the second time.

BEGIN;

-- ── drops, child-first, no CASCADE ───────────────────────────────────────
-- v_term_item_count reads v_item_term_subtree, so it goes first. The two
-- functions are classic string-bodied SQL, so Postgres records no dependency
-- from them on the views and they do not constrain this order.
DROP VIEW IF EXISTS v_term_item_count;
DROP VIEW IF EXISTS v_item_term_subtree;
DROP VIEW IF EXISTS v_retrievable_chunk;
DROP VIEW IF EXISTS v_retrievable_item;

-- Superseded by v_retrievable_item. tests/test_writer.py read this; it now
-- reads the replacement.
DROP VIEW IF EXISTS v_search;

-- ─────────────────────────────────────────────────────────────────────────
-- Retrieval policy — the rules about what a reader may be shown, in one place
--
-- These were written out by hand in tools/search.py, tools/mcp_server.py and
-- web/lib/queries.ts: two to four copies each, in two languages, and they had
-- drifted (commit fdd3448 fixed subtree matching in Python; 115e851 made the
-- same fix again by hand in TypeScript). All three callers now cross one seam.
--
-- Unconditional policy, baked into the WHERE of the surfaces below:
--   * review_status = 'rejected' is neither browsable nor searchable. It stays
--     visible in the review queue and on the item page, which read the base
--     tables deliberately — a rejected row must still be reviewable.
--   * the page-text floor. A page chunk is a whole page of raw text and one of
--     them is three characters long. Item and figure chunks are composed text
--     and are exempt: 180 item chunks are shorter than 40 characters and are
--     perfectly good answers, so a blanket floor would quietly delete them.
--
-- Conditional policy, exposed as named boolean columns a caller opts into
-- rather than hidden in the WHERE, because the two surfaces disagree on
-- purpose: MCP must never serve placeholder text as fact, and the web shows it
-- labelled so a reviewer can find it.
--   * is_placeholder   — content_status in (lorem, template, wip, draft).
--   * has_citation     — CONTRACT.md: a row with no page to point at is
--                        dropped, not returned with a null citation.
-- ─────────────────────────────────────────────────────────────────────────

-- The four-value set, once. Deliberately NOT strict: a page or figure chunk
-- has no knowledge_item and passes NULL here, and must come back false rather
-- than NULL — a NULL would poison every OR and every count built on it.
CREATE OR REPLACE FUNCTION is_placeholder_status(s content_status) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT coalesce(s IN ('lorem','template','wip','draft'), false)
$$;

-- Every (item, ancestor-or-self term) pair. The only place `<@` is written.
--
-- Topic is an ltree hierarchy, so a filter on a parent term must return the
-- whole subtree; matching term_id exactly gave 'Health & Wellbeing' 17 of its
-- 53 items. Flat taxonomies are unaffected — their subtree is the term.
--
-- No DISTINCT, on purpose. It would block qualifier pushdown into the join and
-- charge every caller for a sort. Callers that need uniqueness say so: EXISTS
-- (item_has_term) or count(DISTINCT ...) (v_term_item_count).
CREATE VIEW v_item_term_subtree WITH (security_invoker = true) AS
SELECT it.knowledge_item_id,
       root.id          AS term_id,          -- the term you would filter on
       root.taxonomy_id,
       tag.id           AS tagged_term_id,   -- the term the item actually carries
       it.weight, it.assigned_by, it.confidence
FROM item_term it
JOIN taxonomy_term tag  ON tag.id = it.term_id
JOIN taxonomy_term root ON tag.path <@ root.path;

-- The predicate form, for a WHERE clause.
--
-- The NULL guard is not decoration. Page and figure chunks carry no
-- knowledge_item, so this is called with NULL constantly, and the web counts
-- what a facet hid with `WHERE NOT ok` — which never fires on a NULL. false,
-- not NULL, is the answer that makes that count right.
CREATE OR REPLACE FUNCTION item_has_term(p_item_id uuid, p_term_id text) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT p_item_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM v_item_term_subtree s
     WHERE s.knowledge_item_id = p_item_id
       AND s.term_id = p_term_id
  )
$$;

-- Facet counts that cannot disagree with the filter, because they are the same
-- subtree join. Replaces a correlated subquery repeated three times in
-- web/lib/queries.ts, each of which had to be kept in step with the filter by
-- hand. Rejected items are excluded here for the same reason they are excluded
-- from browse: a count that promises rows the filter will not return is worse
-- than no count.
CREATE VIEW v_term_item_count WITH (security_invoker = true) AS
SELECT tt.id AS term_id, tt.taxonomy_id, tt.parent_id, tt.code, tt.label,
       tt.path, tt.ordinal,
       count(DISTINCT ki.id)::int AS item_count
FROM taxonomy_term tt
LEFT JOIN v_item_term_subtree s ON s.term_id = tt.id
LEFT JOIN knowledge_item ki ON ki.id = s.knowledge_item_id
                           AND ki.review_status <> 'rejected'
GROUP BY tt.id;

-- The search surface: every chunk a retrieval path is allowed to consider,
-- with the conditional rules attached as columns.
--
-- `is_placeholder` folds in the *page's* status, which is the one behavioural
-- change here. 141 figure-description chunks carry content_status='real' while
-- sitting on a page marked 'wip', and both surfaces served them as real
-- content. A description of a work-in-progress page is work in progress;
-- CONTRACT.md's rule is that such content is flagged, not ingested as fact.
-- MCP therefore loses those 141 from its default results (they come back with
-- include_placeholder=True) and the web stamps them WIP instead of real.
CREATE VIEW v_retrievable_chunk WITH (security_invoker = true) AS
SELECT c.id AS chunk_id,
       c.document_id, c.knowledge_item_id, c.asset_id, c.node_id,
       c.page_from, c.page_to, c.ordinal, c.text, c.tsv, c.embedding,
       c.token_count,
       c.content_status AS chunk_content_status,
       coalesce(ap.content_status, cp.content_status) AS page_content_status,
       d.slug  AS document_slug,
       d.title AS document_title,
       d.doc_kind,
       ki.item_type, ki.title, ki.statement, ki.summary,
       ki.content_status,
       ki.review_status,
       -- What counts as one result on a card: an item, a figure, or a page.
       CASE WHEN c.knowledge_item_id IS NOT NULL THEN 'item'
            WHEN c.asset_id IS NOT NULL          THEN 'figure'
            ELSE 'page' END AS result_kind,
       (is_placeholder_status(c.content_status)
        OR is_placeholder_status(ki.content_status)
        OR is_placeholder_status(coalesce(ap.content_status, cp.content_status))
       ) AS is_placeholder,
       -- Which status to stamp the card with, most specific first; NULL when
       -- the row is real, so a caller can label without re-deriving the rule.
       CASE
         WHEN is_placeholder_status(c.content_status)  THEN c.content_status
         WHEN is_placeholder_status(ki.content_status) THEN ki.content_status
         WHEN is_placeholder_status(coalesce(ap.content_status, cp.content_status))
              THEN coalesce(ap.content_status, cp.content_status)
       END AS placeholder_status,
       (c.page_from IS NOT NULL OR c.page_to IS NOT NULL) AS has_citation
FROM chunk c
JOIN source_document d      ON d.id = c.document_id
LEFT JOIN knowledge_item ki ON ki.id = c.knowledge_item_id
-- A figure's page comes through its asset; anything else's through the page
-- index it was cut from. chunk has no page_id, and (document_id, page_index)
-- is the unique key on source_page, so this is the join that exists.
LEFT JOIN source_asset a    ON a.id = c.asset_id
LEFT JOIN source_page ap    ON ap.id = a.page_id
LEFT JOIN source_page cp    ON cp.document_id = c.document_id
                            AND cp.page_index = c.page_from
-- LEFT JOIN above, so the item predicate has to tolerate the NULL it sees for
-- a page or figure chunk, or the outer join is undone by its own WHERE.
WHERE (ki.id IS NULL OR ki.review_status <> 'rejected')
  AND (c.knowledge_item_id IS NOT NULL OR c.asset_id IS NOT NULL
       OR length(c.text) >= 40);

-- The browse surface. Exposes both `id` and `knowledge_item_id`, the same
-- value, so one predicate string — item_has_term(knowledge_item_id, $1) — is
-- valid against this view and v_retrievable_chunk alike.
CREATE VIEW v_retrievable_item WITH (security_invoker = true) AS
SELECT ki.id,
       ki.id AS knowledge_item_id,
       ki.document_id, ki.node_id,
       ki.item_type, ki.title, ki.statement, ki.summary,
       ki.content_status, ki.review_status,
       d.slug  AS document_slug,
       d.title AS document_title,
       d.doc_kind,
       c.page_index, c.printed_page_label,
       is_placeholder_status(ki.content_status) AS is_placeholder,
       CASE WHEN is_placeholder_status(ki.content_status)
            THEN ki.content_status END AS placeholder_status,
       (c.page_index IS NOT NULL) AS has_citation
FROM knowledge_item ki
JOIN source_document d ON d.id = ki.document_id
-- LATERAL … LIMIT 1, so an item with four citations is still one row. A plain
-- LEFT JOIN on citation multiplies the item by its pages, which is how a
-- browse count comes back larger than the corpus.
LEFT JOIN LATERAL (
  SELECT ct.page_index, ct.printed_page_label
    FROM citation ct
   WHERE ct.knowledge_item_id = ki.id
   ORDER BY ct.page_index NULLS LAST
   LIMIT 1
) c ON true
WHERE ki.review_status <> 'rejected';

CREATE OR REPLACE VIEW v_benchmark WITH (security_invoker = true) AS
SELECT b.knowledge_item_id,
       d.slug              AS document_slug,
       m.id                AS metric_id,
       m.name              AS metric,
       b.value_numeric, b.value_min, b.value_max, b.value_text,
       u.symbol            AS unit,
       b.comparator, b.is_placeholder, b.caveat_text,
       b.building_use_id, b.target_year, b.region_id, b.standard_id,
       ki.content_status, ki.review_status,
       c.page_index, c.printed_page_label,
       -- Appended, not renamed: `is_placeholder` on this view is
       -- benchmark.is_placeholder, a claim about the *value* ('X%' in the
       -- source), and the item page labels it "Placeholder value?". The
       -- content rule is a different claim and gets the name MCP already
       -- emits for it.
       is_placeholder_status(ki.content_status) AS is_placeholder_content,
       (c.page_index IS NOT NULL)               AS has_citation
FROM benchmark b
JOIN knowledge_item ki ON ki.id = b.knowledge_item_id
JOIN source_document d ON d.id = ki.document_id
JOIN metric m          ON m.id = b.metric_id
LEFT JOIN unit u       ON u.id = b.unit_id
LEFT JOIN citation c   ON c.knowledge_item_id = ki.id
WHERE ki.review_status <> 'rejected';

CREATE OR REPLACE VIEW v_requirement_matrix WITH (security_invoker = true) AS
SELECT r.knowledge_item_id,
       f.slug              AS framework_slug,
       cr.code             AS criterion_code,
       cr.title_primary    AS criterion,
       cr.path             AS criterion_path,
       rl.ordinal          AS level_ordinal,
       rl.code             AS level_code,
       rl.name             AS level_name,
       ki.statement,
       r.target_text, r.target_value, u.symbol AS unit, r.comparator,
       r.is_deliverable, r.deliverable_name,
       ki.content_status, ki.review_status,
       d.slug              AS document_slug,
       c.page_index,
       is_placeholder_status(ki.content_status) AS is_placeholder_content,
       (c.page_index IS NOT NULL)               AS has_citation
FROM requirement r
JOIN knowledge_item ki ON ki.id = r.knowledge_item_id
JOIN source_document d ON d.id = ki.document_id
LEFT JOIN criterion cr ON cr.id = r.criterion_id
LEFT JOIN framework f  ON f.id = cr.framework_id
LEFT JOIN rating_level rl ON rl.id = r.rating_level_id
LEFT JOIN unit u       ON u.id = r.unit_id
LEFT JOIN citation c   ON c.knowledge_item_id = ki.id
WHERE ki.review_status <> 'rejected';

-- one row per (requirement, scope) it was reprinted under, so "what does
-- this code require from the Design-Build Contractor" is a single filter
-- rather than a manual join against requirement_scope_applicability.
CREATE OR REPLACE VIEW v_requirement_scope_matrix WITH (security_invoker = true) AS
SELECT r.knowledge_item_id,
       f.slug              AS framework_slug,
       cr.code             AS criterion_code,
       cr.title_primary    AS criterion,
       ki.statement,
       r.target_text       AS canonical_target_text,
       rs.id               AS scope_id,
       rs.code             AS scope_code,
       rs.title            AS scope_title,
       rsa.applies,
       rsa.target_text     AS scope_target_text,
       rsa.note            AS scope_note,
       ki.content_status, ki.review_status,
       d.slug              AS document_slug,
       -- No has_citation: this view joins no citation and never did. A
       -- uniformly-false column would only invite a caller to filter every
       -- scope row away.
       is_placeholder_status(ki.content_status) AS is_placeholder_content
FROM requirement_scope_applicability rsa
JOIN requirement_scope rs ON rs.id = rsa.scope_id
JOIN requirement r        ON r.knowledge_item_id = rsa.knowledge_item_id
JOIN knowledge_item ki    ON ki.id = r.knowledge_item_id
JOIN source_document d    ON d.id = ki.document_id
LEFT JOIN criterion cr    ON cr.id = r.criterion_id
LEFT JOIN framework f     ON f.id = cr.framework_id
WHERE ki.review_status <> 'rejected';

CREATE OR REPLACE VIEW v_template_catalogue WITH (security_invoker = true) AS
SELECT t.knowledge_item_id, t.slug, t.template_kind, t.engine,
       ki.title, d.slug AS document_slug,
       count(tp.id) FILTER (WHERE tp.is_input)  AS input_count,
       count(tp.id) FILTER (WHERE tp.is_output) AS output_count,
       -- content_status appended so list_templates no longer has to re-join
       -- knowledge_item for it. No has_citation: templates are xlsx workbooks
       -- with no page to cite at all, and a column that is false for every row
       -- invites someone to filter the whole catalogue out.
       ki.content_status,
       is_placeholder_status(ki.content_status) AS is_placeholder_content
FROM template t
JOIN knowledge_item ki ON ki.id = t.knowledge_item_id
JOIN source_document d ON d.id = ki.document_id
LEFT JOIN template_parameter tp ON tp.template_id = t.knowledge_item_id
WHERE ki.review_status <> 'rejected'
GROUP BY t.knowledge_item_id, t.slug, t.template_kind, t.engine, ki.title, d.slug,
         ki.content_status;


-- ── grants ───────────────────────────────────────────────────────────────
-- DROP + CREATE loses whatever grants the dropped views carried, and
-- db/roles.sql runs once per database rather than on every deploy — so a
-- database where it has already run needs these back, and one where it has
-- not must not fail here. Guarded on the role existing, per-role, so a cluster
-- with only arch_app still gets what it can use.
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['arch_read','arch_app'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format(
        'GRANT SELECT ON v_item_term_subtree, v_term_item_count, '
        'v_retrievable_chunk, v_retrievable_item TO %I', r);
    END IF;
  END LOOP;
END $$;

COMMIT;
