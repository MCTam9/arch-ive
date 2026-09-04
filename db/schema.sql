-- arch-ive — architecture knowledge base
--
-- Four layers, each independently useful, none discarding the one below:
--   0 Source     bytes and pages, so every claim can be cited
--   1 Structure  the document tree
--   2 Knowledge  typed entities, because the corpus is four different shapes
--   3 Access     facets, search and citations over the top
-- Plus ingest bookkeeping, accounts and row-level security.
--
-- Names of real organisations exist ONLY in `organisation`. Everything else
-- addresses them by id. That is what allows the code repo to be public.

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ─────────────────────────────────────────────────────────────────────────
-- Enums
-- ─────────────────────────────────────────────────────────────────────────

CREATE TYPE org_kind      AS ENUM ('client','owner','consultant','author','standards_body','regulator');
CREATE TYPE doc_kind      AS ENUM ('guideline_report','framework','implementation_plan',
                                   'crib_sheet','deck','calculator','standard','unknown');
CREATE TYPE content_status AS ENUM ('real','draft','wip','lorem','template','mixed');
CREATE TYPE confidentiality AS ENUM ('public','internal','client-confidential');
CREATE TYPE node_kind     AS ENUM ('volume','chapter','section','subsection','slide','panel','sheet','matrix','table');
CREATE TYPE item_type     AS ENUM ('requirement','benchmark','guidance','pattern','template',
                                   'definition','process_step','role');
CREATE TYPE requirement_kind AS ENUM ('graded','compliance','checklist');
CREATE TYPE comparator    AS ENUM ('lt','lte','gt','gte','eq','range','boolean','none');
CREATE TYPE review_status AS ENUM ('pending','approved','edited','rejected');
CREATE TYPE assigned_by   AS ENUM ('rule','model','human');
CREATE TYPE cell_role     AS ENUM ('label','input','calc','output');
CREATE TYPE account_role  AS ENUM ('owner','editor','reader');
CREATE TYPE account_status AS ENUM ('active','revoked');
CREATE TYPE ref_status    AS ENUM ('unresolved','resolved','missing_source');
CREATE TYPE ingest_state  AS ENUM ('discovered','stable','hashed','deduped','classified','registered',
                                   'archived','pages','structured','extracted','enriched','embedded',
                                   'done','failed','needs_review');
CREATE TYPE ingest_lane   AS ENUM ('fast','slow');
CREATE TYPE stage_result  AS ENUM ('running','ok','failed','skipped');

-- ─────────────────────────────────────────────────────────────────────────
-- Layer 0 — Source
-- ─────────────────────────────────────────────────────────────────────────

