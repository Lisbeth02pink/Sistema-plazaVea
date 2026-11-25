"""Repair opening timestamps from the first Phase 4 migration and index ledger dates."""
from alembic import op
import sqlalchemy as sa

revision = "0005_kardex_opening_index"
down_revision = "0004_full_inventory_kardex"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE inventory_movements SET occurred_at='1900-01-01T00:00:00-05:00' "
        "WHERE reason='Saldo inicial al habilitar el kardex' AND occurred_at='0001-01-01T00:00:00-05:00'"
    ))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("inventory_movements")}
    if "idx_inventory_movements_occurred_at" not in indexes:
        op.create_index("idx_inventory_movements_occurred_at", "inventory_movements", ["occurred_at"])


def downgrade():
    # The date index is also part of the 0004 schema for fresh installations.
    pass
