"""SQLAlchemy-backed database access with a small SQLite-compatible facade.

The facade lets the existing API move to PostgreSQL without changing every
query in one step. Positional qmark parameters and legacy SQLite conflict
clauses are translated at this boundary; new code should use SQLAlchemy models.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
import os
import re
import sys
import threading
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, URL, make_url
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
_engine: Engine | None = None
_engine_url: str | None = None
_engine_lock = threading.Lock()


def configured_url() -> URL:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        return URL.create("sqlite", database=str(ROOT / "plaza_vea.db"))
    url = make_url(raw)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername.startswith("postgresql") and not url.database:
        raise RuntimeError("DATABASE_URL debe incluir el nombre de la base de datos PostgreSQL.")
    return url


def get_engine() -> Engine:
    global _engine, _engine_url
    url = configured_url()
    rendered = url.render_as_string(hide_password=False)
    with _engine_lock:
        if _engine is None or _engine_url != rendered:
            if _engine is not None:
                _engine.dispose()
            kwargs: dict[str, Any] = {"pool_pre_ping": True}
            if url.get_backend_name() == "sqlite":
                kwargs["connect_args"] = {"check_same_thread": False, "timeout": 10}
            else:
                kwargs.update(pool_size=5, max_overflow=10, pool_recycle=1800)
            _engine = create_engine(url, **kwargs)
            _engine_url = rendered
        return _engine


class Record:
    """Row that supports both SQLite's row['name'] and row[0] access."""

    def __init__(self, keys: tuple[str, ...], values: tuple[Any, ...]):
        self._keys = keys
        self._values = values
        self._mapping = dict(zip(keys, values))

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def keys(self) -> tuple[str, ...]:
        return self._keys

    def __len__(self) -> int:
        return len(self._keys)


class Result:
    def __init__(self, result, lastrowid: int | None = None):
        self._result = result
        self.lastrowid = lastrowid
        self.rowcount = result.rowcount
        self._keys = tuple(result.keys()) if result.returns_rows else ()

    def fetchone(self) -> Record | None:
        row = self._result.fetchone()
        return Record(self._keys, tuple(row)) if row is not None else None

    def fetchall(self) -> list[Record]:
        return [Record(self._keys, tuple(row)) for row in self._result.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


def _bind_sql(sql: str, params: Any, dialect: str) -> tuple[str, dict[str, Any]]:
    statement = sql.strip()
    if statement.upper() == "BEGIN IMMEDIATE":
        return "SELECT 1", {}

    if dialect == "postgresql":
        ignore_conflict = bool(re.match(r"^INSERT\s+OR\s+IGNORE\s+INTO", statement, flags=re.I))
        statement = re.sub(r"^INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", statement, flags=re.I)
        if ignore_conflict and "ON CONFLICT" not in statement.upper():
            statement += " ON CONFLICT DO NOTHING"
        if re.match(r"^INSERT\s+OR\s+REPLACE\s+INTO\s+login_locks", sql, flags=re.I):
            statement = re.sub(r"^INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", sql.strip(), flags=re.I)
            statement += " ON CONFLICT (fingerprint) DO UPDATE SET locked_until=EXCLUDED.locked_until"
        elif re.match(r"^INSERT\s+OR\s+REPLACE\s+INTO\s+deleted_roles", sql, flags=re.I):
            statement = re.sub(r"^INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", sql.strip(), flags=re.I)
            statement += " ON CONFLICT (name) DO UPDATE SET deleted_at=EXCLUDED.deleted_at"
        statement = re.sub(r"\bname\s*=\s*\?\s+COLLATE\s+NOCASE", "lower(name)=lower(?)", statement, flags=re.I)
        statement = re.sub(r"\bname\s*=\s*(:p\d+)\s+COLLATE\s+NOCASE", r"lower(name)=lower(\1)", statement, flags=re.I)
        statement = re.sub(r"ORDER BY\s+display_name\s+COLLATE\s+NOCASE", "ORDER BY lower(display_name)", statement, flags=re.I)
    if isinstance(params, Mapping):
        return statement, dict(params)
    values = tuple(params or ())
    index = 0

    def replace(_match):
        nonlocal index
        name = f"p{index}"
        index += 1
        return f":{name}"

    statement = re.sub(r"\?", replace, statement)
    return statement, {f"p{i}": value for i, value in enumerate(values)}


class CompatibleConnection(AbstractContextManager):
    def __init__(self):
        self._connection: Connection | None = None
        self._transaction = None
        self._dialect = get_engine().dialect.name

    def __enter__(self):
        self._connection = get_engine().connect()
        self._transaction = self._connection.begin()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._transaction and self._transaction.is_active:
                self._transaction.rollback() if exc_type else self._transaction.commit()
        finally:
            if self._connection:
                self._connection.close()
        return False

    def execute(self, sql: str, params: Any = ()) -> Result:
        statement, bound = _bind_sql(sql, params, self._dialect)
        if self._dialect == "postgresql" and re.match(r"^INSERT\s+INTO\s+(products|users)\b", statement, flags=re.I) and "RETURNING" not in statement.upper():
            statement += " RETURNING id"
        result = self._connection.execute(text(statement), bound)
        inserted_id = None
        if self._dialect == "postgresql" and "RETURNING id" in statement.upper():
            inserted_id = result.scalar_one()
        elif self._dialect == "sqlite" and re.match(r"^INSERT\s+INTO\s+(products|users)\b", statement, flags=re.I):
            inserted_id = result.lastrowid
        return Result(result, inserted_id)

    def executemany(self, sql: str, seq_of_params) -> Result:
        statement, _ = _bind_sql(sql, (), self._dialect)
        rows = []
        for params in seq_of_params:
            _, bound = _bind_sql(sql, params, self._dialect)
            rows.append(bound)
        result = self._connection.execute(text(statement), rows)
        return Result(result)


def connection() -> CompatibleConnection:
    return CompatibleConnection()


def initialize_database() -> None:
    """Apply versioned schema migrations before the application seeds defaults."""
    from alembic import command
    from alembic.config import Config

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    with get_engine().begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")


def database_backend() -> str:
    return get_engine().dialect.name
