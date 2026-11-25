"""Track ordered receipt totals and prevent duplicate open requests per product."""
from alembic import op
import sqlalchemy as sa

revision = "0009_replenishment_concurrency"
down_revision = "0008_replenishment_workflow"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("replenishment_requests")}
    if "received_quantity" not in columns:
        op.add_column("replenishment_requests", sa.Column("received_quantity", sa.Integer(), nullable=True))
    if "open_product_key" not in columns:
        op.add_column("replenishment_requests", sa.Column("open_product_key", sa.String(24), nullable=True))
    op.execute("UPDATE replenishment_requests SET received_quantity=COALESCE((SELECT SUM(quantity) FROM replenishment_receipts WHERE replenishment_receipts.request_id=replenishment_requests.id),0)")
    op.execute("UPDATE replenishment_requests SET open_product_key=CAST(product_id AS VARCHAR(24)) WHERE status IN ('pending_review','approved','partially_received') AND product_id IN (SELECT product_id FROM replenishment_requests WHERE status IN ('pending_review','approved','partially_received') GROUP BY product_id HAVING COUNT(*)=1)")
    constraints = {item.get("name") for item in inspector.get_check_constraints("replenishment_requests")}
    received_column = next(item for item in sa.inspect(bind).get_columns("replenishment_requests") if item["name"] == "received_quantity")
    if received_column["nullable"] or "ck_replenishment_received_not_over_ordered" not in constraints:
        with op.batch_alter_table("replenishment_requests") as batch:
            if received_column["nullable"]:
                batch.alter_column("received_quantity", existing_type=sa.Integer(), nullable=False, server_default="0")
            if "ck_replenishment_received_not_over_ordered" not in constraints:
                batch.create_check_constraint("ck_replenishment_received_not_over_ordered", "received_quantity >= 0 AND received_quantity <= suggested_quantity")
    indexes = {item["name"] for item in inspector.get_indexes("replenishment_requests")}
    unique_constraints = inspector.get_unique_constraints("replenishment_requests")
    has_open_unique = any(item.get("column_names") == ["open_product_key"] for item in unique_constraints)
    has_open_unique = has_open_unique or any(item.get("unique") and item.get("column_names") == ["open_product_key"] for item in inspector.get_indexes("replenishment_requests"))
    if "uq_replenishment_open_product_key" not in indexes and not has_open_unique:
        op.create_index("uq_replenishment_open_product_key", "replenishment_requests", ["open_product_key"], unique=True)


def downgrade():
    # These fields are part of revision 0008's canonical schema. Revision 0009
    # only reconciles data and adds a unique index for databases that lack the
    # uniqueness declared by 0008; dropping fields here would make downgrade
    # produce a schema that no longer matches 0008.
    pass
