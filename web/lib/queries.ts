// Data access for the browse / item / matrix / ingest routes. Every function
// takes an accountId and runs inside withAccount() (lib/db.ts) so RLS scopes
// it correctly. Column lists are explicit and come from constants in this
// file: the app used to ask information_schema which columns existed on every
// request, to tolerate a `requirement.scope` column that never landed.
import type { PoolClient } from "pg";
import { withAccount } from "./db";

// item_type is a fixed Postgres enum (db/schema.sql) — safe to hardcode, and
// cheaper than round-tripping pg_enum on every facet-option request.
export const ITEM_TYPES = [
  "requirement",
  "benchmark",
  "guidance",
  "pattern",
  "template",
  "definition",
  "process_step",
  "role",
] as const;
export type ItemType = (typeof ITEM_TYPES)[number];

export type TaxonomyTerm = { id: string; label: string; n: number };
export type DocumentOption = {
  slug: string;
  title: string | null;
  doc_kind: string;
  n: number;
};

export type FacetOptions = {
  documents: DocumentOption[];
  topics: TaxonomyTerm[];
  scales: TaxonomyTerm[];
  levels: TaxonomyTerm[];
};

// One statement, not four. `Promise.all` over a single PoolClient is not
// parallelism — node-postgres sends one statement per connection at a time —
// so this used to cost four sequential round-trips, which with the function
// on one continent and the database on another was most of the browse page's
// latency.
//
// Each option also carries `n`, how many items actually carry it. Coverage is
// very uneven (topic tags 487 of 771 items, stage tags 10), and an option that
// can only ever return an empty list is worse than no option at all.
export async function getFacetOptions(accountId: string): Promise<FacetOptions> {
  return withAccount(accountId, async (client) => {
    const { rows } = await client.query<{ facets: FacetOptions }>(`
      WITH terms AS (
        -- Subtree counts, not direct ones. Topic is an ltree hierarchy, and
        -- selecting a parent filters by the whole subtree (see the topic
        -- clause in listKnowledgeItems), so a direct count on a parent
        -- promises 17 and delivers 53. Scale and level are flat, where a
        -- subtree is just the term itself, so one expression serves all three.
        SELECT tt.taxonomy_id, tt.id, tt.label, tt.path, tt.ordinal,
               (SELECT count(DISTINCT it.knowledge_item_id)::int
                  FROM item_term it
                  JOIN taxonomy_term d ON d.id = it.term_id
                 WHERE d.path <@ tt.path) AS n
          FROM taxonomy_term tt
         WHERE tt.taxonomy_id IN ('topic', 'scale', 'level')
      ), docs AS (
        SELECT sd.slug, sd.title, sd.doc_kind::text AS doc_kind,
               count(ki.id)::int AS n
          FROM source_document sd
          LEFT JOIN knowledge_item ki ON ki.document_id = sd.id
         WHERE sd.is_current
         GROUP BY sd.slug, sd.title, sd.doc_kind
      )
      SELECT json_build_object(
        'documents', coalesce((
          SELECT json_agg(json_build_object(
                   'slug', slug, 'title', title, 'doc_kind', doc_kind, 'n', n)
                 ORDER BY slug) FROM docs), '[]'::json),
        'topics', coalesce((
          SELECT json_agg(json_build_object('id', id, 'label', label, 'n', n)
                 ORDER BY path NULLS LAST, ordinal)
            FROM terms WHERE taxonomy_id = 'topic'), '[]'::json),
        'scales', coalesce((
          SELECT json_agg(json_build_object('id', id, 'label', label, 'n', n)
                 ORDER BY ordinal)
            FROM terms WHERE taxonomy_id = 'scale'), '[]'::json),
        'levels', coalesce((
          SELECT json_agg(json_build_object('id', id, 'label', label, 'n', n)
                 ORDER BY ordinal)
            FROM terms WHERE taxonomy_id = 'level'), '[]'::json)
      ) AS facets
    `);
    return rows[0].facets;
  });
}

export type HomeSummary = {
  topics: TaxonomyTerm[];
  itemTypes: { id: string; n: number }[];
  totals: { items: number; documents: number; pages: number; chunks: number };
};

// The home page in one round-trip, for the same reason getFacetOptions is one
// statement -- and more so: this is the page that wakes the database from idle
// on the free tier, so it is the one whose latency a visitor actually feels.
//
// Two dimensions, deliberately. Topic has the breadth (11 top-level terms) but
// covers only 487 of 771 items, so a topic grid on its own silently hides the
// rest -- most of one large document, which carries scale and building-use
// tags but no topic. item_type is on the row itself, so every item has exactly
// one and the second row closes the gap. Both map 1:1 onto filters that
// already exist, so a tile is just a browse URL.
export async function getHomeSummary(accountId: string): Promise<HomeSummary> {
  return withAccount(accountId, async (client) => {
    const { rows } = await client.query<{ summary: HomeSummary }>(`
      WITH tops AS (
        SELECT tt.id, tt.label, tt.ordinal, tt.path,
               (SELECT count(DISTINCT it.knowledge_item_id)::int
                  FROM item_term it
                  JOIN taxonomy_term d ON d.id = it.term_id
                 WHERE d.path <@ tt.path) AS n
          FROM taxonomy_term tt
         WHERE tt.taxonomy_id = 'topic' AND tt.parent_id IS NULL
      )
      SELECT json_build_object(
        'topics', coalesce((
          SELECT json_agg(json_build_object('id', id, 'label', label, 'n', n)
                 ORDER BY n DESC, ordinal)
            FROM tops WHERE n > 0), '[]'::json),
        'itemTypes', coalesce((
          SELECT json_agg(t ORDER BY (t->>'n')::int DESC)
            FROM (
              SELECT json_build_object('id', item_type::text, 'n', count(*)::int) AS t
                FROM knowledge_item GROUP BY item_type
            ) s), '[]'::json),
        'totals', json_build_object(
          'items',     (SELECT count(*)::int FROM knowledge_item),
          'documents', (SELECT count(*)::int FROM source_document WHERE is_current),
          'pages',     (SELECT count(*)::int FROM source_page),
          'chunks',    (SELECT count(*)::int FROM chunk)
        )
      ) AS summary
    `);
    return rows[0].summary;
  });
}

