"""Complete the local PostgreSQL catalog with marked synthetic sales and stock.

The scenario is additive: products that already have sales are not rewritten, and
sales without the SIM-PLAZA prefix are never modified or used as synthetic rows.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import math
import random

from sqlalchemy import select, text

from database import get_engine, initialize_database
from models import inventory_movements, products, sale_items, sales

LIMA = timezone(timedelta(hours=-5))
SCENARIO_PREFIX = "SIM-PLAZA-"
HISTORY_DAYS = 180


def main():
    initialize_database()
    today = datetime.now(LIMA).date()
    expected_days = [today - timedelta(days=offset) for offset in range(HISTORY_DAYS, 0, -1)]
    rng = random.Random(20261124)

    with get_engine().begin() as db:
        catalog = db.execute(
            select(products.c.id, products.c.code, products.c.name, products.c.category, products.c.price,
                   products.c.unit_of_measure, products.c.inventory_registered, products.c.stock_simulated)
            .order_by(products.c.category, products.c.name)
        ).mappings().all()
        simulated_product_ids = set(db.execute(
            select(sale_items.c.product_id).select_from(sale_items.join(sales, sale_items.c.sale_id == sales.c.id))
            .where(sales.c.reference.like(f"{SCENARIO_PREFIX}%")).distinct()
        ).scalars())
        real_sales_product_ids = set(db.execute(
            select(sale_items.c.product_id).select_from(sale_items.join(sales, sale_items.c.sale_id == sales.c.id))
            .where(~sales.c.reference.like(f"{SCENARIO_PREFIX}%")).distinct()
        ).scalars())
        # Only fill products with no prior sales. This protects any later real store data.
        to_simulate = [item for item in catalog if item["id"] not in simulated_product_ids and item["id"] not in real_sales_product_ids]
        if not to_simulate:
            print(f"No hay productos pendientes: {len(simulated_product_ids)} productos ya tienen ventas sintéticas.")
            return

        sale_headers = db.execute(
            select(sales.c.id, sales.c.reference, sales.c.occurred_at, sales.c.subtotal, sales.c.total)
            .where(sales.c.reference.like(f"{SCENARIO_PREFIX}%"))
        ).mappings().all()
        headers_by_day = {}
        for row in sale_headers:
            try:
                headers_by_day[date.fromisoformat(row["reference"][len(SCENARIO_PREFIX):])] = dict(row)
            except ValueError:
                continue

        # Keep the original sample's period when present so the newly filled products
        # align with the existing synthetic history instead of creating duplicate days.
        days = sorted(headers_by_day)
        if len(days) < HISTORY_DAYS:
            days = expected_days
        else:
            days = days[-HISTORY_DAYS:]
        start_day = days[0]

        product_scenarios = []
        for index, product in enumerate(to_simulate):
            seed = int(product["id"])
            local_rng = random.Random(rng.randint(0, 2**31 - 1) ^ seed)
            base = 2 + seed % 11
            quantities = []
            for day_index, day in enumerate(days):
                weekly = 1.0 + 0.16 * math.sin(2 * math.pi * day.weekday() / 7)
                monthly = 1.0 + 0.07 * math.sin(2 * math.pi * day_index / 30 + seed % 5)
                trend = 1.0 + 0.00025 * day_index
                noise = 1.0 + local_rng.uniform(-0.10, 0.10)
                quantities.append(max(1, int(round(base * weekly * monthly * trend * noise))))
            average = sum(quantities[-28:]) / min(28, len(quantities))
            safety = max(1, math.ceil(average * 3))
            minimum = max(safety + 1, math.ceil(average * 8))
            maximum = max(minimum + 1, math.ceil(average * 35))
            band = (len(simulated_product_ids) + index) % 4
            if band == 0:
                ending_stock = max(0, safety - 1)
            elif band == 1:
                ending_stock = max(safety, minimum - 1)
            elif band == 2:
                ending_stock = minimum + math.ceil(average * 8)
            else:
                ending_stock = maximum + math.ceil(average * 10)
            opening_stock = sum(quantities) + ending_stock
            product_scenarios.append({"product": product, "quantities": quantities, "ending_stock": ending_stock,
                                      "opening_stock": opening_stock, "safety": safety, "minimum": minimum,
                                      "maximum": maximum, "lead": 2 + seed % 7})

        for item in product_scenarios:
            product = item["product"]
            db.execute(text(
                "UPDATE products SET stock=:stock,inventory_registered=1,stock_simulated=1,"
                "minimum_stock=:minimum,safety_stock=:safety,maximum_stock=:maximum,lead_time_days=:lead,"
                "unit_of_measure=COALESCE(NULLIF(unit_of_measure,''),'unidades') WHERE id=:id"
            ), {"stock": item["ending_stock"], "minimum": item["minimum"], "safety": item["safety"],
                "maximum": item["maximum"], "lead": item["lead"], "id": product["id"]})
            opening_day = start_day - timedelta(days=1)
            db.execute(inventory_movements.insert().values(
                product_id=product["id"], movement_type="Ajuste positivo", quantity=item["opening_stock"],
                stock_before=0, stock_after=item["opening_stock"],
                occurred_at=datetime.combine(opening_day, time(8), LIMA).isoformat(),
                reason="Inventario inicial sintético para simulación", reference=f"{SCENARIO_PREFIX}OPEN-{product['id']}", user_id=None,
            ))

        balances = {item["product"]["id"]: item["opening_stock"] for item in product_scenarios}
        for day_index, day in enumerate(days):
            reference = f"{SCENARIO_PREFIX}{day.isoformat()}"
            sale_time = datetime.combine(day, time(17, 30), LIMA).isoformat()
            header = headers_by_day.get(day)
            if not header:
                sale_id = db.execute(sales.insert().values(occurred_at=sale_time, reference=reference, subtotal=0,
                                                           total=0, created_by=None).returning(sales.c.id)).scalar_one()
                header = {"id": sale_id, "subtotal": 0.0, "total": 0.0}
                headers_by_day[day] = header

            item_rows, movement_rows = [], []
            added_total = 0.0
            for item in product_scenarios:
                product = item["product"]
                quantity = item["quantities"][day_index]
                price = max(0.01, float(product["price"] or 1))
                amount = round(price * quantity, 2)
                product_id = product["id"]
                before = balances[product_id]
                after = before - quantity
                if after < 0:
                    raise RuntimeError(f"Saldo negativo durante la simulación del producto {product_id}.")
                balances[product_id] = after
                added_total += amount
                item_rows.append({"sale_id": header["id"], "product_id": product_id, "quantity": quantity,
                                  "unit_price": price, "subtotal": amount})
                movement_rows.append({"product_id": product_id, "movement_type": "Salida por venta", "quantity": quantity,
                                      "stock_before": before, "stock_after": after, "occurred_at": sale_time,
                                      "reason": "Venta sintética del escenario de simulación",
                                      "reference": f"{SCENARIO_PREFIX}OUT-{day:%Y%m%d}-{product_id}", "user_id": None})
            if item_rows:
                db.execute(sale_items.insert(), item_rows)
                db.execute(inventory_movements.insert(), movement_rows)
                db.execute(text("UPDATE sales SET subtotal=subtotal+:amount,total=total+:amount WHERE id=:id"),
                           {"amount": round(added_total, 2), "id": header["id"]})

    print(f"Escenario ampliado en PostgreSQL: {len(catalog)} productos en catálogo.")
    print(f"Productos completados ahora: {len(product_scenarios)}; ya cubiertos previamente: {len(simulated_product_ids)}.")
    print(f"Productos con ventas no simuladas preservados: {len(real_sales_product_ids)}.")
    print(f"Se agregaron {len(product_scenarios) * len(days):,} líneas de venta y el mismo número de movimientos.")
    print(f"Período sintético: {days[0].isoformat()} a {days[-1].isoformat()}.")
    print("Existencias y políticas asignadas en bandas de crítico, bajo, suficiente y exceso.")


if __name__ == "__main__":
    main()
