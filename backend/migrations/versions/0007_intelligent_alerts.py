"""Persist deduplicated operational alerts."""
from alembic import op
import sqlalchemy as sa

revision = "0007_intelligent_alerts"
down_revision = "0006_local_demand_forecasting"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("alert_type", sa.String(40), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("trigger_value", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("resolved_at", sa.String(40)),
        sa.Column("resolution_reason", sa.Text()),
        sa.Column("active_dedupe_key", sa.String(100), unique=True),
        sa.CheckConstraint("priority IN ('alta','media','baja')", name="ck_alerts_priority_allowed"),
        sa.CheckConstraint("status IN ('active','resolved')", name="ck_alerts_status_allowed"),
    )
    op.create_index("idx_alerts_status_generated", "alerts", ["status", "generated_at"])
    op.create_index("idx_alerts_product_status", "alerts", ["product_id", "status"])
    op.create_index("idx_alerts_type_status", "alerts", ["alert_type", "status"])


def downgrade():
    op.drop_index("idx_alerts_type_status", table_name="alerts")
    op.drop_index("idx_alerts_product_status", table_name="alerts")
    op.drop_index("idx_alerts_status_generated", table_name="alerts")
    op.drop_table("alerts")
