"""Create or adopt the persisted tables that currently exist in SQLite."""
from pathlib import Path
import sys

from alembic import op
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legacy_models import metadata

revision = "0001_existing_application_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    metadata.create_all(bind=bind, checkfirst=True)
    if bind.dialect.name == "sqlite":
        existing = {row[0] for row in bind.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index'")}
    else:
        inspector = sa.inspect(bind)
        existing = {item["name"] for table in metadata.tables.values() if inspector.has_table(table.name) for item in inspector.get_indexes(table.name)}
    for table in metadata.tables.values():
        for index in table.indexes:
            if index.name not in existing:
                index.create(bind=bind)
                existing.add(index.name)


def downgrade():
    metadata.drop_all(bind=op.get_bind(), checkfirst=True)
