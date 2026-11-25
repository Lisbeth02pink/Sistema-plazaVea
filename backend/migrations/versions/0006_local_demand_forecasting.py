"""Persist training runs, per-product local models and their forecasts."""
from alembic import op
import sqlalchemy as sa

revision = "0006_local_demand_forecasting"
down_revision = "0005_kardex_opening_index"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forecast_training_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.String(40), nullable=False),
        sa.Column("finished_at", sa.String(40)),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("products_evaluated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_trained", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_validated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.CheckConstraint("products_evaluated >= 0 AND products_trained >= 0 AND products_validated >= 0", name="ck_forecast_training_counts_nonnegative"),
    )
    op.create_index("idx_forecast_training_runs_started", "forecast_training_runs", ["started_at"])
    op.create_table(
        "local_forecast_models",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("training_run_id", sa.Integer(), sa.ForeignKey("forecast_training_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("model_version", sa.String(40), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("trained_at", sa.String(40), nullable=False),
        sa.Column("source_updated_at", sa.String(40), nullable=False),
        sa.Column("history_start", sa.String(10), nullable=False),
        sa.Column("history_end", sa.String(10), nullable=False),
        sa.Column("history_days", sa.Integer(), nullable=False),
        sa.Column("positive_days", sa.Integer(), nullable=False),
        sa.Column("stockout_days", sa.Integer(), nullable=False),
        sa.Column("validation_days", sa.Integer(), nullable=False),
        sa.Column("mae", sa.Float()),
        sa.Column("rmse", sa.Float()),
        sa.Column("wape_percent", sa.Float()),
        sa.Column("bias_percent", sa.Float()),
        sa.Column("baseline_wape_percent", sa.Float()),
        sa.Column("beats_baseline", sa.Integer(), nullable=False),
        sa.Column("validated_operational", sa.Integer(), nullable=False),
        sa.Column("artifact_key", sa.String(120), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.UniqueConstraint("training_run_id", "product_id", name="uq_local_model_run_product"),
        sa.CheckConstraint("history_days >= 0 AND positive_days >= 0 AND stockout_days >= 0 AND validation_days >= 0", name="ck_local_model_counts_nonnegative"),
        sa.CheckConstraint("beats_baseline IN (0,1) AND validated_operational IN (0,1)", name="ck_local_model_flags_boolean"),
    )
    op.create_index("idx_local_forecast_models_product", "local_forecast_models", ["product_id", "trained_at"])
    op.create_index("idx_local_forecast_models_run", "local_forecast_models", ["training_run_id"])
    op.create_table(
        "demand_forecasts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("model_id", sa.Integer(), sa.ForeignKey("local_forecast_models.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("generated_at", sa.String(40), nullable=False),
        sa.Column("forecast_date", sa.String(10), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("estimated_units", sa.Numeric(12, 3), nullable=False),
        sa.Column("model_version", sa.String(40), nullable=False),
        sa.Column("validation_status", sa.String(40), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.UniqueConstraint("model_id", "generated_at", "forecast_date", name="uq_demand_forecast_generated_day"),
        sa.CheckConstraint("horizon_days IN (7,14,30)", name="ck_demand_forecast_horizon_allowed"),
        sa.CheckConstraint("estimated_units >= 0", name="ck_demand_forecast_units_nonnegative"),
    )
    op.create_index("idx_demand_forecasts_product_date", "demand_forecasts", ["product_id", "forecast_date"])
    op.create_index("idx_demand_forecasts_generated", "demand_forecasts", ["generated_at"])


def downgrade():
    op.drop_index("idx_demand_forecasts_generated", table_name="demand_forecasts")
    op.drop_index("idx_demand_forecasts_product_date", table_name="demand_forecasts")
    op.drop_table("demand_forecasts")
    op.drop_index("idx_local_forecast_models_run", table_name="local_forecast_models")
    op.drop_index("idx_local_forecast_models_product", table_name="local_forecast_models")
    op.drop_table("local_forecast_models")
    op.drop_index("idx_forecast_training_runs_started", table_name="forecast_training_runs")
    op.drop_table("forecast_training_runs")
