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

export type BrowseFilters = {
  documentSlug?: string;
  itemType?: string;
  topicId?: string;
  scaleId?: string;
  levelId?: string;
  q?: string;
};

export type BrowseItem = {
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
 *  server. It also cannot see the 398 of 1,169 chunks that carry no
 *  knowledge_item_id.
 */
export async function listKnowledgeItems(
  accountId: string,
  filters: BrowseFilters,
  opts: { limit?: number; offset?: number } = {},
): Promise<{ items: BrowseItem[]; total: number }> {
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

    if (filters.documentSlug) push("d.slug = ?", filters.documentSlug);
    if (filters.itemType) push("ki.item_type = ?::item_type", filters.itemType);
    if (filters.topicId) push(TERM_SUBTREE, filters.topicId);
    if (filters.scaleId) push(TERM_SUBTREE, filters.scaleId);
    if (filters.levelId) push(TERM_SUBTREE, filters.levelId);
    const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";

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
        `SELECT ${SELECT_COLUMNS}, NULL::float8 AS score, NULL::text AS snippet,
                count(*) OVER ()::int AS total
           FROM knowledge_item ki
           JOIN source_document d ON d.id = ki.document_id
           ${CITATION_JOIN}
           ${where}
          ORDER BY d.slug, ki.item_type, ki.title NULLS LAST
          LIMIT $${params.length - 1} OFFSET $${params.length}`,
        params,
      );
      return { items: rows as BrowseItem[], total: rows[0]?.total ?? 0 };
    }

    // The facet predicates are written against ki/d and have to apply inside
    // the ranking legs, not after them: rank-then-filter would return an empty
    // page whenever the top matches happened to miss the chosen facet.
    params.push(filters.q);
    const q = `$${params.length}`;
    params.push(`%${filters.q}%`);
    const like = `$${params.length}`;
    params.push(limit, offset);
    const limitP = `$${params.length - 1}`;
    const offsetP = `$${params.length}`;

    const { rows } = await client.query(
      `
      WITH base AS (
        SELECT ch.id AS chunk_id, ch.tsv, ch.text, ki.id AS item_id
          FROM chunk ch
          JOIN knowledge_item ki ON ki.id = ch.knowledge_item_id
          JOIN source_document d ON d.id = ki.document_id
         ${where}
      ),
      -- OR-of-lexemes, built in SQL so there is no client-side tokenising to
      -- keep in step with the 'english' dictionary.
      or_query AS (
        SELECT NULLIF(string_agg(lexeme, ' | '), '')::tsquery AS tsq
          FROM unnest(to_tsvector('english', ${q})) AS lexeme
      ),
      fts AS (
        SELECT chunk_id, item_id, text,
               ts_rank(tsv, websearch_to_tsquery('english', ${q})) AS rank
          FROM base
         WHERE tsv @@ websearch_to_tsquery('english', ${q})
      ),
      fts_or AS (
        SELECT b.chunk_id, b.item_id, b.text, ts_rank(b.tsv, o.tsq) AS rank
          FROM base b CROSS JOIN or_query o
         WHERE NOT EXISTS (SELECT 1 FROM fts)
           AND o.tsq IS NOT NULL AND b.tsv @@ o.tsq
      ),
      literal AS (
        SELECT b.chunk_id, b.item_id, b.text, 0.0::float4 AS rank
          FROM base b
         WHERE NOT EXISTS (SELECT 1 FROM fts)
           AND NOT EXISTS (SELECT 1 FROM fts_or)
           AND b.text ILIKE ${like}
      ),
      hits AS (
        SELECT * FROM fts UNION ALL SELECT * FROM fts_or UNION ALL SELECT * FROM literal
      ),
      -- One row per item, scored by its best chunk. max(), not sum(): summing
      -- would rank a long item above a precise one for being long.
      per_item AS (
        SELECT item_id,
               max(rank) AS score,
               (array_agg(text ORDER BY rank DESC))[1] AS best_text
          FROM hits GROUP BY item_id
      ),
      page AS (
        SELECT p.*, count(*) OVER ()::int AS total
          FROM per_item p
         ORDER BY p.score DESC, p.item_id
         LIMIT ${limitP} OFFSET ${offsetP}
      )
      SELECT ${SELECT_COLUMNS}, pg.score, pg.total,
             -- StartSel/StopSel are plain markers, split in the page. Returning
             -- HTML here would mean dangerouslySetInnerHTML on corpus text.
             ts_headline('english', pg.best_text,
                         websearch_to_tsquery('english', ${q}),
                         'MaxFragments=1,MaxWords=28,MinWords=10,StartSel=[[,StopSel=]]') AS snippet
        FROM page pg
        JOIN knowledge_item ki ON ki.id = pg.item_id
        JOIN source_document d ON d.id = ki.document_id
        ${CITATION_JOIN}
       ORDER BY pg.score DESC, ki.id
      `,
      params,
    );
    return { items: rows as BrowseItem[], total: rows[0]?.total ?? 0 };
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
