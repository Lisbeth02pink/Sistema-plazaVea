"""Mark products with synthetic inventory for the local demonstration."""
from alembic import op
import sqlalchemy as sa

revision = "0012_simulation_stock_flag"
down_revision = "0011_products_source_stock"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("products")}
    if "stock_simulated" not in columns:
        op.add_column("products", sa.Column("stock_simulated", sa.Integer(), nullable=False, server_default="0"))
        op.create_check_constraint("ck_products_stock_simulated_boolean", "products", "stock_simulated IN (0, 1)")


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("products")}
    if "stock_simulated" in columns:
        with op.batch_alter_table("products") as batch:
            batch.drop_column("stock_simulated")
