"""A thin database layer that runs the same SQL on SQLite and Postgres.

Queries are written once with `?` placeholders in the portable subset of SQL that
both engines share (single-quoted literals, `substr` on ISO date strings, `RETURNING`,
`ON CONFLICT ... DO UPDATE`). For Postgres the placeholders are rewritten to `%s`.

SQLite is the zero-setup default for local development and tests. Production on
Vercel must use Postgres (`DATABASE_URL`) because the serverless filesystem is
ephemeral.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterable
from decimal import Decimal
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from flask import current_app, g

SQLITE = 'sqlite'
POSTGRES = 'postgres'


def _normalize(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in row.items()}


@lru_cache(maxsize=512)
def _to_postgres(sql: str) -> str:
    # Every query in this codebase passes values as parameters, so a literal `%`
    # only appears in SQL text by accident; escape it before swapping placeholders.
    return sql.replace('%', '%%').replace('?', '%s')


def _sqlite_dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class Database:
    """Per-request connection wrapper exposing a small, explicit query API."""

    def __init__(self, conn, dialect: str):
        self.conn = conn
        self.dialect = dialect

    # -- query helpers -------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()):
        params = tuple(params)
        if self.dialect == POSTGRES:
            cur = self.conn.cursor()
            cur.execute(_to_postgres(sql), params)
            return cur
        return self.conn.execute(sql, params)

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return [_normalize(r) for r in self.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        return _normalize(self.execute(sql, params).fetchone())

    def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
        row = self.one(sql, params)
        if not row:
            return default
        value = next(iter(row.values()))
        return default if value is None else value

    def insert(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Run an INSERT ending in `RETURNING id` and return the new id."""
        row = self.execute(sql, params).fetchone()
        return int(row['id'])

    def script(self, statements: Iterable[str]) -> None:
        """Run DDL statements verbatim (no parameters, no placeholder rewriting)."""
        for stmt in statements:
            stmt = stmt.strip()
            if stmt:
                self.conn.execute(stmt)

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        # A broken connection has nothing left to undo.
        with contextlib.suppress(Exception):
            self.conn.rollback()

    def close(self) -> None:
        self.conn.close()

    def column_names(self, table: str) -> set[str]:
        if self.dialect == POSTGRES:
            rows = self.all(
                'SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?',
                (table,),
            )
            return {r['column_name'] for r in rows}
        return {r['name'] for r in self.all(f'PRAGMA table_info({table})')}


def integrity_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [sqlite3.IntegrityError]
    try:
        import psycopg

        errors.append(psycopg.errors.IntegrityError)
    except ImportError:  # psycopg is only needed when DATABASE_URL is set
        pass
    return tuple(errors)


def libpq_url(database_url: str) -> str:
    """Drop query parameters libpq doesn't understand.

    Marketplace integrations append their own markers (Supabase adds `supa=...`,
    Prisma-style URLs add `pgbouncer=true`), and libpq rejects unknown parameters.
    """
    parts = urlsplit(database_url)
    if not parts.query:
        return database_url
    from psycopg import pq

    known = {option.keyword.decode() for option in pq.Conninfo.get_defaults()}
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key in known]
    return urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))


def connect(database_url: str | None, sqlite_path: str) -> Database:
    if database_url:
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(
            libpq_url(database_url),
            row_factory=dict_row,
            connect_timeout=10,
            # Pooled endpoints (PgBouncer in transaction mode, e.g. Neon's default URL)
            # don't support server-side prepared statements.
            prepare_threshold=None,
        )
        return Database(conn, POSTGRES)

    conn = sqlite3.connect(sqlite_path, timeout=30)
    conn.row_factory = _sqlite_dict_factory
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA synchronous = NORMAL')
    conn.execute('PRAGMA busy_timeout = 5000')
    return Database(conn, SQLITE)


def get_db() -> Database:
    db = g.get('db')
    if db is None:
        settings = current_app.extensions['settings']
        db = connect(settings.database_url, settings.sqlite_path)
        g.db = db
    return db


def close_db(exception: BaseException | None = None) -> None:
    db = g.pop('db', None)
    if db is not None:
        # Anything not explicitly committed by a view is discarded.
        db.rollback()
        db.close()