export type BrowseResults = {
  items: BrowseItem[];
  total: number;
  /** How many page and figure matches an item-only facet hid. Counted in the
   *  same statement as the results, so the sentence the page prints is never a
   *  guess. */
  suppressed: number;
  kindCounts: Partial<Record<ResultKind, number>>;
};

export type PageAsset = {
  id: string;
  image_key: string | null;
  /** [x0, y0, x1, y1] in PDF points, top-left origin — the same space as the
   *  page's width_pt/height_pt, so an overlay is four percentages. */
  bbox: number[] | null;
  vlm_description: string | null;
  vlm_model: string | null;
};

export type SourcePageDetail = {
  id: string;
  page_index: number;
  printed_page_label: string | null;
  text: string | null;
  page_image_key: string | null;
  width_pt: number | null;
  height_pt: number | null;
  content_status: string;
  document_slug: string;
  document_title: string | null;
  assets: PageAsset[];
};

/** One page, its scan and the figures on it.
 *
 *  Search can return a page's raw text or a figure's description, and neither
 *  is a knowledge_item, so neither has an /item/[id] to land on. This is where
 *  they go: the scan is the answer to "where did that come from", and for a
 *  figure it is the only way to check a generated description against the
 *  thing it describes.
 */
export async function getSourcePage(
  accountId: string,
  pageId: string,
): Promise<SourcePageDetail | null> {
  return withAccount(accountId, async (client) => {
    const { rows } = await client.query<SourcePageDetail>(
      `SELECT p.id::text, p.page_index, p.printed_page_label, p.text,
              p.page_image_key, p.width_pt::float8, p.height_pt::float8,
              p.content_status::text AS content_status,
              d.slug AS document_slug, d.title AS document_title,
              coalesce((
                SELECT json_agg(json_build_object(
                         'id', a.id::text, 'image_key', a.image_key,
                         'bbox', a.bbox, 'vlm_description', a.vlm_description,
                         'vlm_model', a.vlm_model)
                       ORDER BY a.bbox[2], a.bbox[1])
                  FROM source_asset a
                 WHERE a.page_id = p.id AND a.image_key IS NOT NULL
              ), '[]'::json) AS assets
         FROM source_page p
         JOIN source_document d ON d.id = p.document_id
        WHERE p.id = $1`,
      [pageId],
    );
    return rows[0] ?? null;
  });
}

export type BrowseFilters = {
  documentSlug?: string;
  itemType?: string;
  topicId?: string;
  scaleId?: string;
  levelId?: string;
  q?: string;
};

/** 'item' is an extracted knowledge_item; 'page' is the raw text of a page no
 *  item was extracted from; 'figure' is a model-written description of a
 *  cropped figure. The last one is not something the document says, which is
 *  why it carries its model with it wherever it is rendered. */
export type ResultKind = "item" | "page" | "figure";

export type BrowseItem = {
  id: string;
  kind: ResultKind;
  /** figure only -- the asset whose description this is */
  asset_id?: string | null;
  /** figure only -- provenance, and the reason the card must stamp it */
  vlm_model?: string | null;
  /** page and figure -- where the result lives, for the /page/[id] link */
  page_id?: string | null;
  item_type: string;
  title: string | null;
  statement: string | null;
  summary: string | null;
  content_status: string;
  review_status: string;
  document_slug: string;
  document_title: string | null;
  doc_kind: string;
  page_index: number | null;
  printed_page_label: string | null;
  /** Relevance, only when a query was given. Null in document order. */
  score: number | null;
  /** The matching chunk text with [[…]] around the hits. */
  snippet: string | null;
  total?: number;
};

export const BROWSE_PAGE_SIZE = 25;

