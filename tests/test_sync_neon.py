"""Tests for tools/sync_neon.py.

The two properties worth guarding are the two that were learned expensively:
a comparison that only counts rows misses the drift that actually happens, and
a refresh that treats `allowed_account` / `audit_log` as corpus destroys the
deployment it is meant to update.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from tools import sync_neon
from tests.conftest import TEST_DSN

ROOT = Path(__file__).resolve().parent.parent


def _privileged(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, row_factory=dict_row)
    for stmt in sync_neon.SESSION_SETUP:
        conn.execute(stmt)
    try:
        sync_neon._assert_sees_every_row(conn, "test")
    except sync_neon.SyncError:
        conn.close()
        pytest.skip(
            "needs a connection that is not subject to RLS -- run with "
            "TEST_DATABASE_URL=postgresql://postgres:dev@localhost:55432/arch_test"
        )
    return conn


@pytest.fixture
def src() -> psycopg.Connection:
    conn = _privileged(TEST_DSN)
    yield conn
    conn.close()


def test_the_environment_tables_are_invisible_to_every_list(src):
    """allowed_account and audit_log are not corpus. If they leak into any of
    these three, the push deletes production's allowlist and copies local dev's
    single unusable placeholder row over it -- which is what happened on
    2026-09-08, from load_neon.sh, and locked the owner out."""
    # Named literally, not read back out of ENVIRONMENT_TABLES: emptying that
    # tuple is precisely the regression, and a loop over it would then run zero
    # times and pass.
    for name in ("allowed_account", "audit_log"):
        assert name not in sync_neon.corpus_tables(src)
        assert name not in sync_neon.schema_signature(src)
        assert name not in sync_neon.canonical_columns(src)


def test_copy_order_puts_every_parent_before_its_child(src):
    order = sync_neon.copy_order(src, sync_neon.corpus_tables(src))
    at = {t: i for i, t in enumerate(order)}
    edges = src.execute(
        """SELECT s.relname AS child, p.relname AS parent
             FROM pg_constraint c
             JOIN pg_class s ON s.oid = c.conrelid
             JOIN pg_class p ON p.oid = c.confrelid
             JOIN pg_namespace n ON n.oid = s.relnamespace
            WHERE c.contype = 'f' AND n.nspname = 'public'"""
    ).fetchall()
    for e in edges:
        if e["child"] in at and e["parent"] in at and e["child"] != e["parent"]:
            assert at[e["parent"]] < at[e["child"]], (
                f'{e["parent"]} must be copied before {e["child"]}'
            )


def test_fingerprint_catches_a_changed_value_under_an_unchanged_count(src):
    """The whole reason this tool exists. load_neon.sh's own verification step
    compares seven row counts, and reported a clean load over a corpus that was
    a week stale: every count matched while 21 printed page labels, 205 draft
    items and 3 version labels did not."""
    cols = {"unit": sync_neon.canonical_columns(src)["unit"]}
    uid = f"unit-sync-{uuid.uuid4().hex[:8]}"
    src.execute("INSERT INTO unit (id, symbol, dimension) VALUES (%s, %s, %s)",
                 (uid, "before", "test"))
    before = sync_neon.fingerprints(src, cols)["unit"]
    src.execute("UPDATE unit SET symbol = %s WHERE id = %s", ("after", uid))
    after = sync_neon.fingerprints(src, cols)["unit"]
    src.execute("DELETE FROM unit WHERE id = %s", (uid,))
    src.commit()

    assert before[0] == after[0], "the row count is deliberately unchanged"
    assert before[1] != after[1], "one changed value must change the fingerprint"


def test_push_replaces_the_corpus_and_leaves_the_deployment_alone(src):
    """End to end against a scratch database: every corpus row is deleted and
    rewritten, and the two tables that say who may sign in -- and the audit
    trail's pointers into the documents that were just deleted and re-inserted
    -- come through untouched."""
    admin_dsn = TEST_DSN.rsplit("/", 1)[0] + "/postgres"
    scratch = f"arch_sync_t{uuid.uuid4().hex[:8]}"
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{scratch}"')
    except psycopg.errors.InsufficientPrivilege:
        pytest.skip("the test role may not CREATE DATABASE")

    target_dsn = TEST_DSN.rsplit("/", 1)[0] + "/" + scratch
    try:
        with psycopg.connect(target_dsn, autocommit=True) as setup:
            setup.execute((ROOT / "db" / "schema.sql").read_text())
        tgt = _privileged(target_dsn)
        try:
            account = uuid.uuid4()
            tgt.execute(
                "INSERT INTO allowed_account (id, email, role, status) VALUES (%s, %s, 'owner', 'active')",
                (account, f"{scratch}@example.invalid"),
            )
            # First push gives the scratch database its documents, so the audit
            # rows below can point at real ones.
            sync_neon.push(src, tgt)

            tgt.execute(
                "INSERT INTO audit_log (account_id, action, document_id, detail) "
                "SELECT %s, 'download', d.id, '{}'::jsonb FROM source_document d",
                (account,),
            )
            tgt.commit()
            before = tgt.execute(
                "SELECT count(*)::int AS total, "
                "       count(document_id)::int AS with_doc FROM audit_log"
            ).fetchone()
            assert before["with_doc"] > 0, "the fixture must exercise the pointer"

            # The second push deletes and re-inserts every source_document row,
            # which is what would flatten those pointers via ON DELETE SET NULL.
            sync_neon.push(src, tgt)

            after = tgt.execute(
                "SELECT count(*)::int AS total, "
                "       count(document_id)::int AS with_doc FROM audit_log"
            ).fetchone()
            assert after == before, "the audit trail must survive a push intact"
            assert tgt.execute(
                "SELECT count(*)::int AS n FROM allowed_account"
            ).fetchone()["n"] == 1

            assert sync_neon.check(src, tgt, verbose=False) == 0
        finally:
            tgt.close()
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
