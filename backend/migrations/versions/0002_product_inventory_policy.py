"""Add configurable inventory policy fields without inventing legacy values."""
from alembic import op
import sqlalchemy as sa

revision = "0002_product_inventory_policy"
down_revision = "0001_existing_application_tables"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("unit_of_measure", sa.String(30), nullable=True),
    sa.Column("minimum_stock", sa.Integer(), nullable=True),
    sa.Column("safety_stock", sa.Integer(), nullable=True),
    sa.Column("maximum_stock", sa.Integer(), nullable=True),
    sa.Column("supplier", sa.String(180), nullable=True),
    sa.Column("lead_time_days", sa.Integer(), nullable=True),
)
CHECKS = {
    "ck_products_minimum_stock_nonnegative": "minimum_stock IS NULL OR minimum_stock >= 0",
    "ck_products_safety_stock_nonnegative": "safety_stock IS NULL OR safety_stock >= 0",
    "ck_products_maximum_stock_nonnegative": "maximum_stock IS NULL OR maximum_stock >= 0",
    "ck_products_lead_time_positive": "lead_time_days IS NULL OR lead_time_days >= 1",
    "ck_products_safety_not_above_minimum": "safety_stock IS NULL OR minimum_stock IS NULL OR safety_stock <= minimum_stock",
    "ck_products_maximum_above_minimum": "maximum_stock IS NULL OR minimum_stock IS NULL OR maximum_stock > minimum_stock",
}
INDEXES = {
    "idx_products_category": ["category"],
    "idx_products_stock": ["stock"],
}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("products")}
    constraints = {item.get("name") for item in inspector.get_check_constraints("products")}
    with op.batch_alter_table("products") as batch:
        for column in COLUMNS:
            if column.name not in columns:
                batch.add_column(column)
        for name, expression in CHECKS.items():
            if name not in constraints:
                batch.create_check_constraint(name, expression)
    existing_indexes = {item["name"] for item in inspector.get_indexes("products")}
    for name, columns in INDEXES.items():
        if name not in existing_indexes:
            op.create_index(name, "products", columns)


def downgrade():
    for name in INDEXES:
        op.drop_index(name, table_name="products")
    with op.batch_alter_table("products") as batch:
        for name in reversed(tuple(CHECKS)):
            batch.drop_constraint(name, type_="check")
        for column in reversed(COLUMNS):
            batch.drop_column(column.name)