/** Browse results: ranked when there is a query, document order when not.
 *
 *  The search box used to run `title ILIKE '%q%' OR statement ILIKE ... OR
 *  summary ILIKE ...` and then sort alphabetically by document slug — so
 *  typing changed which rows appeared but never which came first, and body
 *  text that lives in `chunk` and not in those three columns was unfindable.
 *
 *  This ranks against the GIN-indexed `chunk.tsv`, the same index
 *  `tools/search.py` uses. Three legs, each tried only when the one before it
 *  found nothing, all inside a single statement:
 *
 *    1. `websearch_to_tsquery` — phrases, negation, stemming.
 *    2. OR-of-lexemes. Measured on this corpus: "embodied carbon 2030" matches
 *       0 chunks under AND and 95 under OR, because no single chunk contains
 *       all three terms. Without this fallback most real queries return
 *       nothing, which is how a search feature comes to look broken.
 *    3. ILIKE, which is the only leg that finds a code fragment or a prefix
 *       that no tsquery will lex.
 *
 *  What this is not: `tools/search.py` fuses a pgvector leg by RRF, and that
 *  is what lets it answer loosely-worded questions. A query vector has to come
 *  from the same local bge-small model, which cannot run in a Vercel function,
 *  so the web path is lexical only and the hybrid path stays with the MCP
 *  server.
 *
 *  A search reaches all three kinds of chunk: the 771 derived from knowledge
 *  items, the 398 derived from pages no item was extracted from, and the 784
 *  figure descriptions. It used to inner-join knowledge_item, which excluded
 *  the last two -- so a benchmark table that exists only as a picture could
 *  not be found by searching for the numbers in it. The unfiltered list is
 *  still items only: it is a catalogue of what was extracted, and the facet
 *  counts beside it describe exactly that.
 */
