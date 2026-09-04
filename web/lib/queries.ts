// Data access for the browse / item / matrix / ingest routes. Every function
// takes an accountId and runs inside withAccount() (lib/db.ts) so RLS scopes
// it correctly. Column lists are explicit — see lib/schema.ts for how the
// requirement query tolerates a column (e.g. an in-flight "scope" dimension)
// that may not exist yet.
import type { PoolClient } from "pg";
import { withAccount } from "./db";
import { selectList, existingColumns, pickExisting, tableExists } from "./schema";

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

export type TaxonomyTerm = { id: string; label: string };
export type DocumentOption = { slug: string; title: string | null; doc_kind: string };

export type FacetOptions = {
  documents: DocumentOption[];
  topics: TaxonomyTerm[];
  scales: TaxonomyTerm[];
  levels: TaxonomyTerm[];
};

export async function getFacetOptions(accountId: string): Promise<FacetOptions> {
  return withAccount(accountId, async (client) => {
    const [documents, topics, scales, levels] = await Promise.all([
      client.query<DocumentOption>(
        `SELECT slug, title, doc_kind::text AS doc_kind FROM source_document WHERE is_current ORDER BY slug`,
      ),
      client.query<TaxonomyTerm>(
        `SELECT id, label FROM taxonomy_term WHERE taxonomy_id = 'topic' ORDER BY path NULLS LAST, ordinal`,
      ),
      client.query<TaxonomyTerm>(
        `SELECT id, label FROM taxonomy_term WHERE taxonomy_id = 'scale' ORDER BY ordinal`,
      ),
      client.query<TaxonomyTerm>(
        `SELECT id, label FROM taxonomy_term WHERE taxonomy_id = 'level' ORDER BY ordinal`,
      ),
    ]);
    return {
      documents: documents.rows,
      topics: topics.rows,
      scales: scales.rows,
      levels: levels.rows,
    };
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
  doc_kind: string;
  page_index: number | null;
  printed_page_label: string | null;
};

export async function listKnowledgeItems(
  accountId: string,
  filters: BrowseFilters,
  limit = 80,
): Promise<BrowseItem[]> {
  return withAccount(accountId, async (client) => {
    const conditions: string[] = [];
    const params: unknown[] = [];
    const push = (sql: string, value: unknown) => {
      params.push(value);
      conditions.push(sql.replace("?", `$${params.length}`));
    };

    if (filters.documentSlug) push("d.slug = ?", filters.documentSlug);
    if (filters.itemType) push("ki.item_type = ?::item_type", filters.itemType);
    if (filters.topicId)
      push(
        "EXISTS (SELECT 1 FROM item_term it WHERE it.knowledge_item_id = ki.id AND it.term_id = ?)",
        filters.topicId,
      );
    if (filters.scaleId)
      push(
        "EXISTS (SELECT 1 FROM item_term it WHERE it.knowledge_item_id = ki.id AND it.term_id = ?)",
        filters.scaleId,
      );
    if (filters.levelId)
      push(
        "EXISTS (SELECT 1 FROM item_term it WHERE it.knowledge_item_id = ki.id AND it.term_id = ?)",
        filters.levelId,
      );
    if (filters.q) {
      const p = `%${filters.q}%`;
      params.push(p, p, p);
      const n = params.length;
      conditions.push(
        `(ki.title ILIKE $${n - 2} OR ki.statement ILIKE $${n - 1} OR ki.summary ILIKE $${n})`,
      );
    }

    const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
    params.push(limit);

    const { rows } = await client.query<BrowseItem>(
      `
      SELECT ki.id, ki.item_type::text AS item_type, ki.title, ki.statement, ki.summary,
             ki.content_status::text AS content_status, ki.review_status::text AS review_status,
             d.slug AS document_slug, d.doc_kind::text AS doc_kind,
             c.page_index, c.printed_page_label
      FROM knowledge_item ki
      JOIN source_document d ON d.id = ki.document_id
      LEFT JOIN LATERAL (
        SELECT page_index, printed_page_label FROM citation
        WHERE citation.knowledge_item_id = ki.id
        ORDER BY page_index NULLS LAST LIMIT 1
      ) c ON true
      ${where}
      ORDER BY d.slug, ki.item_type, ki.title NULLS LAST
      LIMIT $${params.length}
      `,
      params,
    );
    return rows;
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
      "scope", // in-flight column (see lib/schema.ts) — included only if present
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
  }[];
  terms: { taxonomy_id: string; label: string }[];
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
      const reqCols = await existingColumns(client, "requirement");
      const extra = pickExisting(reqCols, ["scope"]).map((c) => `r.${c}`).join(", ");
      const { rows } = await client.query(
        `
        SELECT r.requirement_kind::text AS requirement_kind, r.target_text, r.target_value,
               u.symbol AS unit, r.comparator::text AS comparator, r.is_deliverable,
               r.deliverable_name, r.parsed_ok,
               rl.ordinal AS level_ordinal, rl.code AS level_code, rl.name AS level_name,
               c.code AS criterion_code, coalesce(c.title_primary, c.title_alt) AS criterion_title,
               m.name AS metric_name
               ${extra ? `, ${extra}` : ""}
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
      const subtype = SUBTYPE_TABLES[itemType];
      if (subtype) {
        const cols = await selectList(client, subtype.table, "t", subtype.columns);
        if (cols) {
          const { rows } = await client.query(
            `SELECT ${cols} FROM ${subtype.table} t WHERE t.knowledge_item_id = $1`,
            [id],
          );
          payload = rows[0] ?? null;
        }
      }
    }

    const citations = await client.query(
      `SELECT c.page_index, c.printed_page_label, d.slug AS document_slug
       FROM citation c JOIN source_document d ON d.id = c.document_id
       WHERE c.knowledge_item_id = $1
       ORDER BY c.page_index NULLS LAST`,
      [id],
    );

    const terms = await client.query(
      `SELECT tt.taxonomy_id, tt.label
       FROM item_term it JOIN taxonomy_term tt ON tt.id = it.term_id
       WHERE it.knowledge_item_id = $1
       ORDER BY tt.taxonomy_id`,
      [id],
    );

    let scopes: ItemDetail["scopes"] = [];
    if (itemType === "requirement" && (await tableExists(client, "requirement_scope_applicability"))) {
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
  extra: Record<string, unknown>;
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

    // degrade gracefully if `scope` (or any other future column) isn't on
    // requirement yet — see lib/schema.ts
    const reqCols = await existingColumns(client, "requirement");
    const extraCols = pickExisting(reqCols, ["scope"]);
    const extraSelect = extraCols.map((c) => `r.${c}`).join(", ");

    const cellsRes = await client.query(
      `
      SELECT r.criterion_id::text AS criterion_id, r.rating_level_id::text AS rating_level_id,
             ki.id::text AS knowledge_item_id, ki.statement,
             r.target_text, u.symbol AS unit, r.comparator::text AS comparator,
             r.is_deliverable, ki.content_status::text AS content_status,
             ki.review_status::text AS review_status,
             cit.page_index
             ${extraSelect ? `, ${extraSelect}` : ""}
      FROM requirement r
      JOIN knowledge_item ki ON ki.id = r.knowledge_item_id
      JOIN criterion c ON c.id = r.criterion_id
      LEFT JOIN unit u ON u.id = r.unit_id
      LEFT JOIN LATERAL (
        SELECT page_index FROM citation WHERE citation.knowledge_item_id = ki.id ORDER BY page_index LIMIT 1
      ) cit ON true
      WHERE c.framework_id = $1
      `,
      [framework.id],
    );

    const cells = new Map<string, MatrixCell[]>();
    for (const row of cellsRes.rows) {
      const key = `${row.criterion_id}::${row.rating_level_id}`;
      const { criterion_id, rating_level_id, ...rest } = row;
      void criterion_id;
      void rating_level_id;
      const extra: Record<string, unknown> = {};
      for (const c of extraCols) extra[c] = row[c];
      const list = cells.get(key) ?? [];
      list.push({ ...(rest as MatrixCell), extra });
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
  page_index: number | null;
  printed_page_label: string | null;
  page_image_key: string | null;
};

/** Items still awaiting a human decision, lowest confidence first — the ones
 *  most likely to be wrong are the ones worth a reviewer's time. */
export async function listReviewQueue(
  accountId: string,
  opts: { document?: string; limit?: number; offset?: number } = {},
): Promise<{ items: ReviewItem[]; total: number }> {
  const limit = Math.min(opts.limit ?? 25, 100);
  const offset = opts.offset ?? 0;
  return withAccount(accountId, async (client) => {
    const where: string[] = ["k.review_status = 'pending'"];
    const params: unknown[] = [];
    if (opts.document) {
      params.push(opts.document);
      where.push(`d.slug = $${params.length}`);
    }
    const clause = where.join(" AND ");

    const totalRes = await client.query(
      `SELECT count(*)::int AS n FROM knowledge_item k
         JOIN source_document d ON d.id = k.document_id
        WHERE ${clause}`,
      params,
    );

    const rows = await client.query(
      `SELECT k.id, k.item_type::text, k.title, k.statement,
              k.content_status::text, k.extraction_confidence,
              d.slug AS document_slug,
              c.page_index, c.printed_page_label, p.page_image_key
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
    return { items: rows.rows as ReviewItem[], total: totalRes.rows[0].n as number };
  });
}

/** Record a reviewer's decision and log who made it. Editors and owners only;
 *  the caller enforces role, this enforces the audit trail. */
export async function recordReview(
  accountId: string,
  itemId: string,
  decision: "approved" | "rejected",
): Promise<void> {
  await withAccount(accountId, async (client) => {
    await client.query(
      "UPDATE knowledge_item SET review_status = $1, reviewed_at = now() WHERE id = $2",
      [decision, itemId],
    );
    await client.query(
      `INSERT INTO audit_log (account_id, action, knowledge_item_id)
       VALUES ($1, $2, $3)`,
      [accountId, `review:${decision}`, itemId],
    );
  });
}
