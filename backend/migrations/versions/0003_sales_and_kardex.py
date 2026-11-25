"""Persist sales, their line items, and stock-out movements."""
from alembic import op
import sqlalchemy as sa

revision = "0003_sales_and_kardex"
down_revision = "0002_product_inventory_policy"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sales",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.String(40), nullable=False),
        sa.Column("reference", sa.String(80), nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.UniqueConstraint("reference", name="uq_sales_reference"),
        sa.CheckConstraint("subtotal >= 0", name="ck_sales_subtotal_nonnegative"),
        sa.CheckConstraint("total >= 0", name="ck_sales_total_nonnegative"),
    )
    op.create_index("idx_sales_occurred_at", "sales", ["occurred_at"])
    op.create_table(
        "sale_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_sale_items_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_sale_items_price_nonnegative"),
        sa.CheckConstraint("subtotal >= 0", name="ck_sale_items_subtotal_nonnegative"),
    )
    op.create_index("idx_sale_items_sale_id", "sale_items", ["sale_id"])
    op.create_index("idx_sale_items_product_id", "sale_items", ["product_id"])
    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("movement_type", sa.String(30), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("stock_before", sa.Integer(), nullable=False),
        sa.Column("stock_after", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.String(40), nullable=False),
        sa.Column("reason", sa.String(240), nullable=False),
        sa.Column("reference", sa.String(80), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.CheckConstraint("quantity > 0", name="ck_inventory_movements_quantity_positive"),
        sa.CheckConstraint("stock_before >= 0", name="ck_inventory_movements_stock_before_nonnegative"),
        sa.CheckConstraint("stock_after >= 0", name="ck_inventory_movements_stock_after_nonnegative"),
        sa.CheckConstraint("stock_before - quantity = stock_after", name="ck_inventory_movements_sale_balance"),
    )
    op.create_index("idx_inventory_movements_product_time", "inventory_movements", ["product_id", "occurred_at"])
    op.create_index("idx_inventory_movements_reference", "inventory_movements", ["reference"])


def downgrade():
    op.drop_index("idx_inventory_movements_reference", table_name="inventory_movements")
    op.drop_index("idx_inventory_movements_product_time", table_name="inventory_movements")
    op.drop_table("inventory_movements")
    op.drop_index("idx_sale_items_product_id", table_name="sale_items")
    op.drop_index("idx_sale_items_sale_id", table_name="sale_items")
    op.drop_table("sale_items")
    op.drop_index("idx_sales_occurred_at", table_name="sales")
    op.drop_table("sales")