export async function listKnowledgeItems(
  accountId: string,
  filters: BrowseFilters,
  opts: { limit?: number; offset?: number } = {},
): Promise<BrowseResults> {
  const limit = Math.min(opts.limit ?? BROWSE_PAGE_SIZE, 100);
  const offset = Math.max(opts.offset ?? 0, 0);
  return withAccount(accountId, async (client) => {
    const conditions: string[] = [];
    const params: unknown[] = [];
    const push = (sql: string, value: unknown) => {
      params.push(value);
      conditions.push(sql.replace("?", `$${params.length}`));
    };

    // A term matches its whole subtree. `term_id = ?` was exact, so choosing
    // a top-level topic returned only the items tagged on the parent itself --
    // "Health & Wellbeing" gave 17 of its 53. The taxonomy is an ltree with a
    // GiST index on `path` (db/schema.sql), so `<@` is both correct and cheap.
    // Scale and level are flat: their subtree is the term, and the clause is
    // identical, which is why there is one of it.
    const TERM_SUBTREE = `EXISTS (
      SELECT 1 FROM item_term it
        JOIN taxonomy_term tt ON tt.id = it.term_id
       WHERE it.knowledge_item_id = ki.id
         AND tt.path <@ (SELECT path FROM taxonomy_term WHERE id = ?))`;

    // Document is the only filter that means anything for all three kinds --
    // every chunk has a document_id. The other four hang off knowledge_item
    // and item_term, and a page or figure chunk cannot satisfy them or fail
    // them; the predicate is simply undefined for it. So they are tracked
    // separately: applied as a predicate for items, and counted as a
    // suppression for everything else, which the page then says out loud.
    const itemOnly: string[] = [];
    const pushItemOnly = (sql: string, value: unknown) => {
      params.push(value);
      itemOnly.push(sql.replace("?", `$${params.length}`));
    };

    if (filters.documentSlug) push("d.slug = ?", filters.documentSlug);
    if (filters.itemType) pushItemOnly("ki.item_type = ?::item_type", filters.itemType);
    if (filters.topicId) pushItemOnly(TERM_SUBTREE, filters.topicId);
    if (filters.scaleId) pushItemOnly(TERM_SUBTREE, filters.scaleId);
    if (filters.levelId) pushItemOnly(TERM_SUBTREE, filters.levelId);

    // The unranked path lists items and nothing else, so there the two sets
    // are simply concatenated -- no kind can be suppressed from a list that
    // only ever held one.
    const allConditions = [...conditions, ...itemOnly];
    const where = allConditions.length ? `WHERE ${allConditions.join(" AND ")}` : "";

    // Shared tail: the columns every row needs, plus the pre-LIMIT total, so
    // the header count and the page come back in one statement.
    const SELECT_COLUMNS = `
      ki.id, ki.item_type::text AS item_type, ki.title, ki.statement, ki.summary,
      ki.content_status::text AS content_status, ki.review_status::text AS review_status,
      d.slug AS document_slug, d.title AS document_title, d.doc_kind::text AS doc_kind,
      c.page_index, c.printed_page_label`;
    const CITATION_JOIN = `
      LEFT JOIN LATERAL (
        SELECT page_index, printed_page_label FROM citation
        WHERE citation.knowledge_item_id = ki.id
        ORDER BY page_index NULLS LAST LIMIT 1
      ) c ON true`;

    if (!filters.q) {
      params.push(limit, offset);
      const { rows } = await client.query(
        `SELECT ${SELECT_COLUMNS}, 'item'::text AS kind,
                NULL::float8 AS score, NULL::text AS snippet,
                count(*) OVER ()::int AS total
           FROM knowledge_item ki
           JOIN source_document d ON d.id = ki.document_id
           ${CITATION_JOIN}
           ${where}
          ORDER BY d.slug, ki.item_type, ki.title NULLS LAST
          LIMIT $${params.length - 1} OFFSET $${params.length}`,
        params,
      );
      const total = rows[0]?.total ?? 0;
      return { items: rows as BrowseItem[], total, suppressed: 0, kindCounts: { item: total } };
    }

    // The document predicate has to apply inside the ranking legs, not after
    // them: rank-then-filter would return an empty page whenever the top
    // matches happened to miss the chosen document.
    params.push(filters.q);
    const q = `$${params.length}`;
    params.push(`%${filters.q}%`);
    const like = `$${params.length}`;
    params.push(limit, offset);
    const limitP = `$${params.length - 1}`;
    const offsetP = `$${params.length}`;

    // A page chunk is a whole page of raw text, and one of them is three
    // characters long. Item and figure chunks are composed, so the floor is
    // page-only: a blanket one would delete 180 short item chunks from search,
    // and they are perfectly good answers.
    const PAGE_TEXT_FLOOR =
      "(ch.knowledge_item_id IS NOT NULL OR ch.asset_id IS NOT NULL OR length(ch.text) >= 40)";
    const baseWhere = `WHERE ${[...conditions, PAGE_TEXT_FLOOR].join(" AND ")}`;
    const itemOk = itemOnly.length ? `(${itemOnly.join(" AND ")})` : "true";

    const { rows } = await client.query(
      `
      WITH base AS (
        SELECT ch.id AS chunk_id, ch.tsv, ch.text, ch.document_id, ch.page_from,
               ki.id AS item_id, ch.asset_id,
               -- What counts as one result. An item groups all of its chunks;
               -- a figure is its own; a page groups the windows it was split
               -- into, so a long page returns once, scored by its best window,
               -- instead of flooding the list with its own fragments.
               -- Grouping used to be on item_id alone, which gave a chunk with
               -- no item nowhere to live -- not filtered out, simply keyless.
               coalesce(ki.id::text, ch.asset_id::text,
                        ch.document_id::text || ':' || ch.page_from) AS result_id,
               CASE WHEN ki.id IS NOT NULL      THEN 'item'
                    WHEN ch.asset_id IS NOT NULL THEN 'figure'
                    ELSE 'page' END AS kind,
               -- Non-items can never satisfy an item-only facet. Carried as a
               -- flag rather than a WHERE so the ones it hides can be counted.
               ${itemOk} AS ok
          FROM chunk ch
          LEFT JOIN knowledge_item ki ON ki.id = ch.knowledge_item_id
          JOIN source_document d ON d.id = ch.document_id
         ${baseWhere}
      ),
      -- OR-of-lexemes, built in SQL so there is no client-side tokenising to
      -- keep in step with the 'english' dictionary.
      or_query AS (
        SELECT NULLIF(string_agg(lexeme, ' | '), '')::tsquery AS tsq
          FROM unnest(to_tsvector('english', ${q})) AS lexeme
      ),
      fts AS (
        SELECT b.*, ts_rank(b.tsv, websearch_to_tsquery('english', ${q})) AS rank
          FROM base b
         WHERE b.tsv @@ websearch_to_tsquery('english', ${q})
      ),
      fts_or AS (
        SELECT b.*, ts_rank(b.tsv, o.tsq) AS rank
          FROM base b CROSS JOIN or_query o
         WHERE NOT EXISTS (SELECT 1 FROM fts)
           AND o.tsq IS NOT NULL AND b.tsv @@ o.tsq
      ),
      literal AS (
        SELECT b.*, 0.0::float4 AS rank
          FROM base b
         WHERE NOT EXISTS (SELECT 1 FROM fts)
           AND NOT EXISTS (SELECT 1 FROM fts_or)
           AND b.text ILIKE ${like}
      ),
      hits AS (
        SELECT * FROM fts UNION ALL SELECT * FROM fts_or UNION ALL SELECT * FROM literal
      ),
      -- One row per result, scored by its best chunk. max(), not sum(): summing
      -- would rank a long item above a precise one for being long.
      per_result AS (
        SELECT result_id, item_id, asset_id, kind, document_id, ok,
               max(rank) AS score,
               (array_agg(text      ORDER BY rank DESC))[1] AS best_text,
               (array_agg(page_from ORDER BY rank DESC))[1] AS page_from
          FROM hits
         GROUP BY result_id, item_id, asset_id, kind, document_id, ok
      ),
      suppressed AS (
        SELECT count(*)::int AS n FROM per_result WHERE NOT ok AND kind <> 'item'
      ),
      kinds AS (
        SELECT coalesce(json_object_agg(kind, n), '{}'::json) AS counts
          FROM (SELECT kind, count(*)::int AS n FROM per_result WHERE ok GROUP BY kind) s
      ),
      page AS (
        SELECT p.*, count(*) OVER ()::int AS total
          FROM per_result p
         WHERE p.ok
         ORDER BY p.score DESC, p.result_id
         LIMIT ${limitP} OFFSET ${offsetP}
      )
      SELECT pg.result_id AS id,
             pg.kind,
             pg.asset_id::text AS asset_id,
             sp.id::text AS page_id,
             a.vlm_model,
             -- The card's type label. A page or figure has no item_type and
             -- must not borrow one, so it shows what it is instead.
             coalesce(ki.item_type::text, pg.kind) AS item_type,
             ki.title, ki.statement, ki.summary,
             coalesce(ki.content_status::text, 'real') AS content_status,
             ki.review_status::text AS review_status,
             d.slug AS document_slug, d.title AS document_title,
             d.doc_kind::text AS doc_kind,
             coalesce(c.page_index, sp.page_index) AS page_index,
             coalesce(c.printed_page_label, sp.printed_page_label) AS printed_page_label,
             pg.score, pg.total, sup.n AS suppressed, kc.counts AS kind_counts,
             -- StartSel/StopSel are plain markers, split in the page. Returning
             -- HTML here would mean dangerouslySetInnerHTML on corpus text.
             ts_headline('english', pg.best_text,
                         websearch_to_tsquery('english', ${q}),
                         'MaxFragments=1,MaxWords=28,MinWords=10,StartSel=[[,StopSel=]]') AS snippet
        -- The counts drive, the page hangs off them. Backwards-looking, and
        -- deliberate: the suppressed and kinds CTEs always return exactly one
        -- row, so this statement does too even when nothing matched -- and
        -- "nothing matched, because your topic filter hid 4 figures" is
        -- precisely the case where that sentence is worth reading. Driving
        -- from the page CTE would return zero rows and lose the explanation,
        -- which is the one message that page most needs. The price
        -- is one phantom row when the page is empty, dropped in the caller.
        FROM suppressed sup
        CROSS JOIN kinds kc
        LEFT JOIN page pg ON true
        LEFT JOIN knowledge_item ki ON ki.id = pg.item_id
        LEFT JOIN source_document d ON d.id = pg.document_id
        LEFT JOIN source_asset a ON a.id = pg.asset_id
        LEFT JOIN source_page sp
               ON sp.document_id = pg.document_id AND sp.page_index = pg.page_from
        ${CITATION_JOIN}
       ORDER BY pg.score DESC, pg.result_id
      `,
      params,
    );
    return {
      items: rows.filter((r) => r.id !== null) as BrowseItem[],
      total: rows[0]?.total ?? 0,
      suppressed: rows[0]?.suppressed ?? 0,
      kindCounts: rows[0]?.kind_counts ?? {},
    };
  });
}

