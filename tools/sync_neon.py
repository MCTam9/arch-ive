"""Refresh Neon's copy of the corpus, and prove it matches -- without a reset.

    python3 -m tools.sync_neon --check          # read-only; exit 1 on drift
    python3 -m tools.sync_neon --push --yes     # data-only refresh

`scripts/load_neon.sh` provisions: it creates the roles, drops and rebuilds the
schema, restores a full dump, and rotates all three passwords. That is the
right shape for standing a Neon project up, and the wrong shape for the thing
that actually keeps happening -- local dev moves on and Neon quietly does not.
Reaching for the provisioning script to fix a data gap is what wiped the
production allowlist (2026-09-08) and what left Vercel holding a password the
same run had rotated away, which surfaced days later as a sign-in refusal that
named the wrong cause entirely.

So this tool does the smaller thing, and refuses the bigger one:

  * it never issues DDL, so the schema, the views, the grants and the RLS
    policies on the target are not in play at all;
  * it never touches a role or a password, so nothing has to be re-copied into
    Vercel afterwards and no deployment goes stale behind it;
  * it never reads or writes `allowed_account` or `audit_log` rows. Those two
    describe the ENVIRONMENT, not the corpus snapshot -- the same rule
    `load_neon.sh` learned the hard way, stated once here as ENVIRONMENT_TABLES
    and enforced by leaving them out of every list this module builds.

## Why --check exists separately

The gap that prompted this was invisible to a row count: every table matched on
count while 21 printed page labels, 205 draft items and 3 document version
labels did not. `load_neon.sh` step 6/6 compares seven counts, so it reported a
clean load over a week-old corpus. --check compares VALUES, as an
order-independent md5 over every row of every corpus table, which cannot agree
by coincidence.

It also compares the two schemas column by column and the view layer definition
by definition. A data-only push cannot fix a missing column, and finding that
out halfway through a write is worse than being told first, so --push runs
--check's schema half and refuses on any difference: apply the migration (see
workflows/provision_database.md) and come back.

## Why the push is safe to interrupt

One transaction, and the last thing inside it -- before COMMIT -- is --check's
own value comparison against the rows just written. A push that would leave the
target differing from the source in any row of any corpus table rolls back
instead of committing. There is no partial state to discover later.

`audit_log.document_id` references `source_document` ON DELETE SET NULL, so
clearing the corpus would silently flatten the audit trail's document pointers
even though the rows survive. They are snapshotted inside the transaction and
restored for every document that still exists afterwards.
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from tools.env import load_env

# Not corpus. Never compared, never cleared, never copied. `allowed_account` is
# who may sign in to THIS deployment and local dev's copy is a single unusable
# placeholder row; `audit_log` is the record that makes a lost allowlist
# recoverable. Restoring dev's copy of either over production is a loss with no
# undo, and it has already happened once.
ENVIRONMENT_TABLES = ("allowed_account", "audit_log")

# Text rendering has to be pinned on both sides or the comparison invents
# differences: timestamptz renders in the session's TimeZone, dates in its
# DateStyle, and float8/vector in extra_float_digits. Local dev is Postgres 17
# and Neon is 18, so leaving any of these to the server default would report
# drift that is really two servers describing the same value.
SESSION_SETUP = (
    "SET TimeZone TO 'UTC'",
    "SET DateStyle TO 'ISO, YMD'",
    "SET extra_float_digits TO 1",
)


class SyncError(RuntimeError):
    """Anything that should stop the run with a message, not a traceback."""


# ── connections ──────────────────────────────────────────────────────────


def direct(url: str) -> str:
    """Neon's -pooler endpoint is PgBouncer in transaction mode: it cannot hold
    session state and cannot carry a COPY-driven transaction of this shape.
    Everything here goes over the direct endpoint."""
    return url.replace("-pooler.", ".")


def _source_dsn() -> str:
    # Host-side port and the superuser, deliberately: RLS is FORCED on 46 of
    # these tables, so reading the corpus as arch_app would return one
    # account's rows and push a silently partial snapshot.
    return os.environ.get("SYNC_SOURCE_URL") or os.environ.get(
        "DATABASE_URL_LOCAL", "postgresql://postgres:dev@localhost:55432/postgres"
    )


def _target_dsn() -> str:
    url = os.environ.get("SYNC_TARGET_URL") or os.environ.get("NEON_ADMIN_URL", "")
    if not url:
        raise SyncError("set NEON_ADMIN_URL in .env (or SYNC_TARGET_URL) -- see workflows/provision_database.md")
    return direct(url)


@contextmanager
def _open(dsn: str, label: str) -> Iterator[psycopg.Connection]:
    try:
        conn = psycopg.connect(dsn, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        # Never echo the DSN: it carries a password.
        raise SyncError(f"cannot reach the {label} database ({exc.__class__.__name__})") from exc
    with conn:
        for stmt in SESSION_SETUP:
            conn.execute(stmt)
        _assert_sees_every_row(conn, label)
        yield conn


def _assert_sees_every_row(conn: psycopg.Connection, label: str) -> None:
    """A connection subject to RLS sees one account's rows and says nothing
    about the rest. Copying from such a connection would push a partial corpus
    that verifies clean, because both sides would be filtered the same way.
    Refuse rather than find out later."""
    row = conn.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if not row or not (row["rolsuper"] or row["rolbypassrls"]):
        raise SyncError(
            f"the {label} connection is subject to row-level security "
            f"(current_user is neither superuser nor BYPASSRLS). It would see "
            f"one account's rows and report them as the whole corpus."
        )


# ── what is where ────────────────────────────────────────────────────────


def corpus_tables(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        """SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> ALL(%s)
            ORDER BY c.relname""",
        (list(ENVIRONMENT_TABLES),),
    ).fetchall()
    return [r["relname"] for r in rows]


def schema_signature(conn: psycopg.Connection) -> dict[str, str]:
    """One line per table: every column and its type, ordered by NAME.

    Deliberately not by ordinal position. A column added by ALTER TABLE lands
    at the end, while db/schema.sql declares it wherever it reads best, so a
    database built by migration and one built from schema.sql hold the same
    columns in different physical order -- `source_asset` does today. Nothing
    here depends on that order: the copy names its columns explicitly, and
    fingerprints project them in this same sorted order on both sides. Refusing
    a push over it would be refusing over a difference that cannot matter."""
    rows = conn.execute(
        """SELECT c.relname AS t,
                  string_agg(a.attname || ':' || format_type(a.atttypid, a.atttypmod)
                             || coalesce('=' || pg_get_expr(ad.adbin, ad.adrelid), ''),
                             ',' ORDER BY a.attname) AS sig
             FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
             JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
             LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
                                    AND a.attgenerated <> ''
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> ALL(%s)
            GROUP BY c.relname""",
        (list(ENVIRONMENT_TABLES),),
    ).fetchall()
    return {r["t"]: r["sig"] for r in rows}


def canonical_columns(conn: psycopg.Connection) -> dict[str, list[str]]:
    """Column names per table, name-sorted -- the order every comparison and
    every copy uses, so neither side's physical layout can leak into either.

    Read from the catalogue rather than parsed back out of the signature above:
    a type can contain a comma (`numeric(9,2)[]` on source_asset.bbox), so
    splitting that string on commas invents a column called `2)`.

    Generated columns are left out. COPY rejects them outright, and a value
    derived from columns that do match cannot itself differ -- while a
    generation EXPRESSION that differs is schema drift, which is where the
    signature above reports it and where a push cannot paper over it."""
    rows = conn.execute(
        """SELECT c.relname AS t, a.attname AS col
             FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
             JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> ALL(%s)
              AND a.attgenerated = ''
            ORDER BY c.relname, a.attname""",
        (list(ENVIRONMENT_TABLES),),
    ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["t"], []).append(r["col"])
    return out


def view_signature(conn: psycopg.Connection) -> dict[str, str]:
    """Definition plus security_invoker. The retrieval policy layer is views,
    and a view that reverts to security_definer serves the whole corpus to a
    connection that should see one account -- db/test_schema.sh exists for
    exactly that, and this is the cheap version of the same question."""
    rows = conn.execute(
        """SELECT c.relname AS v,
                  md5(pg_get_viewdef(c.oid, true)) AS body,
                  coalesce((SELECT option_value FROM pg_options_to_table(c.reloptions)
                             WHERE option_name = 'security_invoker'), 'false') AS invoker
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'v'"""
    ).fetchall()
    return {r["v"]: f"{r['body']}/invoker={r['invoker']}" for r in rows}


def fingerprints(conn: psycopg.Connection, columns: dict[str, list[str]]) -> dict[str, tuple[int, str]]:
    """Row count and an order-independent md5 of every row.

    Hashing each row and aggregating the hashes in sorted order means no
    primary key is needed and physical row order is irrelevant -- two databases
    holding the same rows agree, and two holding different VALUES under the same
    COUNT do not. That second case is the one that went unnoticed for a week.

    The row is built column by column from `columns` rather than as `x.*`,
    because `x.*::text` renders in physical column order and the two sides do
    not always share one. Same values, different layout, permanently different
    hash -- a difference the tool would report forever and no push could fix."""
    out: dict[str, tuple[int, str]] = {}
    for t, cols in columns.items():
        row_expr = ", ".join(f'x."{c}"' for c in cols)
        row = conn.execute(
            f'SELECT count(*)::int AS n, coalesce(md5(string_agg(h, \'\' ORDER BY h)), \'-\') AS fp '
            f'FROM (SELECT md5(ROW({row_expr})::text) AS h FROM "{t}" x) s'
        ).fetchone()
        out[t] = (row["n"], row["fp"])
    return out


# ── copy order ───────────────────────────────────────────────────────────


def copy_order(conn: psycopg.Connection, tables: list[str]) -> list[str]:
    """Parents before children. Foreign keys are checked per row as COPY runs,
    so this is not a nicety. The graph is acyclic today and the sort says so
    loudly if that ever changes."""
    edges = conn.execute(
        """SELECT src.relname AS child, tgt.relname AS parent
             FROM pg_constraint con
             JOIN pg_class src ON src.oid = con.conrelid
             JOIN pg_class tgt ON tgt.oid = con.confrelid
             JOIN pg_namespace n ON n.oid = src.relnamespace
            WHERE con.contype = 'f' AND n.nspname = 'public'"""
    ).fetchall()
    in_scope = set(tables)
    parents: dict[str, set[str]] = {t: set() for t in tables}
    for e in edges:
        if e["child"] in in_scope and e["parent"] in in_scope and e["child"] != e["parent"]:
            parents[e["child"]].add(e["parent"])

    ordered: list[str] = []
    done: set[str] = set()
    remaining = set(tables)
    while remaining:
        ready = sorted(t for t in remaining if not parents[t] - done)
        if not ready:
            raise SyncError(
                "foreign keys form a cycle among "
                + ", ".join(sorted(remaining))
                + " -- no copy order can satisfy them row by row"
            )
        ordered.extend(ready)
        done.update(ready)
        remaining -= set(ready)
    return ordered


def self_references(conn: psycopg.Connection) -> dict[str, tuple[str, str]]:
    """table -> (primary key column, self-referencing column).

    `doc_node.parent_id`, `criterion.parent_id`, `taxonomy_term.parent_id` and
    `source_document.supersedes_id`. Ordering between tables is not enough for
    these: a child row arriving before its parent fails the same check."""
    rows = conn.execute(
        """SELECT src.relname AS t, a.attname AS col, pk.attname AS pk
             FROM pg_constraint con
             JOIN pg_class src ON src.oid = con.conrelid
             JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = con.conkey[1]
             JOIN pg_constraint p ON p.conrelid = con.conrelid AND p.contype = 'p'
             JOIN pg_attribute pk ON pk.attrelid = con.conrelid AND pk.attnum = p.conkey[1]
             JOIN pg_namespace n ON n.oid = src.relnamespace
            WHERE con.contype = 'f' AND con.conrelid = con.confrelid
              AND n.nspname = 'public' AND array_length(con.conkey, 1) = 1"""
    ).fetchall()
    return {r["t"]: (r["pk"], r["col"]) for r in rows}


def _select_sql(conn: psycopg.Connection, table: str, cols: str,
                 self_ref: dict[str, tuple[str, str]]) -> str:
    if table not in self_ref:
        return f'SELECT {cols} FROM "{table}"'
    pk, parent = self_ref[table]
    # Roots first, then each generation. Rows are emitted parent-before-child,
    # which is all the FK asks for.
    return (
        f'WITH RECURSIVE d AS ('
        f'  SELECT "{pk}" AS k, 0 AS depth FROM "{table}" WHERE "{parent}" IS NULL'
        f'  UNION ALL'
        f'  SELECT t."{pk}", d.depth + 1 FROM "{table}" t JOIN d ON t."{parent}" = d.k'
        f') SELECT {cols} FROM "{table}" t JOIN d ON d.k = t."{pk}" ORDER BY d.depth'
    )


def _assert_reachable(conn: psycopg.Connection, table: str,
                       self_ref: dict[str, tuple[str, str]]) -> None:
    """The depth walk above starts at the rows with no parent. A cycle in the
    data would leave its members unreachable and silently drop them from the
    copy, so count what the walk reaches and refuse if it is not everything."""
    pk, parent = self_ref[table]
    row = conn.execute(
        f'WITH RECURSIVE d AS ('
        f'  SELECT "{pk}" AS k FROM "{table}" WHERE "{parent}" IS NULL'
        f'  UNION SELECT t."{pk}" FROM "{table}" t JOIN d ON t."{parent}" = d.k'
        f') SELECT (SELECT count(*) FROM d)::int AS reached, '
        f'         (SELECT count(*) FROM "{table}")::int AS total'
    ).fetchone()
    if row["reached"] != row["total"]:
        raise SyncError(
            f'{table}: {row["total"] - row["reached"]} row(s) are not reachable from a '
            f'root through "{parent}" -- the data has a cycle, and no row order '
            f"can satisfy a self-referencing foreign key across one"
        )


# ── check ────────────────────────────────────────────────────────────────


def check(src: psycopg.Connection, tgt: psycopg.Connection, verbose: bool = True) -> int:
    """Returns the number of differences found. Schema first: a column that is
    missing explains every value difference under it, and reporting 46 tables
    of drift when one migration is unapplied buries the actual answer."""
    problems = 0

    s_sig, t_sig = schema_signature(src), schema_signature(tgt)
    only_src = sorted(set(s_sig) - set(t_sig))
    only_tgt = sorted(set(t_sig) - set(s_sig))
    changed = sorted(t for t in set(s_sig) & set(t_sig) if s_sig[t] != t_sig[t])
    for t in only_src:
        print(f"  SCHEMA  {t:<34} on local, missing from neon")
    for t in only_tgt:
        print(f"  SCHEMA  {t:<34} on neon, missing from local")
    for t in changed:
        print(f"  SCHEMA  {t:<34} columns differ")
    problems += len(only_src) + len(only_tgt) + len(changed)

    s_view, t_view = view_signature(src), view_signature(tgt)
    for v in sorted(set(s_view) | set(t_view)):
        if s_view.get(v) != t_view.get(v):
            what = "missing from neon" if v not in t_view else (
                "missing from local" if v not in s_view else "definition or security_invoker differs")
            print(f"  VIEW    {v:<34} {what}")
            problems += 1

    if problems:
        print("\n  schema drift -- apply the migration to both before comparing values")
        print("  (workflows/provision_database.md: same sitting, direct endpoint)")
        return problems

    shared = sorted(set(s_sig) & set(t_sig))
    cols = {t: c for t, c in canonical_columns(src).items() if t in set(shared)}
    s_fp = fingerprints(src, cols)
    t_fp = fingerprints(tgt, cols)
    for t in shared:
        (sn, sf), (tn, tf) = s_fp[t], t_fp[t]
        if sf == tf:
            if verbose:
                print(f"  ok      {t:<34} {sn}")
        elif sn != tn:
            print(f"  DIFFERS {t:<34} local={sn} neon={tn}")
            problems += 1
        else:
            # The case row counts cannot see, and the reason this tool exists.
            print(f"  DIFFERS {t:<34} {sn} rows on both sides, values differ")
            problems += 1
    return problems


# ── push ─────────────────────────────────────────────────────────────────


def push(src: psycopg.Connection, tgt: psycopg.Connection) -> None:
    s_sig, t_sig = schema_signature(src), schema_signature(tgt)
    if s_sig != t_sig:
        raise SyncError(
            "the two schemas differ -- run --check for the detail. A data-only "
            "push cannot add a column; apply the migration to both first."
        )

    tables = corpus_tables(src)
    order = copy_order(src, tables)
    self_ref = self_references(src)
    for t in sorted(set(self_ref) & set(tables)):
        _assert_reachable(src, t, self_ref)

    canon = {t: c for t, c in canonical_columns(src).items() if t in set(tables)}
    cols = {t: ", ".join(f'"{c}"' for c in canon[t]) for t in tables}

    with tgt.transaction():
        # audit_log.document_id is ON DELETE SET NULL, so clearing
        # source_document below would quietly blank the audit trail's document
        # pointers. The rows survive either way; the pointers only survive if
        # they are carried across, which is this.
        tgt.execute(
            "CREATE TEMP TABLE _audit_doc ON COMMIT DROP AS "
            "SELECT id, document_id FROM audit_log WHERE document_id IS NOT NULL"
        )
        for t in reversed(order):
            tgt.execute(f'DELETE FROM "{t}"')

        copied = 0
        for t in order:
            sql = _select_sql(src, t, cols[t], self_ref)
            with src.cursor().copy(f"COPY ({sql}) TO STDOUT") as reader:
                with tgt.cursor().copy(f'COPY "{t}" ({cols[t]}) FROM STDIN') as writer:
                    for block in reader:
                        writer.write(block)
            copied += 1
            print(f"  copied  {t}", flush=True)

        tgt.execute(
            "UPDATE audit_log a SET document_id = s.document_id "
            "  FROM _audit_doc s WHERE a.id = s.id AND a.document_id IS NULL "
            "   AND EXISTS (SELECT 1 FROM source_document d WHERE d.id = s.document_id)"
        )

        # Verify before COMMIT, not after. A push that would leave any row of
        # any corpus table differing from the source raises here, and the
        # transaction takes the whole thing back out.
        s_fp = fingerprints(src, canon)
        t_fp = fingerprints(tgt, canon)
        bad = [t for t in tables if s_fp[t] != t_fp[t]]
        if bad:
            raise SyncError(
                "post-copy verification failed for " + ", ".join(bad) + " -- rolled back, "
                "the target is exactly as it was"
            )
        print(f"\n  verified {copied} table(s) identical to local, committing")

    kept = tgt.execute("SELECT count(*)::int AS n FROM allowed_account").fetchone()["n"]
    audit = tgt.execute("SELECT count(*)::int AS n FROM audit_log").fetchone()["n"]
    print(f"  kept     allowed_account {kept} row(s), audit_log {audit} row(s) (untouched)")
    if kept == 0:
        print("  NOTE: no allowlist rows on the target -- nobody can sign in until")
        print("        ADMIN_DATABASE_URL=... python3 -m tools.manage_allowlist add <email> --role owner")


# ── cli ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                     help="compare local and neon; exit 1 on any difference")
    ap.add_argument("--push", action="store_true",
                     help="replace neon's corpus rows with local's, in one transaction")
    ap.add_argument("--yes", action="store_true", help="required by --push")
    ap.add_argument("--quiet", action="store_true", help="--check prints only differences")
    args = ap.parse_args(argv)

    if args.check == args.push:
        ap.error("choose exactly one of --check or --push")
    if args.push and not args.yes:
        ap.error("--push rewrites every corpus row on the target; pass --yes")

    load_env()
    try:
        with _open(_source_dsn(), "local") as src, _open(_target_dsn(), "neon") as tgt:
            if args.check:
                n = check(src, tgt, verbose=not args.quiet)
                print(f"\nsync_neon: {'in sync' if n == 0 else f'{n} difference(s)'}")
                return 1 if n else 0
            push(src, tgt)
            print("\nsync_neon: pushed -- no schema, role or password was touched")
            return 0
    except SyncError as exc:
        print(f"sync_neon: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
