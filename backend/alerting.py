"""Reconcile persistent operational alerts from configured policies and local data."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import threading

try:
    from .database import connection
    from .forecast_queries import demand_during_lead, latest_forecasts_by_product
    from .inventory_policy import inventory_status
except ImportError:
    from database import connection
    from forecast_queries import demand_during_lead, latest_forecasts_by_product
    from inventory_policy import inventory_status

PERU_TIMEZONE = timezone(timedelta(hours=-5))
DEMAND_INCREASE_RATIO = 1.30
_lock = threading.Lock()

ALERT_TYPES = {
    "stock_critical": "Stock crítico",
    "stockout_risk": "Riesgo de desabastecimiento",
    "low_stock": "Stock bajo",
    "excess_inventory": "Exceso de inventario",
    "demand_increase": "Incremento de demanda",
    "replenishment_needed": "Necesidad de reposición",
}


def _now() -> datetime:
    return datetime.now(PERU_TIMEZONE)


def _append_alert(items: list[dict], product, alert_type: str, priority: str, description: str, value: dict):
    items.append({
        "product_id": product["id"], "alert_type": alert_type, "priority": priority,
        "description": description, "trigger_value": value,
    })


def _sales_by_product(db, since: date) -> dict[int, dict[str, float]]:
    rows = db.execute(
        "SELECT si.product_id,substr(s.occurred_at,1,10) AS day,SUM(si.quantity) AS units "
        "FROM sale_items si JOIN sales s ON s.id=si.sale_id "
        "WHERE s.occurred_at>=? GROUP BY si.product_id,substr(s.occurred_at,1,10)",
        (since.isoformat(),),
    ).fetchall()
    result: dict[int, dict[str, float]] = {}
    for row in rows:
        result.setdefault(row["product_id"], {})[row["day"]] = float(row["units"])
    return result


def _forecast_demand_by_product(db, products, today: date, simulation_active: bool) -> dict[int, dict]:
    max_lead = max((int(product.get("lead_time_days") or 0) for product in products), default=0)
    if not max_lead:
        return {}
    daily_forecasts = latest_forecasts_by_product(db, today + timedelta(days=1), today + timedelta(days=max_lead), simulation_active)
    forecasts = {}
    for product in products:
        lead = int(product.get("lead_time_days") or 0)
        demand = demand_during_lead(daily_forecasts, product["id"], today, lead)
        if demand is not None:
            forecasts[product["id"]] = {"units": demand, "daily_average": demand / lead}
    return forecasts


def _build_conditions(products, sales: dict[int, dict[str, float]], today: date, pending_incoming: dict[int, int], forecasts: dict[int, dict] | None = None) -> list[dict]:
    desired: list[dict] = []
    forecasts = forecasts or {}
    series_start = today - timedelta(days=34)
    recent_start = today - timedelta(days=6)
    for product in products:
        stock = int(product["stock"])
        minimum = product["minimum_stock"]
        safety = product["safety_stock"]
        maximum = product["maximum_stock"]
        unit = product["unit_of_measure"] or "unidades"
        policy_state = inventory_status(stock, minimum, safety, maximum)
        if policy_state == "Stock crítico":
            _append_alert(desired, product, "stock_critical", "alta",
                          f"Stock actual de {stock} {unit}, igual o inferior al stock de seguridad ({safety} {unit}).",
                          {"stock": stock, "safety_stock": safety})
        elif policy_state == "Stock bajo":
            _append_alert(desired, product, "low_stock", "media",
                          f"Stock actual de {stock} {unit}, por debajo del mínimo ({minimum} {unit}).",
                          {"stock": stock, "minimum_stock": minimum, "safety_stock": safety})
        elif policy_state == "Exceso de inventario":
            _append_alert(desired, product, "excess_inventory", "media",
                          f"Stock actual de {stock} {unit}, por encima del máximo configurado ({maximum} {unit}).",
                          {"stock": stock, "maximum_stock": maximum})

        daily = sales.get(product["id"], {})
        first_day = min(daily) if daily else None
        enough_history = bool(first_day and first_day <= series_start.isoformat())
        if not enough_history:
            continue
        recent_units = sum(daily.get((recent_start + timedelta(days=i)).isoformat(), 0.0) for i in range(7))
        previous_units = sum(daily.get((series_start + timedelta(days=i)).isoformat(), 0.0) for i in range(28))
        recent_average = recent_units / 7
        previous_average = previous_units / 28
        model_daily = forecasts.get(product["id"], {}).get("daily_average")
        # Replenishment and demand alerts require a fresh model prediction; historical
        # averages alone must not be presented as an AI replenishment recommendation.
        if model_daily is None:
            continue
        compared_average = model_daily
        if previous_average > 0 and compared_average >= previous_average * DEMAND_INCREASE_RATIO:
            ratio = compared_average / previous_average
            driver = "el pronóstico del modelo"
            _append_alert(desired, product, "demand_increase", "alta" if ratio >= 1.75 else "media",
                          f"Según {driver} ({compared_average:.2f} {unit}/día), la demanda esperada supera en {(ratio - 1) * 100:.0f}% el promedio de los 28 días anteriores ({previous_average:.2f} {unit}/día).",
                          {"recent_7_day_units": round(recent_units, 3), "recent_daily_average": round(recent_average, 4), "model_daily_average": round(model_daily, 4) if model_daily is not None else None,
                           "previous_28_day_units": round(previous_units, 3), "previous_daily_average": round(previous_average, 4),
                           "increase_ratio": round(ratio, 4), "threshold_ratio": DEMAND_INCREASE_RATIO})

        lead_time = product["lead_time_days"]
        if minimum is None or safety is None or lead_time is None or previous_average <= 0:
            continue
        forecast = forecasts.get(product["id"])
        lead_demand = float(forecast["units"])
        demand_basis = "pronóstico del modelo"
        reorder_point = lead_demand + int(safety)
        if stock <= lead_demand:
            _append_alert(desired, product, "stockout_risk", "alta",
                          f"El {demand_basis} para los próximos {lead_time} días es {lead_demand:.1f} {unit}; el stock actual ({stock} {unit}) no lo cubriría.",
                          {"stock": stock, "daily_average_28_days": round(previous_average, 4), "lead_time_days": int(lead_time), "lead_time_demand": round(lead_demand, 3), "demand_source": demand_basis})
        inventory_position = stock + pending_incoming.get(product["id"], 0)
        if inventory_position < reorder_point:
            _append_alert(desired, product, "replenishment_needed", "alta" if stock <= lead_demand else "media",
                          f"La posición de inventario ({inventory_position} {unit}, incluidas {pending_incoming.get(product['id'], 0)} unidades pendientes) está por debajo del punto de reposición ({reorder_point:.1f} {unit}): {demand_basis} durante {lead_time} días más stock de seguridad.",
                          {"stock": stock, "pending_incoming": pending_incoming.get(product["id"], 0), "inventory_position": inventory_position, "daily_average_28_days": round(previous_average, 4), "lead_time_days": int(lead_time),
                           "lead_time_demand": round(lead_demand, 3), "safety_stock": int(safety), "reorder_point": round(reorder_point, 3), "demand_source": demand_basis})
    return desired


def refresh_alerts() -> dict:
    """Reconcile active conditions without creating repeat alerts for the same product/rule."""
    if not _lock.acquire(blocking=False):
        return {"refreshed": False, "reason": "refresh_in_progress"}
    try:
        timestamp = _now().isoformat()
        today = _now().date()
        with connection() as db:
            products = [dict(row) for row in db.execute("SELECT id,code,name,category,stock,unit_of_measure,minimum_stock,safety_stock,maximum_stock,lead_time_days FROM products WHERE inventory_registered=1 ORDER BY id")]
            sales = _sales_by_product(db, today - timedelta(days=365))
            incoming_rows = db.execute(
                "SELECT r.product_id,SUM(r.suggested_quantity-COALESCE(received.total_received,0)) AS remaining "
                "FROM replenishment_requests r LEFT JOIN (SELECT request_id,SUM(quantity) AS total_received FROM replenishment_receipts GROUP BY request_id) received ON received.request_id=r.id "
                "WHERE r.status IN ('pending_review','approved','partially_received') GROUP BY r.product_id"
            ).fetchall()
            pending_incoming = {row["product_id"]: max(0, int(row["remaining"] or 0)) for row in incoming_rows}
            simulation_active = bool(db.execute("SELECT EXISTS(SELECT 1 FROM sales WHERE reference LIKE 'SIM-PLAZA-%')").fetchone()[0])
            forecasts = _forecast_demand_by_product(db, products, today, simulation_active)
            desired = _build_conditions(products, sales, today, pending_incoming, forecasts)
            by_key = {f"product:{item['product_id']}:{item['alert_type']}": item for item in desired}
            active = db.execute("SELECT id,active_dedupe_key,priority,description,trigger_value FROM alerts WHERE status='active'").fetchall()
            retained_keys = set()
            resolved_count = 0
            updated_count = 0
            for row in active:
                key = row["active_dedupe_key"]
                condition = by_key.get(key)
                if condition is None:
                    db.execute("UPDATE alerts SET status='resolved',resolved_at=?,updated_at=?,resolution_reason=?,active_dedupe_key=NULL WHERE id=? AND status='active'", (timestamp, timestamp, "La condición que originó la alerta ya no se cumple.", row["id"]))
                    resolved_count += 1
                    continue
                retained_keys.add(key)
                trigger_value = json.dumps(condition["trigger_value"], ensure_ascii=False, sort_keys=True)
                if row["priority"] != condition["priority"] or row["description"] != condition["description"] or row["trigger_value"] != trigger_value:
                    db.execute("UPDATE alerts SET priority=?,description=?,trigger_value=?,updated_at=? WHERE id=? AND status='active'", (condition["priority"], condition["description"], trigger_value, timestamp, row["id"]))
                    updated_count += 1
            created_count = 0
            for key, condition in by_key.items():
                if key in retained_keys:
                    continue
                inserted = db.execute(
                    "INSERT INTO alerts(product_id,alert_type,priority,description,trigger_value,generated_at,updated_at,status,active_dedupe_key) "
                    "VALUES(?,?,?,?,?,?,?,'active',?) ON CONFLICT(active_dedupe_key) DO NOTHING",
                    (condition["product_id"], condition["alert_type"], condition["priority"], condition["description"],
                     json.dumps(condition["trigger_value"], ensure_ascii=False, sort_keys=True), timestamp, timestamp, key),
                )
                if inserted.rowcount > 0:
                    created_count += 1
        return {"refreshed": True, "active_count": len(by_key), "created_count": created_count,
                "updated_count": updated_count, "resolved_count": resolved_count, "checked_at": timestamp}
    finally:
        _lock.release()


def list_alerts(status: str = "active", alert_type: str | None = None, priority: str | None = None,
                search: str | None = None, page: int = 1, page_size: int = 25) -> dict:
    refresh_result = refresh_alerts()
    filters, params = [], []
    if status != "all":
        filters.append("a.status=?")
        params.append(status)
    if alert_type:
        filters.append("a.alert_type=?")
        params.append(alert_type)
    if priority:
        filters.append("a.priority=?")
        params.append(priority)
    if search:
        filters.append("(p.name LIKE ? OR p.code LIKE ? OR a.description LIKE ?)")
        term = f"%{search.strip()}%"
        params.extend((term, term, term))
    where = " WHERE " + " AND ".join(filters) if filters else ""
    offset = (page - 1) * page_size
    with connection() as db:
        total = db.execute(f"SELECT COUNT(*) FROM alerts a JOIN products p ON p.id=a.product_id{where}", params).fetchone()[0]
        rows = db.execute(
            "SELECT a.id,a.product_id,p.code,p.name AS product,p.category,a.alert_type,a.priority,a.description,a.trigger_value,a.generated_at,a.updated_at,a.status,a.resolved_at,a.resolution_reason "
            f"FROM alerts a JOIN products p ON p.id=a.product_id{where} ORDER BY CASE a.priority WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END,a.generated_at DESC,a.id DESC LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()
        active_total = db.execute("SELECT COUNT(*) FROM alerts WHERE status='active'").fetchone()[0]
        high_total = db.execute("SELECT COUNT(*) FROM alerts WHERE status='active' AND priority='alta'").fetchone()[0]
        missing_stock_policy = db.execute("SELECT COUNT(*) FROM products WHERE inventory_registered=1 AND (minimum_stock IS NULL OR safety_stock IS NULL)").fetchone()[0]
        missing_lead_time = db.execute("SELECT COUNT(*) FROM products WHERE inventory_registered=1 AND lead_time_days IS NULL").fetchone()[0]
    return {"items": [{**dict(row), "type_label": ALERT_TYPES.get(row["alert_type"], row["alert_type"]),
                        "trigger_value": json.loads(row["trigger_value"])} for row in rows],
            "total": total, "active_total": active_total, "high_priority_total": high_total,
            "products_missing_stock_policy": missing_stock_policy, "products_missing_lead_time": missing_lead_time,
            "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size,
            "types": [{"value": key, "label": value} for key, value in ALERT_TYPES.items()],
            "refreshed_at": refresh_result.get("checked_at")}