// ── item detail ────────────────────────────────────────────────────────

const SUBTYPE_TABLES: Record<ItemType, { table: string; columns: string[] }> = {
  requirement: {
    table: "requirement",
    columns: [
      "requirement_kind",
      "criterion_id",
      "rating_level_id",
      "metric_id",
      "target_value",
      "target_text",
      "unit_id",
      "comparator",
      "is_deliverable",
      "deliverable_name",
      "parsed_ok",
    ],
  },
  benchmark: {
    table: "benchmark",
    columns: [
      "metric_id",
      "value_numeric",
      "value_min",
      "value_max",
      "value_text",
      "unit_id",
      "comparator",
      "is_placeholder",
      "caveat_text",
      "building_use_id",
      "target_year",
      "region_id",
      "standard_id",
      "baseline_relative_pct",
    ],
  },
  guidance: {
    table: "guidance",
    columns: ["body_md", "figure_ids", "legend_tokens", "disclaimer"],
  },
  pattern: {
    table: "pattern",
    columns: ["pattern_kind", "code", "name", "parent_pattern_id", "attributes"],
  },
  definition: { table: "definition", columns: ["term", "definition", "category"] },
  process_step: {
    table: "process_step",
    columns: ["code", "ordinal", "gate", "responsible_role_id"],
  },
  role: {
    table: "role",
    columns: ["code", "name", "reports_to", "qualifications"],
  },
  template: {
    table: "template",
    columns: ["template_kind", "engine", "slug"],
  },
};

export type ItemDetail = {
  id: string;
  item_type: string;
  title: string | null;
  statement: string | null;
  summary: string | null;
  content_status: string;
  review_status: string;
  document_slug: string;
  document_title: string | null;
  doc_kind: string;
  node_title: string | null;
  node_code: string | null;
  payload: Record<string, unknown> | null;
  citations: {
    page_index: number | null;
    printed_page_label: string | null;
    document_slug: string;
    document_title: string | null;
    page_image_key: string | null;
    width_pt: number | null;
    height_pt: number | null;
  }[];
  terms: { id: string; taxonomy_id: string; label: string }[];
  // per-scope applicability (e.g. "Design-Build Contractor", "Main
  // Contractor") for requirement items — see requirement_scope /
  // requirement_scope_applicability in db/schema.sql. Populated only when
  // those tables exist (tableExists() guard below) and carry rows for this
  // item; otherwise empty, never an error.
  scopes: { title: string; code: string | null; applies: boolean; target_text: string | null; note: string | null }[];
};

