// Defensive column selection. Another agent may add columns to `requirement`
// (a scope dimension was flagged as in-flight) while this app is being
// built and run. Querying with an explicit column list is normally the
// right call for its own sake — no accidental `SELECT *` drift — but here it
// also has to survive a column that doesn't exist YET. We ask
// information_schema which of our "wanted" columns actually exist and build
// the SELECT from the intersection, so a not-yet-added column is silently
// omitted instead of throwing, and starts appearing automatically once it
// lands (per-process cache, so a dev server picks it up on restart).
import type { PoolClient } from "pg";

const columnCache = new Map<string, Set<string>>();
const tableCache = new Map<string, boolean>();

/** Same idea as existingColumns, but for a whole relation — used for the
 * requirement_scope / requirement_scope_applicability tables, which may not
 * exist yet depending on which migration a given environment has applied. */
export async function tableExists(client: PoolClient, table: string): Promise<boolean> {
  const cached = tableCache.get(table);
  if (cached !== undefined) return cached;
  const { rows } = await client.query(
    `SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = $1`,
    [table],
  );
  const exists = rows.length > 0;
  tableCache.set(table, exists);
  return exists;
}

export async function existingColumns(
  client: PoolClient,
  table: string,
): Promise<Set<string>> {
  const cached = columnCache.get(table);
  if (cached) return cached;
  const { rows } = await client.query<{ column_name: string }>(
    `SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = $1`,
    [table],
  );
  const set = new Set(rows.map((r) => r.column_name));
  columnCache.set(table, set);
  return set;
}

/** Intersect a wanted column list with what's actually there, preserving order. */
export function pickExisting(existing: Set<string>, wanted: string[]): string[] {
  return wanted.filter((c) => existing.has(c));
}

/** Build `alias.col AS col, ...` for a SELECT clause from the intersection. */
export async function selectList(
  client: PoolClient,
  table: string,
  alias: string,
  wanted: string[],
): Promise<string> {
  const existing = await existingColumns(client, table);
  const cols = pickExisting(existing, wanted);
  return cols.map((c) => `${alias}.${c}`).join(", ");
}
