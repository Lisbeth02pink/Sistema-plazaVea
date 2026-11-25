"""Keep sourced product references in the store list without inventing stock."""
from alembic import op
import sqlalchemy as sa

revision = "0011_products_source_stock"
down_revision = "0010_public_web_catalog"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("products")}
    if "inventory_registered" not in columns:
        op.add_column("products", sa.Column("inventory_registered", sa.Integer(), nullable=False, server_default="1"))
        op.create_check_constraint("ck_products_inventory_registered_boolean", "products", "inventory_registered IN (0, 1)")
    if "source_item_id" not in columns:
        op.add_column("products", sa.Column("source_item_id", sa.String(40), nullable=True))
    if "source_url" not in columns:
        op.add_column("products", sa.Column("source_url", sa.Text(), nullable=True))
    if "source_captured_at" not in columns:
        op.add_column("products", sa.Column("source_captured_at", sa.String(40), nullable=True))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("products")}
    if "uq_products_source_item_id" not in indexes:
        op.create_index("uq_products_source_item_id", "products", ["source_item_id"], unique=True)


def downgrade():
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("products")}
    if "uq_products_source_item_id" in indexes:
        op.drop_index("uq_products_source_item_id", table_name="products")
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("products")}
    with op.batch_alter_table("products") as batch:
        if "source_captured_at" in columns:
            batch.drop_column("source_captured_at")
        if "source_url" in columns:
            batch.drop_column("source_url")
        if "source_item_id" in columns:
            batch.drop_column("source_item_id")
        if "inventory_registered" in columns:
            batch.drop_column("inventory_registered")