export async function getKnowledgeItem(
  accountId: string,
  id: string,
): Promise<ItemDetail | null> {
  return withAccount(accountId, async (client) => {
    const base = await client.query(
      `
      SELECT ki.id, ki.item_type::text AS item_type, ki.title, ki.statement, ki.summary,
             ki.content_status::text AS content_status, ki.review_status::text AS review_status,
             d.slug AS document_slug, d.title AS document_title, d.doc_kind::text AS doc_kind,
             n.title AS node_title, n.code AS node_code
      FROM knowledge_item ki
      JOIN source_document d ON d.id = ki.document_id
      LEFT JOIN doc_node n ON n.id = ki.node_id
      WHERE ki.id = $1
      `,
      [id],
    );
    const row = base.rows[0];
    if (!row) return null;

    const itemType = row.item_type as ItemType;
    let payload: Record<string, unknown> | null = null;

    if (itemType === "requirement") {
      const { rows } = await client.query(
        `
        SELECT r.requirement_kind::text AS requirement_kind, r.target_text, r.target_value,
               u.symbol AS unit, r.comparator::text AS comparator, r.is_deliverable,
               r.deliverable_name, r.parsed_ok,
               rl.ordinal AS level_ordinal, rl.code AS level_code, rl.name AS level_name,
               c.code AS criterion_code, coalesce(c.title_primary, c.title_alt) AS criterion_title,
               m.name AS metric_name
        FROM requirement r
        LEFT JOIN unit u ON u.id = r.unit_id
        LEFT JOIN rating_level rl ON rl.id = r.rating_level_id
        LEFT JOIN criterion c ON c.id = r.criterion_id
        LEFT JOIN metric m ON m.id = r.metric_id
        WHERE r.knowledge_item_id = $1
        `,
        [id],
      );
      payload = rows[0] ?? null;
    } else if (itemType === "benchmark") {
      const { rows } = await client.query(
        `
        SELECT b.value_numeric, b.value_min, b.value_max, b.value_text,
               u.symbol AS unit, b.comparator::text AS comparator, b.is_placeholder,
               b.caveat_text, b.building_use_id, b.target_year, b.region_id,
               b.standard_id, b.baseline_relative_pct, m.name AS metric_name
        FROM benchmark b
        LEFT JOIN unit u ON u.id = b.unit_id
        LEFT JOIN metric m ON m.id = b.metric_id
        WHERE b.knowledge_item_id = $1
        `,
        [id],
      );
      payload = rows[0] ?? null;
    } else {
      // Column lists come from the constant above, not from
      // information_schema. The old version asked the database which columns
      // existed on every item page — two `information_schema` probes on every
      // cold lambda — to tolerate a `requirement.scope` column that was never
      // added and never will be: the scope dimension landed as the
      // requirement_scope / requirement_scope_applicability tables instead.
      // If a column is ever added, add it here in the same change.
      const subtype = SUBTYPE_TABLES[itemType];
      if (subtype) {
        const cols = subtype.columns.map((c) => `t.${c}`).join(", ");
        const { rows } = await client.query(
          `SELECT ${cols} FROM ${subtype.table} t WHERE t.knowledge_item_id = $1`,
          [id],
        );
        payload = rows[0] ?? null;
      }
    }

    const citations = await client.query(
      // The scan comes along with the citation: this used to render only in
      // the review queue, so deciding an item was also the act of hiding the
      // page it was extracted from.
      `SELECT c.page_index, c.printed_page_label, d.slug AS document_slug,
              d.title AS document_title,
              p.page_image_key, p.width_pt, p.height_pt
       FROM citation c
       JOIN source_document d ON d.id = c.document_id
       LEFT JOIN source_page p
              ON p.document_id = c.document_id AND p.page_index = c.page_index
       WHERE c.knowledge_item_id = $1
       ORDER BY c.page_index NULLS LAST`,
      [id],
    );

    const terms = await client.query(
      // tt.id comes along so the item page can link each facet back into a
      // filtered browse — the chips used to be inert text.
      `SELECT tt.id, tt.taxonomy_id, tt.label
       FROM item_term it JOIN taxonomy_term tt ON tt.id = it.term_id
       WHERE it.knowledge_item_id = $1
       ORDER BY tt.taxonomy_id`,
      [id],
    );

    // No tableExists() guard: requirement_scope_applicability is in
    // db/schema.sql and on every database. The guard was a round trip whose
    // answer was always yes.
    let scopes: ItemDetail["scopes"] = [];
    if (itemType === "requirement") {
      const { rows } = await client.query(
        `SELECT rs.title, rs.code, rsa.applies, rsa.target_text, rsa.note
         FROM requirement_scope_applicability rsa
         JOIN requirement_scope rs ON rs.id = rsa.scope_id
         WHERE rsa.knowledge_item_id = $1
         ORDER BY rs.ordinal, rs.title`,
        [id],
      );
      scopes = rows;
    }

    return {
      ...row,
      payload,
      citations: citations.rows,
      terms: terms.rows,
      scopes,
    } as ItemDetail;
  });
}

// ── matrix ────────────────────────────────────────────────────────────

export type FrameworkOption = { slug: string; name: string; rating_scale_id: string | null };

export async function listFrameworks(accountId: string): Promise<FrameworkOption[]> {
  return withAccount(accountId, (client) =>
    client
      .query<FrameworkOption>(
        `SELECT slug, name, rating_scale_id::text AS rating_scale_id FROM framework ORDER BY slug`,
      )
      .then((r) => r.rows),
  );
}

export type MatrixLevel = { id: string; ordinal: number; code: string | null; name: string | null };
export type MatrixCriterion = { id: string; code: string | null; title: string; ordinal: number };
export type MatrixCell = {
  knowledge_item_id: string;
  statement: string | null;
  target_text: string | null;
  unit: string | null;
  comparator: string;
  is_deliverable: boolean;
  content_status: string;
  review_status: string;
  page_index: number | null;
};

