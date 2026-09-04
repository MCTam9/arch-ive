"""Database access for the pipeline.

Every connection sets `app.account_id`, because row-level security is on and
FORCED for the app role -- an unset account sees nothing at all. Pipeline runs
use the owner account from ARCHIVE_ACCOUNT_ID.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import psycopg
from psycopg.rows import dict_row

DEFAULT_DSN = "postgresql://arch_app:dev@localhost:55432/postgres"
DEFAULT_ACCOUNT = "00000000-0000-0000-0000-0000000000aa"


def dsn() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DSN)


def account_id() -> str:
    return os.environ.get("ARCHIVE_ACCOUNT_ID", DEFAULT_ACCOUNT)


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """A connection with the RLS account applied for its whole lifetime."""
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.account_id', %s, false)", (account_id(),))
        yield conn


@contextmanager
def transaction() -> Iterator[psycopg.Connection]:
    """One stage, one transaction. A partially-loaded document is never visible."""
    with connect() as conn:
        with conn.transaction():
            yield conn


def one(conn: psycopg.Connection, sql: str, params: Sequence[Any] = ()) -> dict | None:
    return conn.execute(sql, params).fetchone()


def all_rows(conn: psycopg.Connection, sql: str, params: Sequence[Any] = ()) -> list[dict]:
    return conn.execute(sql, params).fetchall()


def scalar(conn: psycopg.Connection, sql: str, params: Sequence[Any] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    if not row:
        return None
    return next(iter(row.values()))


def insert_returning_id(conn: psycopg.Connection, table: str, values: dict[str, Any]) -> Any:
    """INSERT one row, return its id. Column names come from code, never user input."""
    cols = list(values)
    placeholders = ", ".join(["%s"] * len(cols))
    sql = (
        f'INSERT INTO {table} ({", ".join(cols)}) '
        f"VALUES ({placeholders}) RETURNING id"
    )
    return scalar(conn, sql, [values[c] for c in cols])
