// Data access. Two pools, two very different privilege levels — do not blur
// them:
//
//   tenantPool  — arch_app, via DATABASE_URL. RLS is FORCED on every table
//                 it touches. Every query through this pool MUST run inside
//                 withAccount(), which sets app.account_id for the
//                 transaction. There is no exported way to get a bare client
//                 off this pool.
//
//   authPool    — arch_auth, via AUTH_DB_URL. Reads allowed_account and
//                 touches last_seen_at. That is the whole of its privilege:
//                 it cannot SELECT a single corpus table (see db/roles.sql).
//
// Why the second pool exists: allowed_account's RLS policy is
// `USING (id = current_account_id())` — you can only ever see your own row.
// That is correct for the app's normal operation (a leaked tenant connection
// returns nothing), but it makes the *first* lookup, matching a Google email
// to an account id, structurally impossible through the tenant role: you
// would need to already know the id to be allowed to look it up by email.
// Confirmed empirically against the live DB (arch_app returns 0 rows for any
// allowed_account SELECT until app.account_id already equals the target
// row's id).
//
// The tempting fix is to run that one query as a superuser. Don't. The web
// app is the most exposed surface here, and a superuser DSN in its
// environment turns any compromise of it into write access over the entire
// corpus, defeating every RLS control in db/schema.sql. arch_auth exists
// instead: SELECT on allowed_account, UPDATE on one timestamp column, and
// permission denied on everything else. Verified, not assumed.
import { Pool, type PoolClient } from "pg";

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `${name} is not set. Copy web/.env.example to web/.env and fill it in.`,
    );
  }
  return value;
}

let _tenantPool: Pool | null = null;
function tenantPool(): Pool {
  if (!_tenantPool) {
    _tenantPool = new Pool({ connectionString: requireEnv("DATABASE_URL") });
  }
  return _tenantPool;
}

let _authPool: Pool | null = null;
function authPool(): Pool {
  if (!_authPool) {
    _authPool = new Pool({
      connectionString: requireEnv("AUTH_DB_URL"),
      max: 3,
    });
  }
  return _authPool;
}

/**
 * Run `fn` inside a transaction with `app.account_id` set for its duration.
 * This is the ONLY sanctioned way to query tenant tables. RLS is FORCED, so
 * a query issued without this returns zero rows rather than erroring —
 * silent, not loud — which is exactly why we don't expose a bare client.
 */
export async function withAccount<T>(
  accountId: string,
  fn: (client: PoolClient) => Promise<T>,
): Promise<T> {
  const client = await tenantPool().connect();
  try {
    await client.query("BEGIN");
    // set_config(..., true) = LOCAL scope: cleared at COMMIT/ROLLBACK, so a
    // pooled connection can never leak one tenant's account id to the next
    // checkout. Parameterised — never string-interpolate the uuid.
    await client.query("SELECT set_config('app.account_id', $1, true)", [
      accountId,
    ]);
    const result = await fn(client);
    await client.query("COMMIT");
    return result;
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }
}

/** Sign-in-time lookup, as arch_auth. This role can read allowed_account and
 * nothing else, so a bug here cannot reach the corpus. */
export async function findAllowedAccountByEmail(email: string): Promise<{
  id: string;
  role: string;
  status: string;
} | null> {
  const { rows } = await authPool().query(
    `SELECT id, role, status FROM allowed_account WHERE lower(email) = lower($1) LIMIT 1`,
    [email],
  );
  return rows[0] ?? null;
}

export async function touchLastSeen(accountId: string): Promise<void> {
  // Goes through the auth pool because arch_app cannot write allowed_account
  // at all -- the table is administered out of band. arch_auth holds a
  // column-level UPDATE grant on last_seen_at only; role, status and email
  // are unreachable to it.
  await authPool().query(
    `UPDATE allowed_account SET last_seen_at = now() WHERE id = $1`,
    [accountId],
  );
}
