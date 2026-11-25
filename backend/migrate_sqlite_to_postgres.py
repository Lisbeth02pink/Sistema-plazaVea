"""Copy the current SQLite records into an empty PostgreSQL database.

Run from the project root after setting DATABASE_URL in backend/.env:
    backend/.venv/Scripts/python.exe -m backend.migrate_sqlite_to_postgres

The source is opened read-only. The target must be empty; data is copied in a
single transaction, then row counts and content hashes are compared.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime

from sqlalchemy import MetaData, Table, text

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from database import configured_url, get_engine, initialize_database
from models import metadata

SOURCE = ROOT / "plaza_vea.db"
SEQUENCE_TABLES = ("products", "users", "audit_log", "security_reviews")


def _canonical(value):
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        return format(Decimal(str(value)).normalize(), "f")
    if isinstance(value, bytes):
        return value.hex()
    return value


def _digest(rows: list[dict]) -> str:
    encoded_rows = [
        json.dumps({key: _canonical(value) for key, value in row.items()}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in rows
    ]
    payload = "\n".join(sorted(encoded_rows)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_source() -> tuple[dict[str, list[dict]], dict[str, int], dict[str, int]]:
    if not SOURCE.is_file():
        raise RuntimeError(f"No se encontró la base SQLite de origen: {SOURCE}")
    source = sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        names = [row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE ? AND name<>? ORDER BY name",
            ("table", "sqlite_%", "alembic_version"),
        )]
        app_tables = set(metadata.tables)
        unknown = sorted(set(names) - app_tables)
        missing = sorted(app_tables - set(names))
        if unknown or missing:
            raise RuntimeError(f"El esquema fuente no coincide con la migración. Tablas ajenas={unknown}; faltantes={missing}.")
        records = {}
        for name in names:
            records[name] = [dict(row) for row in source.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
        sequences = {}
        if "sqlite_sequence" in [row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type=?", ("table",)
        )]:
            sequences = {name: int(value) for name, value in source.execute("SELECT name,seq FROM sqlite_sequence")}
        return records, {name: len(rows) for name, rows in records.items()}, sequences
    finally:
        source.close()


def _create_backup() -> Path:
    backup_dir = ROOT / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = backup_dir / f"plaza_vea_pre_postgresql_{stamp}.sqlite3"
    source = sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"La copia SQLite no superó integrity_check: {result}")
    except Exception:
        backup.unlink(missing_ok=True)
        raise
    finally:
        destination.close()
        source.close()
    return backup


def migrate() -> dict:
    url = configured_url()
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("Configura DATABASE_URL con PostgreSQL; no se copiarán datos a SQLite.")

    backup = _create_backup()
    records, source_counts, source_sequences = _read_source()
    initialize_database()
    engine = get_engine()
    reflected = MetaData()
    reflected.reflect(bind=engine, only=list(records))
    with engine.begin() as target:
        existing = {
            name: int(target.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one())
            for name in records
        }
        if any(existing.values()):
            raise RuntimeError(f"La base PostgreSQL destino no está vacía. Conteos actuales: {existing}")

        insertion_order = [name for name in metadata.sorted_tables if name.name in records]
        for table in insertion_order:
            rows = records[table.name]
            if rows:
                target.execute(reflected.tables[table.name].insert(), rows)

        for table_name in SEQUENCE_TABLES:
            if table_name not in records:
                continue
            source_sequence = source_sequences.get(table_name, 0)
            current_max = target.execute(text(f'SELECT MAX(id) FROM "{table_name}"')).scalar_one()
            sequence_value = max(int(current_max or 1), source_sequence)
            sequence_called = current_max is not None or source_sequence > 0
            target.execute(
                text("SELECT setval(pg_get_serial_sequence(:table_name, 'id'), :sequence_value, :sequence_called)"),
                {"table_name": table_name, "sequence_value": sequence_value, "sequence_called": sequence_called},
            )

        target_counts = {
            name: int(target.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one())
            for name in records
        }
        target_digests = {}
        for name in records:
            rows = [dict(row._mapping) for row in target.execute(reflected.tables[name].select())]
            target_digests[name] = _digest(rows)

        if source_counts != target_counts:
            raise RuntimeError(f"No coinciden los conteos; origen={source_counts}; destino={target_counts}")
        for name, rows in records.items():
            if _digest(rows) != target_digests[name]:
                raise RuntimeError(f"La verificación de contenido falló para la tabla {name}.")

    return {"tables": len(records), "source_counts": source_counts, "target_counts": target_counts,
            "source_sequences": source_sequences, "sequences_preserved": True,
            "hashes_match": True, "database": "postgresql", "backup": str(backup)}


if __name__ == "__main__":
    print(json.dumps(migrate(), ensure_ascii=False, indent=2))
