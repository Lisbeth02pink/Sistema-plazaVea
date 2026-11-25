"""Real, filterable business intelligence aggregates for the executive dashboard."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

try:
    from .database import connection
    from .forecast_queries import latest_forecasts_by_product
    from .inventory_policy import inventory_status
except ImportError:
    from database import connection
    from forecast_queries import latest_forecasts_by_product
    from inventory_policy import inventory_status


def _product_row(row):
    product = dict(row)
    product["status"] = inventory_status(product["stock"], product.get("minimum_stock"), product.get("safety_stock"), product.get("maximum_stock"), bool(product.get("inventory_registered", 1)))
    product["policy_configured"] = product["status"] != "Política pendiente"
    return product


def dashboard_data(from_date: date, to_date: date, product_id: int | None = None, category: str | None = None) -> dict:
    if from_date > to_date:
        raise ValueError("La fecha inicial no puede ser posterior a la fecha final.")
    if (to_date - from_date).days > 365:
        raise ValueError("El período máximo del dashboard es de 366 días.")

    clauses, params = [], []
    if product_id:
        clauses.append("p.id=?")
        params.append(product_id)
    if category:
        clauses.append("p.category=?")
        params.append(category)
    product_where = " AND " + " AND ".join(clauses) if clauses else ""
    sale_where = "s.occurred_at>=? AND s.occurred_at<?" + product_where
    sale_params = [from_date.isoformat(), (to_date + timedelta(days=1)).isoformat(), *params]

    with connection() as db:
        simulation_active = db.execute(
            "SELECT EXISTS(SELECT 1 FROM sales WHERE reference LIKE 'SIM-PLAZA-%')"
        ).fetchone()[0]
        product_rows = [_product_row(row) for row in db.execute(f"SELECT p.* FROM products p WHERE 1=1{product_where} ORDER BY p.name", params)]
        summary = db.execute(
            "SELECT COALESCE(SUM(si.subtotal),0) AS total_sales,COALESCE(SUM(si.quantity),0) AS units,COUNT(DISTINCT s.id) AS transactions "
            "FROM sales s JOIN sale_items si ON si.sale_id=s.id JOIN products p ON p.id=si.product_id WHERE " + sale_where,
            sale_params,
        ).fetchone()
        daily_rows = db.execute(
            "SELECT substr(s.occurred_at,1,10) AS day,SUM(si.subtotal) AS sales,SUM(si.quantity) AS units "
            "FROM sales s JOIN sale_items si ON si.sale_id=s.id JOIN products p ON p.id=si.product_id WHERE " + sale_where +
            " GROUP BY substr(s.occurred_at,1,10) ORDER BY day", sale_params,
        ).fetchall()
        top_rows = db.execute(
            "SELECT p.id,p.code,p.name,p.category,SUM(si.quantity) AS units,SUM(si.subtotal) AS sales "
            "FROM sales s JOIN sale_items si ON si.sale_id=s.id JOIN products p ON p.id=si.product_id WHERE " + sale_where +
            " GROUP BY p.id,p.code,p.name,p.category ORDER BY units DESC,sales DESC,p.name LIMIT 5", sale_params,
        ).fetchall()
        active_alerts = db.execute("SELECT COUNT(*) FROM alerts a JOIN products p ON p.id=a.product_id WHERE a.status='active'" + product_where, params).fetchone()[0]
        recent_alerts = db.execute(
            "SELECT a.id,p.name AS product,p.code,a.alert_type,a.priority,a.description,a.updated_at "
            "FROM alerts a JOIN products p ON p.id=a.product_id WHERE a.status='active'" + product_where +
            " ORDER BY CASE a.priority WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END,a.updated_at DESC,a.id DESC LIMIT 5", params,
        ).fetchall()
        pending_replenishment = db.execute(
            "SELECT COUNT(*) FROM replenishment_requests r JOIN products p ON p.id=r.product_id "
            "WHERE r.status IN ('pending_review','approved','partially_received')" + product_where, params,
        ).fetchone()[0]

        today = datetime.now(timezone(timedelta(hours=-5))).date()
        forecast_end = today + timedelta(days=29)
        forecast_totals: dict[str, float] = {}
        forecast_products = set()
        selected_product_ids = {product["id"] for product in product_rows}
        daily_forecasts = latest_forecasts_by_product(db, today, forecast_end, bool(simulation_active))
        for product_id, forecasts in daily_forecasts.items():
            if product_id not in selected_product_ids:
                continue
            forecast_products.add(product_id)
            for forecast_day, estimated_units in forecasts.items():
                forecast_totals[forecast_day] = forecast_totals.get(forecast_day, 0.0) + estimated_units

        count = lambda status: sum(1 for product in product_rows if product["status"] == status)
        sales_total, units, transactions = float(summary["total_sales"]), int(summary["units"]), int(summary["transactions"])
        daily = [{"day": row["day"], "sales": float(row["sales"]), "units": int(row["units"])} for row in daily_rows]
        if daily:
            observed = {row["day"]: row for row in daily}
            daily = []
            day = from_date
            while day <= to_date:
                daily.append(observed.get(day.isoformat(), {"day": day.isoformat(), "sales": 0.0, "units": 0}))
                day += timedelta(days=1)
        return {
            "period": {"from_date": from_date.isoformat(), "to_date": to_date.isoformat()},
            "filters": {"product_id": product_id, "category": category},
            "categories": sorted({row["category"] for row in db.execute("SELECT DISTINCT category FROM products ORDER BY category")}),
            "product_options": [{"id": row["id"], "code": row["code"], "name": row["name"], "category": row["category"]} for row in db.execute("SELECT id,code,name,category FROM products ORDER BY name")],
            "summary": {"total_sales": sales_total, "units_sold": units, "transactions": transactions,
                        "average_ticket": sales_total / transactions if transactions else 0.0,
                        "products": len(product_rows), "stock_critical": count("Stock crítico"),
                        "stock_units": sum(int(product["stock"]) for product in product_rows if product.get("inventory_registered", 1)),
                        "stock_low": count("Stock bajo"), "stock_excess": count("Exceso de inventario"),
                        "stock_policy_missing": count("Política pendiente"), "active_alerts": int(active_alerts),
                        "pending_replenishments": int(pending_replenishment)},
            "sales_daily": daily,
            "top_products": [{"product_id": row["id"], "code": row["code"], "name": row["name"], "category": row["category"], "units": int(row["units"]), "sales": float(row["sales"])} for row in top_rows],
            "forecast": {"source": "Pronósticos locales validados y vigentes" if forecast_totals else None,
                         "products_count": len(forecast_products),
                         "daily": [{"day": day, "units": round(units, 3)} for day, units in sorted(forecast_totals.items())]},
            "recent_alerts": [dict(row) for row in recent_alerts],
            "data_quality": {"sales_available": transactions > 0, "forecast_available": bool(forecast_totals),
                             "simulation_active": bool(simulation_active),
                             "inventory_available": bool(product_rows)},
        }
