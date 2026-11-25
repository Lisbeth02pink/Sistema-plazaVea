"""Expand the inventory ledger to all movement types and establish opening balances."""
from alembic import op
import sqlalchemy as sa

revision = "0004_full_inventory_kardex"
down_revision = "0003_sales_and_kardex"
branch_labels = None
depends_on = None

MOVEMENT_TYPES = (
    "Entrada por recepción", "Salida por venta", "Ajuste positivo",
    "Ajuste negativo", "Devolución", "Merma",
)


def upgrade():
    bind = op.get_bind()
    op.execute(sa.text("UPDATE inventory_movements SET movement_type='Salida por venta' WHERE movement_type='Venta'"))
    allowed = ", ".join("'" + value.replace("'", "''") + "'" for value in MOVEMENT_TYPES)
    with op.batch_alter_table("inventory_movements") as batch:
        batch.drop_constraint("ck_inventory_movements_sale_balance", type_="check")
        batch.create_check_constraint(
            "ck_inventory_movements_balance",
            "((movement_type IN ('Salida por venta','Ajuste negativo','Merma') AND stock_before - quantity = stock_after) OR (movement_type IN ('Entrada por recepción','Ajuste positivo','Devolución') AND stock_before + quantity = stock_after))",
        )
        batch.create_check_constraint("ck_inventory_movements_type", f"movement_type IN ({allowed})")
    op.create_index("idx_inventory_movements_occurred_at", "inventory_movements", ["occurred_at"])

    rows = bind.execute(sa.text(
        "SELECT p.id,p.stock,COALESCE(SUM(CASE WHEN m.movement_type IN ('Salida por venta','Venta','Ajuste negativo','Merma') THEN -m.quantity ELSE m.quantity END),0) AS net_movement "
        "FROM products p LEFT JOIN inventory_movements m ON m.product_id=p.id GROUP BY p.id,p.stock ORDER BY p.id"
    )).mappings().all()
    for row in rows:
        opening_stock = row["stock"] - row["net_movement"]
        if opening_stock < 0:
            raise RuntimeError(f"No se puede reconstruir el saldo inicial del producto {row['id']}; el kardex excede su stock actual.")
        if opening_stock:
            bind.execute(sa.text(
                "INSERT INTO inventory_movements(product_id,movement_type,quantity,stock_before,stock_after,occurred_at,reason,reference,user_id) "
                "VALUES(:product_id,'Ajuste positivo',:quantity,0,:quantity,'1900-01-01T00:00:00-05:00','Saldo inicial al habilitar el kardex',:reference,NULL)"
            ), {"product_id": row["id"], "quantity": opening_stock, "reference": f"APERTURA-FASE4-{row['id']}"})


def downgrade():
    bind = op.get_bind()
    unsupported = bind.execute(sa.text("SELECT COUNT(*) FROM inventory_movements WHERE movement_type NOT IN ('Salida por venta','Venta')")).scalar_one()
    if unsupported:
        raise RuntimeError("No se puede revertir el kardex mientras existan movimientos distintos de ventas; se conservarán los datos.")
    op.drop_index("idx_inventory_movements_occurred_at", table_name="inventory_movements")
    op.execute(sa.text("UPDATE inventory_movements SET movement_type='Venta' WHERE movement_type='Salida por venta'"))
    with op.batch_alter_table("inventory_movements") as batch:
        batch.drop_constraint("ck_inventory_movements_type", type_="check")
        batch.drop_constraint("ck_inventory_movements_balance", type_="check")
        batch.create_check_constraint("ck_inventory_movements_sale_balance", "stock_before - quantity = stock_after")
