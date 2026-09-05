"""Bulk operations on the extraction review queue.

    python -m tools.review_queue --status
    python -m tools.review_queue --approve-all --yes
    python -m tools.review_queue --approve-all --document crib-water --yes
    python -m tools.review_queue --reset --yes          # approved -> pending

The queue exists because text-layer extraction reads plausibly and is wrong
often: the checks that found the duplicated nodes, the mis-routed doc_kind and
the two-codes-short role scope all came from comparing a record against the
page it came from. Approving in bulk asserts that a person checked these, and
nobody has. Use it when you want the queue cleared as a starting state, not as
a substitute for `workflows/verify_extraction.md`.

It is reversible (`--reset`) and it does not change what search returns:
`tools/search.py` and `tools/mcp_server.py` only ever exclude `rejected`.

Every item gets an `audit_log` row in the same format the web app writes
(`review:approved`, keyed to the item), so the trail is uniform whether a
decision came from the UI or from here -- and a later reviewer can tell a bulk
approval from a considered one by the timestamps.
"""
from __future__ import annotations

import argparse

from tools import db
from tools.env import load_env


def show_status(conn) -> int:
    rows = db.all_rows(
        conn,
        "SELECT review_status::text AS status, count(*)::int AS n "
        "FROM knowledge_item GROUP BY 1 ORDER BY 2 DESC",
    )
    if not rows:
        print("no knowledge items visible to this account")
        return 0
    for r in rows:
        print(f"  {r['status']:<10} {r['n']:>6}")
    return 0


def _pending_ids(conn, document: str | None) -> list[str]:
    sql = """
        SELECT k.id FROM knowledge_item k
          JOIN source_document d ON d.id = k.document_id
         WHERE k.review_status = 'pending'
    """
    params: tuple = ()
    if document:
        sql += " AND d.slug = %s"
        params = (document,)
    return [r["id"] for r in db.all_rows(conn, sql, params)]


def approve_all(conn, document: str | None) -> int:
    ids = _pending_ids(conn, document)
    scope = f" for {document}" if document else ""
    if not ids:
        print(f"nothing pending{scope}")
        return 0

    with conn.transaction():
        db._exec(
            conn,
            "UPDATE knowledge_item SET review_status = 'approved', reviewed_at = now() "
            "WHERE id = ANY(%s)",
            (ids,),
        )
        db._exec(
            conn,
            "INSERT INTO audit_log (account_id, action, knowledge_item_id) "
            "SELECT %s, 'review:approved', unnest(%s::uuid[])",
            (db.account_id(), ids),
        )
    print(f"approved {len(ids)} item(s){scope}, {len(ids)} audit row(s) written")
    return 0


def reset(conn) -> int:
    ids = [
        r["id"]
        for r in db.all_rows(conn, "SELECT id FROM knowledge_item WHERE review_status = 'approved'")
    ]
    if not ids:
        print("nothing approved to reset")
        return 0
    with conn.transaction():
        db._exec(
            conn,
            "UPDATE knowledge_item SET review_status = 'pending', reviewed_at = NULL "
            "WHERE id = ANY(%s)",
            (ids,),
        )
        db._exec(
            conn,
            "INSERT INTO audit_log (account_id, action, knowledge_item_id) "
            "SELECT %s, 'review:reset', unnest(%s::uuid[])",
            (db.account_id(), ids),
        )
    print(f"reset {len(ids)} item(s) to pending")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", action="store_true", help="counts by review_status")
    ap.add_argument("--approve-all", action="store_true", help="approve every pending item")
    ap.add_argument("--reset", action="store_true", help="put approved items back to pending")
    ap.add_argument("--document", help="limit to one document slug")
    ap.add_argument("--yes", action="store_true", help="required for anything that writes")
    args = ap.parse_args(argv)
    load_env()

    if (args.approve_all or args.reset) and not args.yes:
        raise SystemExit("refusing to write without --yes")
    if args.approve_all and args.reset:
        raise SystemExit("--approve-all and --reset are mutually exclusive")

    with db.connect() as conn:
        if args.approve_all:
            return approve_all(conn, args.document)
        if args.reset:
            return reset(conn)
        return show_status(conn)


if __name__ == "__main__":
    raise SystemExit(main())
