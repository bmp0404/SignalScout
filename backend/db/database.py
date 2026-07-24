"""Database connection provider + schema initialization.

Two backends behind one interface:
- SQLite (default): local dev, demos, the backtest. Path from SIGNAL_SCOUT_DB.
- Postgres: used automatically when DATABASE_URL is set (e.g. on Railway).

Repositories keep writing SQLite-flavored SQL (qmark params, INSERT OR REPLACE);
PostgresConnection translates it on the fly so both backends run the same code.
"""

import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

try:  # psycopg is only required when DATABASE_URL is set (see requirements.txt)
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - local SQLite-only installs
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_INSERT_OR_REPLACE_RE = re.compile(
    r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+(?P<table>[\w\"]+)\s*\((?P<columns>[^)]+)\)",
    re.IGNORECASE | re.DOTALL,
)
_INSERT_OR_IGNORE_RE = re.compile(
    r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+(?P<table>[\w\"]+)",
    re.IGNORECASE,
)
_INSERT_RE = re.compile(r"^\s*INSERT\s+INTO\s+(?P<table>[\w\"]+)", re.IGNORECASE)
_UPSERT_ARITHMETIC_RE = re.compile(
    r"(?P<prefix>\bDO\s+UPDATE\s+SET\s+)(?P<column>\w+)(?P<equals>\s*=\s*)"
    r"(?P=column)(?=\s*[-+*/])",
    re.IGNORECASE,
)


def _translate_placeholders(sql: str) -> str:
    """qmark ('?') -> psycopg ('%s'), skipping question marks inside string literals."""
    out: list[str] = []
    in_string = False
    for ch in sql:
        if ch == "'":
            in_string = not in_string
        if ch == "?" and not in_string:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


def _split_statements(script: str) -> list[str]:
    """Split a SQL script on ';' (executescript equivalent), respecting
    single-quoted literals and skipping '--' line comments."""
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    in_comment = False
    i = 0
    while i < len(script):
        ch = script[i]
        if in_comment:
            if ch == "\n":
                in_comment = False
                current.append(ch)
            i += 1
            continue
        if ch == "'":
            in_string = not in_string
        elif not in_string and ch == "-" and script[i : i + 2] == "--":
            in_comment = True
            i += 2
            continue
        if ch == ";" and not in_string:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


class PostgresConnection:
    """Thin shim exposing the sqlite3.Connection surface the repositories use
    (execute/commit/close/executescript) on top of a psycopg connection.

    Rows come back as dicts (psycopg dict_row), so row["column"] access used
    throughout the repositories works unchanged.
    """

    def __init__(self, database_url: str):
        if psycopg is None:
            raise RuntimeError(
                "DATABASE_URL is set but psycopg is not installed. "
                "Run: pip install 'psycopg[binary]>=3.1'"
            )
        self._conn = psycopg.connect(database_url, row_factory=dict_row)
        self._pk_cache: dict[str, list[str]] = {}

    def execute(self, sql: str, params: Any = ()) -> Any:
        translated = self._translate(sql)
        if translated is None:  # SQLite-only statement (e.g. PRAGMA): no-op
            return self._conn.execute("SELECT 1 WHERE FALSE")
        return self._conn.execute(translated, tuple(params))

    def executescript(self, script: str) -> None:
        for statement in _split_statements(script):
            self._conn.execute(statement)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _translate(self, sql: str) -> str | None:
        stripped = sql.lstrip()
        if stripped.upper().startswith("PRAGMA"):
            return None
        sql = _translate_placeholders(sql)
        match = _INSERT_OR_REPLACE_RE.match(sql)
        if match:
            sql = self._to_upsert(sql, match)
        else:
            ignore_match = _INSERT_OR_IGNORE_RE.match(sql)
            if ignore_match:
                sql = self._to_insert_ignore(sql, ignore_match)
        sql = self._qualify_upsert_arithmetic(sql)
        return sql

    def _to_upsert(self, sql: str, match: "re.Match[str]") -> str:
        """INSERT OR REPLACE -> INSERT ... ON CONFLICT (pk) DO UPDATE (portable upsert)."""
        table = match.group("table").strip('"')
        columns = [c.strip().strip('"') for c in match.group("columns").split(",")]
        body = re.sub(r"^\s*INSERT\s+OR\s+REPLACE\s+", "INSERT ", sql, flags=re.IGNORECASE)
        pk_columns = self._primary_key(table)
        if not pk_columns:
            return body
        updates = [f"{c} = EXCLUDED.{c}" for c in columns if c not in pk_columns]
        conflict = f" ON CONFLICT ({', '.join(pk_columns)})"
        if updates:
            return body + conflict + " DO UPDATE SET " + ", ".join(updates)
        return body + conflict + " DO NOTHING"

    def _to_insert_ignore(self, sql: str, match: "re.Match[str]") -> str:
        """INSERT OR IGNORE -> INSERT ... ON CONFLICT (pk) DO NOTHING (portable no-clobber insert)."""
        table = match.group("table").strip('"')
        body = re.sub(r"^\s*INSERT\s+OR\s+IGNORE\s+", "INSERT ", sql, flags=re.IGNORECASE)
        pk_columns = self._primary_key(table)
        if not pk_columns:
            return body
        return body + f" ON CONFLICT ({', '.join(pk_columns)}) DO NOTHING"

    @staticmethod
    def _qualify_upsert_arithmetic(sql: str) -> str:
        """Disambiguate target columns from EXCLUDED columns in Postgres upserts."""
        insert = _INSERT_RE.match(sql)
        if not insert:
            return sql
        table = insert.group("table")
        return _UPSERT_ARITHMETIC_RE.sub(
            lambda match: (
                f"{match.group('prefix')}{match.group('column')}{match.group('equals')}"
                f"{table}.{match.group('column')}"
            ),
            sql,
        )

    def _primary_key(self, table: str) -> list[str]:
        """Primary-key columns for a table, looked up once from the catalog.

        Dynamic on purpose: works for any table in schema.sql, including ones
        added after this code was written.
        """
        if table not in self._pk_cache:
            cur = self._conn.execute(
                """SELECT kcu.column_name AS name
                   FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage kcu
                     ON kcu.constraint_name = tc.constraint_name
                    AND kcu.table_schema = tc.table_schema
                   WHERE tc.constraint_type = 'PRIMARY KEY'
                     AND tc.table_schema = 'public' AND tc.table_name = %s
                   ORDER BY kcu.ordinal_position""",
                (table,),
            )
            self._pk_cache[table] = [row["name"] for row in cur.fetchall()]
        return self._pk_cache[table]


class Database:
    def __init__(self, db_path: Path, database_url: str | None = None):
        self.db_path = db_path
        self.database_url = database_url if database_url is not None else os.environ.get("DATABASE_URL", "")
        self.backend: str = "postgres" if self.database_url else "sqlite"
        self._conn: sqlite3.Connection | PostgresConnection | None = None
        self._local = threading.local()
        self._sqlite_connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()

    @property
    def conn(self) -> "sqlite3.Connection | PostgresConnection":
        if self.backend == "postgres":
            if self._conn is None:
                self._conn = PostgresConnection(self.database_url)
            return self._conn

        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.db_path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            self._local.connection = connection
            with self._connections_lock:
                self._sqlite_connections.append(connection)
        return connection

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text())
        self.conn.commit()

    def reset(self) -> None:
        """Drop everything and recreate. Used by build_db for idempotent rebuilds."""
        if self.backend == "postgres":
            cur = self.conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            for row in cur.fetchall():
                self.conn.execute(f'DROP TABLE IF EXISTS "{row["tablename"]}" CASCADE')
            self.conn.commit()
            self.init_schema()
            return
        self.conn.execute("PRAGMA foreign_keys = OFF")
        cur = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        for (table,) in cur.fetchall():
            self.conn.execute(f"DROP TABLE IF EXISTS {table}")
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.init_schema()

    def close(self) -> None:
        if self.backend == "postgres" and self._conn is not None:
            self._conn.close()
            self._conn = None
            return
        with self._connections_lock:
            connections = self._sqlite_connections
            self._sqlite_connections = []
        for connection in connections:
            connection.close()
        self._local = threading.local()
