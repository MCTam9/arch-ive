"""Manage who may sign in. The allowlist is the access control.

    python -m tools.manage_allowlist list
    python -m tools.manage_allowlist add someone@example.com --role reader
    python -m tools.manage_allowlist set-role someone@example.com editor
    python -m tools.manage_allowlist revoke someone@example.com
    python -m tools.manage_allowlist restore someone@example.com

Adding an approved person was a hand-written INSERT every time, which is
exactly the kind of repeated deterministic operation that belongs in a tool:
it is the control that decides who reads a confidential corpus, and doing it
from memory at a psql prompt is how a wrong role or a typo'd email gets in.

`allowed_account` is managed **out of band, never by the app role** (db/schema.sql).
So this connects with an admin DSN from ADMIN_DATABASE_URL and refuses to run
as arch_app, arch_read or arch_auth -- if the web app could write this table,
a compromise of the web app would be able to grant itself access.

Every change writes an audit_log row. Revocation takes effect on the next
sign-in check, not instantly for a live session.
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

APP_ROLES = ("arch_app", "arch_read", "arch_auth")
ROLES = ("owner", "editor", "reader")


def _dsn() -> str:
    dsn = os.environ.get("ADMIN_DATABASE_URL") or os.environ.get("NEON_ADMIN_URL")
    if not dsn:
        raise SystemExit(
            "set ADMIN_DATABASE_URL (or NEON_ADMIN_URL) -- the allowlist is deliberately "
            "not writable by the application role"
        )
    for role in APP_ROLES:
        if f"//{role}:" in dsn or f"//{role}@" in dsn:
            raise SystemExit(
                f"refusing to manage the allowlist as {role}: that role must never be able "
                f"to grant access. Use the admin DSN."
            )
    return dsn


def _connect():
    """Not autocommit, deliberately.

    A change to who can read the corpus and the audit row recording it are one
    fact. The first version ran autocommit and serialised the whole existing
    row into the audit detail -- the UUID blew up json.dumps, the UPDATE had
    already committed, and the result was an access change with no audit trail.
    One transaction per command means that cannot happen.
    """
    return psycopg.connect(_dsn(), row_factory=dict_row)


def _show(conn) -> None:
    rows = conn.execute(
        "SELECT email, role, status, invited_at::date AS invited, last_seen_at "
        "FROM allowed_account ORDER BY invited_at"
    ).fetchall()
    if not rows:
        print("allowlist is empty -- nobody can sign in")
        return
    print(f"{'email':<34} {'role':<7} {'status':<8} {'invited':<11} last seen")
    for r in rows:
        seen = r["last_seen_at"].strftime("%Y-%m-%d %H:%M") if r["last_seen_at"] else "never"
        print(f"{r['email']:<34} {r['role']:<7} {r['status']:<8} {str(r['invited']):<11} {seen}")


def _audit(conn, account_id, action: str, detail: dict) -> None:
    conn.execute(
        "INSERT INTO audit_log (account_id, action, detail) VALUES (%s, %s, %s)",
        (account_id, action, Jsonb({**detail, "via": "tools.manage_allowlist"})),
    )


def _find(conn, email: str) -> dict | None:
    return conn.execute(
        "SELECT id, email, role, status FROM allowed_account WHERE lower(email) = lower(%s)",
        (email,),
    ).fetchone()


def cmd_add(conn, email: str, role: str) -> int:
    if _find(conn, email):
        print(f"{email} is already on the allowlist -- use set-role or restore", file=sys.stderr)
        return 1
    row = conn.execute(
        "INSERT INTO allowed_account (email, role) VALUES (%s, %s) RETURNING id",
        (email, role),
    ).fetchone()
    _audit(conn, row["id"], "allowlist_add", {"email": email, "role": role})
    print(f"added {email} as {role}")
    return 0


def _update(conn, email: str, action: str, **fields) -> int:
    existing = _find(conn, email)
    if not existing:
        print(f"{email} is not on the allowlist", file=sys.stderr)
        return 1
    sets = ", ".join(f"{k} = %s" for k in fields)
    conn.execute(
        f"UPDATE allowed_account SET {sets} WHERE id = %s",
        (*fields.values(), existing["id"]),
    )
    before = {k: existing[k] for k in ("role", "status")}
    _audit(conn, existing["id"], action, {"email": email, "from": before, **fields})
    print(f"{email}: {action.replace('allowlist_', '')} -> {', '.join(f'{k}={v}' for k, v in fields.items())}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p_add = sub.add_parser("add")
    p_add.add_argument("email")
    p_add.add_argument("--role", choices=ROLES, default="reader")
    p_role = sub.add_parser("set-role")
    p_role.add_argument("email")
    p_role.add_argument("role", choices=ROLES)
    for name in ("revoke", "restore"):
        p = sub.add_parser(name)
        p.add_argument("email")
    args = ap.parse_args(argv)

    with _connect() as conn:
        if args.cmd == "list":
            _show(conn)
            return 0
        with conn.transaction():
            if args.cmd == "add":
                rc = cmd_add(conn, args.email, args.role)
            elif args.cmd == "set-role":
                rc = _update(conn, args.email, "allowlist_set_role", role=args.role)
            elif args.cmd == "revoke":
                rc = _update(conn, args.email, "allowlist_revoke", status="revoked")
            else:
                rc = _update(conn, args.email, "allowlist_restore", status="active")
            if rc:
                conn.rollback()
        return rc


if __name__ == "__main__":
    raise SystemExit(main())