export async function getMatrixDocuments(
  accountId: string,
  frameworkSlug: string,
): Promise<DocumentOption[]> {
  return withAccount(accountId, (client) =>
    client
      .query<DocumentOption>(
        `
        SELECT DISTINCT d.slug, d.title, d.doc_kind::text AS doc_kind
        FROM framework f
        JOIN criterion c ON c.framework_id = f.id
        JOIN requirement r ON r.criterion_id = c.id
        JOIN knowledge_item ki ON ki.id = r.knowledge_item_id
        JOIN source_document d ON d.id = ki.document_id
        WHERE f.slug = $1
        ORDER BY d.slug
        `,
        [frameworkSlug],
      )
      .then((r) => r.rows),
  );
}

export async function getMatrix(
  accountId: string,
  frameworkSlug: string,
  documentSlug?: string,
): Promise<{ levels: MatrixLevel[]; criteria: MatrixCriterion[]; cells: Map<string, MatrixCell[]> }> {
  return withAccount(accountId, async (client) => {
    const fw = await client.query(
      `SELECT id, rating_scale_id FROM framework WHERE slug = $1`,
      [frameworkSlug],
    );
    const framework = fw.rows[0];
    if (!framework) return { levels: [], criteria: [], cells: new Map() };

    const levels = await client.query<MatrixLevel>(
      `SELECT id, ordinal, code, name FROM rating_level WHERE scale_id = $1 ORDER BY ordinal`,
      [framework.rating_scale_id],
    );

    const criteriaParams: unknown[] = [framework.id];
    let docJoin = "";
    if (documentSlug) {
      docJoin = `AND EXISTS (
        SELECT 1 FROM requirement r2
        JOIN knowledge_item ki2 ON ki2.id = r2.knowledge_item_id
        JOIN source_document d2 ON d2.id = ki2.document_id
        WHERE r2.criterion_id = c.id AND d2.slug = $2
      )`;
      criteriaParams.push(documentSlug);
    }
    const criteria = await client.query<MatrixCriterion>(
      `SELECT c.id, c.code, coalesce(c.title_primary, c.title_alt, c.code) AS title, c.ordinal
       FROM criterion c
       WHERE c.framework_id = $1 ${docJoin}
       ORDER BY c.path NULLS LAST, c.ordinal, c.code`,
      criteriaParams,
    );

    // The sheet filter has to reach the CELLS, not just the criteria. It used
    // to narrow only the rows, so picking a sheet kept every other sheet's
    // requirements inside those rows — the table looked filtered and was not.
    const cellsParams: unknown[] = [framework.id];
    let cellDocFilter = "";
    if (documentSlug) {
      cellsParams.push(documentSlug);
      cellDocFilter = `AND d.slug = $${cellsParams.length}`;
    }

    const cellsRes = await client.query(
      `
      SELECT r.criterion_id::text AS criterion_id, r.rating_level_id::text AS rating_level_id,
             ki.id::text AS knowledge_item_id, ki.statement,
             r.target_text, u.symbol AS unit, r.comparator::text AS comparator,
             r.is_deliverable, ki.content_status::text AS content_status,
             ki.review_status::text AS review_status,
             cit.page_index
      FROM requirement r
      JOIN knowledge_item ki ON ki.id = r.knowledge_item_id
      JOIN source_document d ON d.id = ki.document_id
      JOIN criterion c ON c.id = r.criterion_id
      LEFT JOIN unit u ON u.id = r.unit_id
      LEFT JOIN LATERAL (
        SELECT page_index FROM citation WHERE citation.knowledge_item_id = ki.id ORDER BY page_index LIMIT 1
      ) cit ON true
      WHERE c.framework_id = $1 ${cellDocFilter}
      `,
      cellsParams,
    );

    const cells = new Map<string, MatrixCell[]>();
    for (const row of cellsRes.rows) {
      const key = `${row.criterion_id}::${row.rating_level_id}`;
      const { criterion_id, rating_level_id, ...rest } = row;
      void criterion_id;
      void rating_level_id;
      const list = cells.get(key) ?? [];
      list.push(rest as MatrixCell);
      cells.set(key, list);
    }

    return { levels: levels.rows, criteria: criteria.rows, cells };
  });
}

// ── ingest ────────────────────────────────────────────────────────────

export type IngestJob = {
  id: string;
  source_path: string;
  original_filename: string;
  state: string;
  lane: string;
  doc_kind_guess: string | null;
  classification_confidence: number | null;
  document_id: string | null;
  attempts: number;
  last_error: string | null;
  discovered_at: string;
  updated_at: string;
};

export type IngestStageRun = {
  job_id: string;
  stage: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  error: string | null;
};

export async function listIngestJobs(
  accountId: string,
): Promise<{ jobs: IngestJob[]; stages: IngestStageRun[] }> {
  return withAccount(accountId, async (client) => {
    const jobs = await client.query<IngestJob>(
      `
      SELECT id::text, source_path, original_filename, state::text AS state, lane::text AS lane,
             doc_kind_guess::text AS doc_kind_guess, classification_confidence,
             document_id::text AS document_id, attempts, last_error,
             discovered_at::text AS discovered_at, updated_at::text AS updated_at
      FROM ingest_job
      ORDER BY updated_at DESC
      LIMIT 200
      `,
    );
    const stages = await client.query<IngestStageRun>(
      `
      SELECT job_id::text, stage::text AS stage, status::text AS status,
             started_at::text AS started_at, finished_at::text AS finished_at,
             duration_ms, error
      FROM ingest_stage_run
      ORDER BY started_at
      `,
    );
    return { jobs: jobs.rows, stages: stages.rows };
  });
}