-- The one and only place real names live.
CREATE TABLE organisation (
  id          text PRIMARY KEY,               -- 'org-consult-engineering'
  name        text NOT NULL,
  aliases     text[] NOT NULL DEFAULT '{}',
  kind        org_kind NOT NULL,
  country     char(2),
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE source_document (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug               text NOT NULL,            -- the only id permitted in code
  title              text,
  doc_kind           doc_kind NOT NULL DEFAULT 'unknown',
  series_ref         text,
  revision           text,
  version_label      text,
  issue_date         date,
  client_org_id      text REFERENCES organisation(id),
  author_org_id      text REFERENCES organisation(id),
  consultant_org_ids text[] NOT NULL DEFAULT '{}',
  confidentiality    confidentiality NOT NULL DEFAULT 'client-confidential',
  content_status     content_status NOT NULL DEFAULT 'real',
  language           text NOT NULL DEFAULT 'en',
  sha256             char(64) NOT NULL,
  size_bytes         bigint,
  page_count         int,
  is_spread_paginated boolean NOT NULL DEFAULT false,
  r2_key             text,                     -- encrypted object key, never a URL
  archived_at        timestamptz,
  original_filename  text,
  supersedes_id      uuid REFERENCES source_document(id) ON DELETE SET NULL,
  is_current         boolean NOT NULL DEFAULT true,
  ingested_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (sha256)
);
CREATE INDEX ON source_document (slug);
-- at most one current revision per slug; superseded rows stay queryable
CREATE UNIQUE INDEX source_document_current_slug ON source_document (slug) WHERE is_current;
CREATE INDEX ON source_document (doc_kind) WHERE is_current;

CREATE TABLE source_page (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id        uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  page_index         int NOT NULL,             -- 1-based PDF page
  printed_page_label text,                     -- '187 / 188' on spread layouts
  width_pt           numeric(8,2),
  height_pt          numeric(8,2),
  text               text,
  char_count         int GENERATED ALWAYS AS (coalesce(length(text), 0)) STORED,
  image_count        int NOT NULL DEFAULT 0,
  vector_op_count    int NOT NULL DEFAULT 0,
  is_image_only      boolean GENERATED ALWAYS AS (coalesce(length(text), 0) < 100) STORED,
  content_status     content_status NOT NULL DEFAULT 'real',
  page_image_key     text,                     -- private Blob key
  UNIQUE (document_id, page_index)
);
CREATE INDEX ON source_page (document_id) WHERE content_status = 'real';

CREATE TABLE source_asset (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  page_id         uuid NOT NULL REFERENCES source_page(id) ON DELETE CASCADE,
  code            text,                        -- 'FIGURE 5.1.2.1'
  caption         text,
  vlm_description text,
  bbox            numeric(9,2)[],              -- [x0,y0,x1,y1]
  image_key       text
);
CREATE INDEX ON source_asset (page_id);

CREATE TABLE spreadsheet_sheet (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  name        text NOT NULL,
  ordinal     int NOT NULL,
  UNIQUE (document_id, name)
);

CREATE TABLE spreadsheet_cell (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sheet_id      uuid NOT NULL REFERENCES spreadsheet_sheet(id) ON DELETE CASCADE,
  ref           text NOT NULL,                 -- 'AO6'
  row_num       int,
  col_num       int,
  value_text    text,
  value_numeric numeric,
  formula       text,                          -- kept: this is what makes it a template
  number_format text,
  role          cell_role,
  UNIQUE (sheet_id, ref)
);
CREATE INDEX ON spreadsheet_cell (sheet_id) WHERE formula IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────
-- Layer 1 — Structure
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE doc_node (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  parent_id   uuid REFERENCES doc_node(id) ON DELETE CASCADE,
  node_kind   node_kind NOT NULL,
  code        text,                            -- '5.1.2', 'RE2.1', '3.4.7'
  title       text,
  title_alt   text,                            -- sheets rename sections between pages
  ordinal     int NOT NULL DEFAULT 0,
  page_from   int,
  page_to     int,
  path        ltree,
  text        text
);
CREATE INDEX ON doc_node USING gist (path);
CREATE INDEX ON doc_node (document_id, ordinal);
CREATE INDEX ON doc_node (document_id, code);

-- ─────────────────────────────────────────────────────────────────────────
-- Layer 2 — Knowledge
--
-- One supertype so facets, embeddings, citations and review all attach in a
-- single place; typed subtables so nothing is flattened into a text blob.
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE knowledge_item (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  item_type             item_type NOT NULL,
  document_id           uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  node_id               uuid REFERENCES doc_node(id) ON DELETE SET NULL,
  title                 text,
  statement             text,
  summary               text,
  content_status        content_status NOT NULL DEFAULT 'real',
  extraction_confidence numeric(3,2) CHECK (extraction_confidence BETWEEN 0 AND 1),
  review_status         review_status NOT NULL DEFAULT 'pending',
  reviewed_by           text,
  reviewed_at           timestamptz,
  extraction_run_id     uuid,
  created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON knowledge_item (document_id, item_type);
CREATE INDEX ON knowledge_item (item_type) WHERE content_status = 'real' AND review_status <> 'rejected';
CREATE INDEX ON knowledge_item (review_status) WHERE review_status = 'pending';

-- ── frameworks, criteria and the graded ladders ──────────────────────────

CREATE TABLE rating_scale (
  id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug   text UNIQUE NOT NULL,                 -- 'crib-levels', 'qsf-targets'
  name   text NOT NULL
);

CREATE TABLE rating_level (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scale_id    uuid NOT NULL REFERENCES rating_scale(id) ON DELETE CASCADE,
  ordinal     int NOT NULL,                    -- 1..n, ascending ambition
  code        text,                            -- 'L1'
  name        text,                            -- 'CTO Contributive' (L1 is unlabelled)
  description text,
  colour      text,                            -- echoes the band colour on the source sheet
  UNIQUE (scale_id, ordinal)
);

-- lets 'Exemplar' compare with 'pioneering' and 'Transformational'
CREATE TABLE rating_level_crosswalk (
  from_level_id uuid NOT NULL REFERENCES rating_level(id) ON DELETE CASCADE,
  to_level_id   uuid NOT NULL REFERENCES rating_level(id) ON DELETE CASCADE,
  equivalence   numeric(3,2) NOT NULL DEFAULT 1.0,
  PRIMARY KEY (from_level_id, to_level_id)
);

CREATE TABLE framework (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug          text UNIQUE NOT NULL,          -- public-safe
  name          text NOT NULL,
  owner_org_id  text REFERENCES organisation(id),
  version       text,
  rating_scale_id uuid REFERENCES rating_scale(id),
  document_id   uuid REFERENCES source_document(id) ON DELETE SET NULL
);

CREATE TABLE criterion (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  framework_id  uuid NOT NULL REFERENCES framework(id) ON DELETE CASCADE,
  parent_id     uuid REFERENCES criterion(id) ON DELETE CASCADE,
  code          text,                          -- 'RE2.1'; stable across title drift
  title_primary text NOT NULL,
  title_alt     text,
  ordinal       int NOT NULL DEFAULT 0,
  path          ltree
);
CREATE INDEX ON criterion USING gist (path);
-- unique, not merely indexed: a code identifies one criterion within a
-- framework, and the writer needs a conflict target to upsert against.
CREATE UNIQUE INDEX criterion_framework_code ON criterion (framework_id, code)
  WHERE code IS NOT NULL;

-- ── units and metrics ────────────────────────────────────────────────────

CREATE TABLE unit (
  id        text PRIMARY KEY,                  -- 'kgco2e_m2_gia'
  symbol    text NOT NULL,                     -- 'kgCO2e/m²GIA'
  dimension text,
  si_factor numeric
);

CREATE TABLE metric (
  id              text PRIMARY KEY,            -- 'eui'
  name            text NOT NULL,
  definition      text,
  default_unit_id text REFERENCES unit(id),
  formula         text,
  higher_is_better boolean
);

-- ── the two requirement shapes, sharing one supertype ────────────────────

CREATE TABLE requirement (
  knowledge_item_id uuid PRIMARY KEY REFERENCES knowledge_item(id) ON DELETE CASCADE,
  requirement_kind  requirement_kind NOT NULL,
  criterion_id      uuid REFERENCES criterion(id) ON DELETE SET NULL,
  rating_level_id   uuid REFERENCES rating_level(id),
  metric_id         text REFERENCES metric(id),
  target_value      numeric,
  target_text       text,                      -- always populated; the value may not parse
  unit_id           text REFERENCES unit(id),
  comparator        comparator NOT NULL DEFAULT 'none',
  is_deliverable    boolean NOT NULL DEFAULT false,
  deliverable_name  text,
  parsed_ok         boolean NOT NULL DEFAULT false
);
CREATE INDEX ON requirement (criterion_id, rating_level_id);
CREATE INDEX ON requirement (metric_id) WHERE metric_id IS NOT NULL;

-- a framework's compliance appendix is often reprinted once per contractor
-- role / tracking context (Concept Design, Design-Build, Main Contractor,
-- a compliance checklist, ...), and a code's applicability marker can
-- legitimately differ between reprints. Modelled as a join so the same
-- requirement stays one row rather than being duplicated N times over with
-- otherwise-identical statement text -- see extractors/compliance_table.py.
CREATE TABLE requirement_scope (
  id           text PRIMARY KEY,             -- 'masterplan-sustainability-design_build_contractor'
  framework_id uuid NOT NULL REFERENCES framework(id) ON DELETE CASCADE,
  code         text,                         -- short slug, stable across title drift
  title        text NOT NULL,                -- verbatim scope/role label from the source
  ordinal      int NOT NULL DEFAULT 0
);
CREATE INDEX ON requirement_scope (framework_id);
-- a code identifies one scope within a framework, and the writer needs a
-- conflict target to upsert against -- same pattern as criterion_framework_code.
CREATE UNIQUE INDEX requirement_scope_framework_code ON requirement_scope (framework_id, code)
  WHERE code IS NOT NULL;

CREATE TABLE requirement_scope_applicability (
  knowledge_item_id uuid NOT NULL REFERENCES knowledge_item(id) ON DELETE CASCADE,
  scope_id          text NOT NULL REFERENCES requirement_scope(id) ON DELETE CASCADE,
  applies           boolean NOT NULL DEFAULT true,
  target_text       text,                    -- verbatim marker for this scope: 'Y','N/A','100',...
  note              text,
  PRIMARY KEY (knowledge_item_id, scope_id)
);
CREATE INDEX ON requirement_scope_applicability (scope_id);

CREATE TABLE benchmark (
  knowledge_item_id uuid PRIMARY KEY REFERENCES knowledge_item(id) ON DELETE CASCADE,
  metric_id         text NOT NULL REFERENCES metric(id),
  value_numeric     numeric,
  value_min         numeric,                   -- '700-800ppm'
  value_max         numeric,
  value_text        text NOT NULL,             -- verbatim, incl. 'X%' and '0.4*'
  unit_id           text REFERENCES unit(id),
  comparator        comparator NOT NULL DEFAULT 'none',
  is_placeholder    boolean NOT NULL DEFAULT false,   -- 'X%', 'Xkm', 'X no of'
  caveat_text       text,                      -- the asterisk footnotes
  building_use_id   text,
  target_year       int,
  region_id         text,
  standard_id       text,
  baseline_relative_pct numeric
);
CREATE INDEX ON benchmark (metric_id, target_year, building_use_id);

-- ── patterns: typologies, prototypes, clusters, comfort tiers ────────────

CREATE TABLE design_variable (
  id       text PRIMARY KEY,                   -- 'access_typology'
  name     text NOT NULL,                      -- 'ACCESS TYPOLOGY'
  document_id uuid REFERENCES source_document(id) ON DELETE SET NULL,
  ordinal  int NOT NULL DEFAULT 0
);

CREATE TABLE design_variable_value (
  id          text PRIMARY KEY,
  variable_id text NOT NULL REFERENCES design_variable(id) ON DELETE CASCADE,
  label       text NOT NULL,                   -- 'Stepped Duplex'
  ordinal     int NOT NULL DEFAULT 0
);

CREATE TABLE pattern (
  knowledge_item_id uuid PRIMARY KEY REFERENCES knowledge_item(id) ON DELETE CASCADE,
  pattern_kind      text NOT NULL,             -- typology|prototype|urban_cluster|unit_type|comfort_tier
  code              text,                      -- 'STH', 'DL', 'PC1', 'LCPA', 'Tier 3'
  name              text NOT NULL,
  parent_pattern_id uuid REFERENCES knowledge_item(id) ON DELETE SET NULL,
  attributes        jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX ON pattern (pattern_kind, code);
CREATE INDEX ON pattern USING gin (attributes);

CREATE TABLE pattern_attribute (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pattern_id  uuid NOT NULL REFERENCES pattern(knowledge_item_id) ON DELETE CASCADE,
  variable_id text NOT NULL REFERENCES design_variable(id),
  value_id    text REFERENCES design_variable_value(id),
  value_text  text
);
-- a pattern may hold several values for one variable, but not the same one twice
CREATE UNIQUE INDEX pattern_attribute_uniq
  ON pattern_attribute (pattern_id, variable_id, coalesce(value_id, value_text, ''));
CREATE INDEX ON pattern_attribute (variable_id, value_id);

-- ── the remaining typed shapes ───────────────────────────────────────────

CREATE TABLE guidance (
  knowledge_item_id uuid PRIMARY KEY REFERENCES knowledge_item(id) ON DELETE CASCADE,
  body_md       text,
  figure_ids    uuid[] NOT NULL DEFAULT '{}',
  legend_tokens text[] NOT NULL DEFAULT '{}',
  disclaimer    text
);

CREATE TABLE definition (
  knowledge_item_id uuid PRIMARY KEY REFERENCES knowledge_item(id) ON DELETE CASCADE,
  term       text NOT NULL,
  definition text NOT NULL,
  category   text                              -- 'TECHNICAL + ADMINISTRATIVE' etc.
);
CREATE INDEX ON definition (lower(term));

CREATE TABLE process_step (
  knowledge_item_id uuid PRIMARY KEY REFERENCES knowledge_item(id) ON DELETE CASCADE,
  code       text,
  ordinal    int,
  gate       text,                             -- 'Pass/Fail'
  responsible_role_id uuid
);

CREATE TABLE role (
  knowledge_item_id uuid PRIMARY KEY REFERENCES knowledge_item(id) ON DELETE CASCADE,
  code        text,                            -- 'SLA','SLO','SLD','SLC','SLS'
  name        text NOT NULL,
  reports_to  text,
  qualifications text
);

-- ── templates: what turns the workbooks into instruments ─────────────────

CREATE TABLE template (
  knowledge_item_id uuid PRIMARY KEY REFERENCES knowledge_item(id) ON DELETE CASCADE,
  template_kind text NOT NULL,                 -- calculator|checklist|matrix|form
  engine        text NOT NULL DEFAULT 'xlsx',
  slug          text UNIQUE NOT NULL
);

CREATE TABLE template_parameter (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  template_id uuid NOT NULL REFERENCES template(knowledge_item_id) ON DELETE CASCADE,
  name        text NOT NULL,                   -- 'mark_up'
  label       text,                            -- 'Mark-up'
  sheet_name  text,
  cell_ref    text,                            -- 'E26'
  data_type   text NOT NULL DEFAULT 'number',
  unit_id     text REFERENCES unit(id),
  default_value text,
  is_input    boolean NOT NULL DEFAULT true,
  is_output   boolean NOT NULL DEFAULT false,
  ordinal     int NOT NULL DEFAULT 0,
  UNIQUE (template_id, name)
);

CREATE TABLE project (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug       text UNIQUE NOT NULL,
  name       text NOT NULL,
  client_org_id text REFERENCES organisation(id),
  location   text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE template_instance (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  template_id uuid NOT NULL REFERENCES template(knowledge_item_id) ON DELETE CASCADE,
  project_id  uuid REFERENCES project(id) ON DELETE CASCADE,
  values      jsonb NOT NULL DEFAULT '{}',
  outputs     jsonb NOT NULL DEFAULT '{}',
  created_by  text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON template_instance (project_id);

-- ─────────────────────────────────────────────────────────────────────────
-- Layer 3 — Access: facets, stages, standards, search, citations
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE taxonomy (
  id    text PRIMARY KEY,                      -- 'topic','scale','building_use',...
  name  text NOT NULL
);

CREATE TABLE taxonomy_term (
  id          text PRIMARY KEY,                -- 'topic.operational_carbon.passive'
  taxonomy_id text NOT NULL REFERENCES taxonomy(id) ON DELETE CASCADE,
  parent_id   text REFERENCES taxonomy_term(id) ON DELETE CASCADE,
  code        text NOT NULL,
  label       text NOT NULL,
  synonyms    text[] NOT NULL DEFAULT '{}',    -- reconciles the corpus's spellings
  path        ltree,
  ordinal     int NOT NULL DEFAULT 0
);
CREATE INDEX ON taxonomy_term USING gist (path);
CREATE INDEX ON taxonomy_term USING gin (synonyms);
CREATE INDEX ON taxonomy_term (taxonomy_id);

-- one polymorphic tagging table: a single join answers any facet combination
CREATE TABLE item_term (
  knowledge_item_id uuid NOT NULL REFERENCES knowledge_item(id) ON DELETE CASCADE,
  term_id           text NOT NULL REFERENCES taxonomy_term(id) ON DELETE CASCADE,
  weight            numeric(3,2) NOT NULL DEFAULT 1.0,
  assigned_by       assigned_by NOT NULL DEFAULT 'model',
  confidence        numeric(3,2),
  PRIMARY KEY (knowledge_item_id, term_id)
);
CREATE INDEX ON item_term (term_id, knowledge_item_id);

-- four native stage vocabularies, one canonical spine
CREATE TABLE stage_scheme (
  id   text PRIMARY KEY,                       -- 'riba_2020','riba_legacy','masterplan'
  name text NOT NULL,
  is_canonical boolean NOT NULL DEFAULT false
);

CREATE TABLE stage (
  id        text PRIMARY KEY,                  -- 'masterplan.dmp'
  scheme_id text NOT NULL REFERENCES stage_scheme(id) ON DELETE CASCADE,
  code      text NOT NULL,
  name      text NOT NULL,
  ordinal   int NOT NULL
);

CREATE TABLE stage_crosswalk (
  from_stage_id text NOT NULL REFERENCES stage(id) ON DELETE CASCADE,
  to_stage_id   text NOT NULL REFERENCES stage(id) ON DELETE CASCADE,
  PRIMARY KEY (from_stage_id, to_stage_id)
);

CREATE TABLE item_stage (
  knowledge_item_id uuid NOT NULL REFERENCES knowledge_item(id) ON DELETE CASCADE,
  stage_id          text NOT NULL REFERENCES stage(id) ON DELETE CASCADE,
  PRIMARY KEY (knowledge_item_id, stage_id)
);

CREATE TABLE standard (
  id        text PRIMARY KEY,                  -- 'uknzcbs','leed_v5','riba_2030'
  name      text NOT NULL,
  publisher text,
  version   text,
  url       text
);

CREATE TABLE item_standard (
  knowledge_item_id uuid NOT NULL REFERENCES knowledge_item(id) ON DELETE CASCADE,
  standard_id       text NOT NULL REFERENCES standard(id) ON DELETE CASCADE,
  relation          text,                      -- 'derived_from' | 'cites' | 'aligns_with'
  PRIMARY KEY (knowledge_item_id, standard_id)
);

-- chunks derive from BOTH raw pages and knowledge items, so a single graded
-- requirement is retrievable on its own with its facets attached
CREATE TABLE chunk (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id       uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  knowledge_item_id uuid REFERENCES knowledge_item(id) ON DELETE CASCADE,
  node_id           uuid REFERENCES doc_node(id) ON DELETE SET NULL,
  asset_id          uuid REFERENCES source_asset(id) ON DELETE SET NULL,
  page_from         int,
  page_to           int,
  ordinal           int NOT NULL DEFAULT 0,
  text              text NOT NULL,
  token_count       int,
  content_status    content_status NOT NULL DEFAULT 'real',
  embedding         vector(384),               -- bge-small-en-v1.5, run locally
  tsv               tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(text, ''))) STORED
);
CREATE INDEX ON chunk USING gin (tsv);
CREATE INDEX ON chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON chunk (document_id);
CREATE INDEX ON chunk (knowledge_item_id) WHERE knowledge_item_id IS NOT NULL;

-- every answer must be able to point at a page
CREATE TABLE citation (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  knowledge_item_id uuid REFERENCES knowledge_item(id) ON DELETE CASCADE,
  chunk_id          uuid REFERENCES chunk(id) ON DELETE CASCADE,
  document_id       uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  page_id           uuid REFERENCES source_page(id) ON DELETE SET NULL,
  page_index        int,
  printed_page_label text,
  bbox              numeric(9,2)[],
  CHECK (knowledge_item_id IS NOT NULL OR chunk_id IS NOT NULL)
);
CREATE INDEX ON citation (knowledge_item_id);
CREATE INDEX ON citation (document_id, page_index);

-- the crib sheets cite a parent guide that is not in the corpus; record the
-- reference now so it resolves later without a migration
CREATE TABLE external_reference (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  from_node_id        uuid REFERENCES doc_node(id) ON DELETE CASCADE,
  from_document_id    uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  raw_text            text NOT NULL,           -- '(Module 5 Chapter 3)', 'Module 1 P48'
  ref_kind            text,                    -- module_chapter|page|standard|sibling_doc
  resolved_document_id uuid REFERENCES source_document(id) ON DELETE SET NULL,
  resolved_node_id    uuid REFERENCES doc_node(id) ON DELETE SET NULL,
  status              ref_status NOT NULL DEFAULT 'unresolved'
);
CREATE INDEX ON external_reference (status);

-- ─────────────────────────────────────────────────────────────────────────
-- Ingest bookkeeping — a crash resumes rather than restarts
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE extraction_run (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pipeline_version text NOT NULL,
  model       text,
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  stats       jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE ingest_job (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_path       text NOT NULL,
  original_filename text NOT NULL,
  sha256            char(64),
  size_bytes        bigint,
  state             ingest_state NOT NULL DEFAULT 'discovered',
  lane              ingest_lane NOT NULL DEFAULT 'fast',
  doc_kind_guess    doc_kind,
  classification_confidence numeric(3,2),
  document_id       uuid REFERENCES source_document(id) ON DELETE SET NULL,
  attempts          int NOT NULL DEFAULT 0,
  last_error        text,
  checkpoint        jsonb NOT NULL DEFAULT '{}',
  discovered_at     timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON ingest_job (state) WHERE state NOT IN ('done','failed');
CREATE INDEX ON ingest_job (sha256);

CREATE TABLE ingest_stage_run (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id      uuid NOT NULL REFERENCES ingest_job(id) ON DELETE CASCADE,
  stage       ingest_state NOT NULL,
  status      stage_result NOT NULL DEFAULT 'running',
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  duration_ms int,
  stats       jsonb NOT NULL DEFAULT '{}',
  error       text,
  UNIQUE (job_id, stage)                       -- idempotency key
);

-- ─────────────────────────────────────────────────────────────────────────
-- Accounts — an allowlist you own, not a vendor feature
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE allowed_account (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email      text NOT NULL,
  google_sub text UNIQUE,
  role       account_role NOT NULL DEFAULT 'reader',
  status     account_status NOT NULL DEFAULT 'active',
  invited_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz
);
CREATE UNIQUE INDEX allowed_account_email ON allowed_account (lower(email));

CREATE TABLE audit_log (
  id         bigserial PRIMARY KEY,
  account_id uuid REFERENCES allowed_account(id) ON DELETE SET NULL,
  action     text NOT NULL,                    -- 'sign_in','download','view_page','export'
  document_id uuid REFERENCES source_document(id) ON DELETE SET NULL,
  knowledge_item_id uuid,
  detail     jsonb NOT NULL DEFAULT '{}',
  ip         inet,
  at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON audit_log (account_id, at DESC);
CREATE INDEX ON audit_log (action, at DESC);

-- ─────────────────────────────────────────────────────────────────────────
-- Views — the "easy to receive" surface
-- ─────────────────────────────────────────────────────────────────────────

CREATE VIEW v_benchmark WITH (security_invoker = true) AS
SELECT b.knowledge_item_id,
       d.slug              AS document_slug,
       m.id                AS metric_id,
       m.name              AS metric,
       b.value_numeric, b.value_min, b.value_max, b.value_text,
       u.symbol            AS unit,
       b.comparator, b.is_placeholder, b.caveat_text,
       b.building_use_id, b.target_year, b.region_id, b.standard_id,
       ki.content_status, ki.review_status,
       c.page_index, c.printed_page_label
FROM benchmark b
JOIN knowledge_item ki ON ki.id = b.knowledge_item_id
JOIN source_document d ON d.id = ki.document_id
JOIN metric m          ON m.id = b.metric_id
LEFT JOIN unit u       ON u.id = b.unit_id
LEFT JOIN citation c   ON c.knowledge_item_id = ki.id;

CREATE VIEW v_requirement_matrix WITH (security_invoker = true) AS
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
       c.page_index
FROM requirement r
JOIN knowledge_item ki ON ki.id = r.knowledge_item_id
JOIN source_document d ON d.id = ki.document_id
LEFT JOIN criterion cr ON cr.id = r.criterion_id
LEFT JOIN framework f  ON f.id = cr.framework_id
LEFT JOIN rating_level rl ON rl.id = r.rating_level_id
LEFT JOIN unit u       ON u.id = r.unit_id
LEFT JOIN citation c   ON c.knowledge_item_id = ki.id;

-- one row per (requirement, scope) it was reprinted under, so "what does
-- this code require from the Design-Build Contractor" is a single filter
-- rather than a manual join against requirement_scope_applicability.
CREATE VIEW v_requirement_scope_matrix WITH (security_invoker = true) AS
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
       d.slug              AS document_slug
FROM requirement_scope_applicability rsa
JOIN requirement_scope rs ON rs.id = rsa.scope_id
JOIN requirement r        ON r.knowledge_item_id = rsa.knowledge_item_id
JOIN knowledge_item ki    ON ki.id = r.knowledge_item_id
JOIN source_document d    ON d.id = ki.document_id
LEFT JOIN criterion cr    ON cr.id = r.criterion_id
LEFT JOIN framework f     ON f.id = cr.framework_id;

CREATE VIEW v_search WITH (security_invoker = true) AS
SELECT ki.id AS knowledge_item_id,
       ki.item_type, ki.title, ki.statement, ki.summary,
       ki.content_status, ki.review_status,
       d.slug AS document_slug, d.doc_kind,
       array_remove(array_agg(DISTINCT it.term_id), NULL) AS term_ids,
       min(c.page_index) AS page_index
FROM knowledge_item ki
JOIN source_document d ON d.id = ki.document_id
LEFT JOIN item_term it ON it.knowledge_item_id = ki.id
LEFT JOIN citation c   ON c.knowledge_item_id = ki.id
GROUP BY ki.id, d.slug, d.doc_kind;

CREATE VIEW v_template_catalogue WITH (security_invoker = true) AS
SELECT t.knowledge_item_id, t.slug, t.template_kind, t.engine,
       ki.title, d.slug AS document_slug,
       count(tp.id) FILTER (WHERE tp.is_input)  AS input_count,
       count(tp.id) FILTER (WHERE tp.is_output) AS output_count
FROM template t
JOIN knowledge_item ki ON ki.id = t.knowledge_item_id
JOIN source_document d ON d.id = ki.document_id
LEFT JOIN template_parameter tp ON tp.template_id = t.knowledge_item_id
GROUP BY t.knowledge_item_id, t.slug, t.template_kind, t.engine, ki.title, d.slug;

-- ─────────────────────────────────────────────────────────────────────────
-- Row-level security — the second line, so a leaked connection string
-- returns nothing rather than everything.
--
-- The app connects as arch_app and sets, per request:
--     SET LOCAL app.account_id = '<uuid>';
-- ─────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION current_account_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.account_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION has_access() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM allowed_account a
    WHERE a.id = current_account_id() AND a.status = 'active'
  )
$$;

CREATE OR REPLACE FUNCTION can_edit() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM allowed_account a
    WHERE a.id = current_account_id() AND a.status = 'active'
      AND a.role IN ('owner','editor')
  )
$$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'organisation','source_document','source_page','source_asset',
    'spreadsheet_sheet','spreadsheet_cell','doc_node','knowledge_item',
    'framework','criterion','rating_scale','rating_level','requirement',
    'requirement_scope','requirement_scope_applicability',
    'benchmark','metric','unit','pattern','pattern_attribute','guidance',
    'design_variable','design_variable_value',
    'definition','process_step','role','template','template_parameter',
    'project','template_instance','taxonomy','taxonomy_term','item_term',
    'stage','stage_scheme','item_stage','standard','item_standard','chunk','citation',
    'external_reference','ingest_job','ingest_stage_run','extraction_run',
    'audit_log'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY %I_read ON %I FOR SELECT USING (has_access())', t, t);
    EXECUTE format(
      'CREATE POLICY %I_write ON %I FOR ALL USING (can_edit()) WITH CHECK (can_edit())', t, t);
  END LOOP;
END $$;

-- allowed_account is managed out of band, never by the app role
-- ENABLE but not FORCE: the app role sees only its own row, while the table
-- owner still administers the allowlist out of band.
ALTER TABLE allowed_account ENABLE ROW LEVEL SECURITY;
CREATE POLICY allowed_account_self ON allowed_account FOR SELECT
  USING (id = current_account_id());

COMMIT;
