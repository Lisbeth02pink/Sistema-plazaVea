"""Store source-attributed Plaza Vea web catalog snapshots separately."""
from alembic import op
import sqlalchemy as sa

revision = "0010_public_web_catalog"
down_revision = "0009_replenishment_concurrency"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("web_catalog_products"):
        op.create_table(
        "web_catalog_products",
        sa.Column("item_id", sa.String(40), primary_key=True),
        sa.Column("product_id", sa.String(40), nullable=False),
        sa.Column("reference_code", sa.String(80)),
        sa.Column("ean", sa.String(40)),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("brand", sa.String(160)),
        sa.Column("category", sa.String(300), nullable=False),
        sa.Column("unit", sa.String(40)),
        sa.Column("price_pen", sa.Numeric(12, 2), nullable=False),
        sa.Column("list_price_pen", sa.Numeric(12, 2)),
        sa.Column("online_available_quantity", sa.Integer()),
        sa.Column("online_available", sa.Integer()),
        sa.Column("product_url", sa.Text(), nullable=False),
        sa.Column("source_category_url", sa.Text(), nullable=False),
        sa.Column("source", sa.String(80), nullable=False, server_default="plazavea.com.pe"),
        sa.Column("captured_at", sa.String(40), nullable=False),
        sa.CheckConstraint("price_pen >= 0", name="ck_web_catalog_price_nonnegative"),
        sa.CheckConstraint("list_price_pen IS NULL OR list_price_pen >= 0", name="ck_web_catalog_list_price_nonnegative"),
        sa.CheckConstraint("online_available_quantity IS NULL OR online_available_quantity >= 0", name="ck_web_catalog_available_nonnegative"),
        sa.CheckConstraint("online_available IS NULL OR online_available IN (0, 1)", name="ck_web_catalog_available_boolean"),
        )
    existing_indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("web_catalog_products")}
    if "idx_web_catalog_name" not in existing_indexes:
        op.create_index("idx_web_catalog_name", "web_catalog_products", ["name"])
    if "idx_web_catalog_category" not in existing_indexes:
        op.create_index("idx_web_catalog_category", "web_catalog_products", ["category"])


def downgrade():
    if sa.inspect(op.get_bind()).has_table("web_catalog_products"):
        op.drop_table("web_catalog_products")