export type { PoolClient };

// ── review queue ──────────────────────────────────────────────────────────

export type ReviewItem = {
  id: string;
  item_type: string;
  title: string | null;
  statement: string | null;
  content_status: string;
  extraction_confidence: number | null;
  document_slug: string;
  document_title: string | null;
  review_status: string;
  page_index: number | null;
  printed_page_label: string | null;
  page_image_key: string | null;
  width_pt: number | null;
  height_pt: number | null;
  total?: number;
};

export const REVIEW_STATUSES = ["pending", "approved", "rejected", "all"] as const;
export type ReviewFilter = (typeof REVIEW_STATUSES)[number];

/** Items in the review queue, lowest confidence first — the ones most likely
 *  to be wrong are the ones worth a reviewer's time.
 *
 *  Filterable by status rather than hardcoded to 'pending', because the page
 *  scan renders only here: approving an item used to remove the only place in
 *  the app where its source page could be seen. */
export async function listReviewQueue(
  accountId: string,
  opts: { document?: string; status?: ReviewFilter; limit?: number; offset?: number } = {},
): Promise<{ items: ReviewItem[]; total: number }> {
  const limit = Math.min(opts.limit ?? 25, 100);
  const offset = opts.offset ?? 0;
  const status: ReviewFilter = REVIEW_STATUSES.includes(opts.status as ReviewFilter)
    ? (opts.status as ReviewFilter)
    : "pending";
  return withAccount(accountId, async (client) => {
    // The status is validated against the literal list above and never
    // interpolated from raw input.
    const where: string[] = status === "all" ? ["true"] : [`k.review_status = '${status}'`];
    const params: unknown[] = [];
    if (opts.document) {
      params.push(opts.document);
      where.push(`d.slug = $${params.length}`);
    }
    const clause = where.join(" AND ");

    // One statement, not two: `count(*) OVER ()` on the windowed set gives the
    // pre-LIMIT total alongside the page, so the header count and the rows
    // come back together instead of costing a second round trip.
    //
    // width_pt/height_pt come along so the page scan can reserve its space
    // before it loads. They are populated for all 806 pages, in three
    // geometries, and without them every image reflows its row on decode.
    const rows = await client.query(
      `SELECT k.id, k.item_type::text, k.title, k.statement,
              k.content_status::text, k.extraction_confidence,
              k.review_status::text AS review_status,
              d.slug AS document_slug, d.title AS document_title,
              c.page_index, c.printed_page_label,
              p.page_image_key, p.width_pt, p.height_pt,
              count(*) OVER ()::int AS total
         FROM knowledge_item k
         JOIN source_document d ON d.id = k.document_id
         LEFT JOIN LATERAL (
           SELECT page_index, printed_page_label FROM citation
            WHERE knowledge_item_id = k.id ORDER BY page_index LIMIT 1
         ) c ON true
         LEFT JOIN source_page p
                ON p.document_id = k.document_id AND p.page_index = c.page_index
        WHERE ${clause}
        ORDER BY k.extraction_confidence NULLS FIRST, k.id
        LIMIT $${params.length + 1} OFFSET $${params.length + 2}`,
      [...params, limit, offset],
    );
    if (rows.rows.length > 0) {
      return { items: rows.rows as ReviewItem[], total: rows.rows[0].total as number };
    }
    // An empty page past the end carries no window row, so the total is
    // unknown — and reporting 0 would hide the "previous" link and strand the
    // reader. Only this rare path pays for a second statement.
    if (offset === 0) return { items: [], total: 0 };
    const totalRes = await client.query(
      `SELECT count(*)::int AS n FROM knowledge_item k
         JOIN source_document d ON d.id = k.document_id
        WHERE ${clause}`,
      params,
    );
    return { items: [], total: totalRes.rows[0].n as number };
  });
}

/** Record a reviewer's decision and log who made it. Editors and owners only;
 *  the caller enforces role, this enforces the audit trail. */
export async function recordReview(
  accountId: string,
  itemId: string,
  decision: "approved" | "rejected" | "pending",
): Promise<void> {
  await withAccount(accountId, async (client) => {
    await client.query(
      // $1 is the enum and $2 is the reset flag, deliberately not the same
      // placeholder twice: `SET review_status = $1 ... CASE WHEN $1 = 'pending'`
      // made Postgres deduce $1 as both review_status and text and fail the
      // statement with 42P08 "inconsistent types deduced for parameter $1".
      //
      // reviewed_at goes back to NULL on reopen: a reopened item has not been
      // decided, and leaving a timestamp there says it has.
      `UPDATE knowledge_item
          SET review_status = $1,
              reviewed_at = CASE WHEN $2 THEN NULL ELSE now() END
        WHERE id = $3`,
      [decision, decision === "pending", itemId],
    );
    await client.query(
      `INSERT INTO audit_log (account_id, action, knowledge_item_id)
       VALUES ($1, $2, $3)`,
      [accountId, `review:${decision}`, itemId],
    );
  });
}
