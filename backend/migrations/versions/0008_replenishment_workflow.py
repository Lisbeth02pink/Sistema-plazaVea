"""Persist replenishment requests, decisions and partial receipts."""
from alembic import op
import sqlalchemy as sa

revision = "0008_replenishment_workflow"
down_revision = "0007_intelligent_alerts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "replenishment_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reference", sa.String(40), nullable=False, unique=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("suggested_quantity", sa.Integer(), nullable=False),
        sa.Column("received_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stock_at_request", sa.Integer(), nullable=False),
        sa.Column("incoming_at_request", sa.Integer(), nullable=False),
        sa.Column("demand_during_lead", sa.Float(), nullable=False),
        sa.Column("reorder_point", sa.Integer(), nullable=False),
        sa.Column("demand_source", sa.String(40), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending_review"),
        sa.Column("request_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_at", sa.String(40)),
        sa.Column("decision_note", sa.Text()),
        sa.Column("open_product_key", sa.String(24), unique=True),
        sa.CheckConstraint("suggested_quantity > 0", name="ck_replenishment_quantity_positive"),
        sa.CheckConstraint("received_quantity >= 0 AND received_quantity <= suggested_quantity", name="ck_replenishment_received_not_over_ordered"),
        sa.CheckConstraint("stock_at_request >= 0 AND incoming_at_request >= 0 AND demand_during_lead >= 0 AND reorder_point >= 0", name="ck_replenishment_snapshot_nonnegative"),
        sa.CheckConstraint("lead_time_days > 0", name="ck_replenishment_lead_time_positive"),
        sa.CheckConstraint("priority IN ('alta','media')", name="ck_replenishment_priority_allowed"),
        sa.CheckConstraint("status IN ('pending_review','approved','rejected','partially_received','received')", name="ck_replenishment_status_allowed"),
    )
    op.create_index("idx_replenishment_product_status", "replenishment_requests", ["product_id", "status"])
    op.create_index("idx_replenishment_status_created", "replenishment_requests", ["status", "created_at"])
    op.create_table(
        "replenishment_receipts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("replenishment_requests.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("inventory_movement_id", sa.Integer(), sa.ForeignKey("inventory_movements.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(80), nullable=False, unique=True),
        sa.Column("occurred_at", sa.String(40), nullable=False),
        sa.Column("received_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.CheckConstraint("quantity > 0", name="ck_replenishment_receipt_quantity_positive"),
    )
    op.create_index("idx_replenishment_receipts_request", "replenishment_receipts", ["request_id", "occurred_at"])


def downgrade():
    op.drop_index("idx_replenishment_receipts_request", table_name="replenishment_receipts")
    op.drop_table("replenishment_receipts")
    op.drop_index("idx_replenishment_status_created", table_name="replenishment_requests")
    op.drop_index("idx_replenishment_product_status", table_name="replenishment_requests")
    op.drop_table("replenishment_requests")
