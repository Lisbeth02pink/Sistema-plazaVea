"""Demand-based replenishment suggestions and persisted approval/receipt workflow."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math
import os
import secrets
from sqlalchemy.exc import IntegrityError

try:
    from .database import connection
    from .forecast_queries import demand_during_lead as forecast_demand_during_lead, latest_forecasts_by_product
    from .inventory_policy import inventory_status
except ImportError:
    from database import connection
    from forecast_queries import demand_during_lead as forecast_demand_during_lead, latest_forecasts_by_product
    from inventory_policy import inventory_status

PERU_TIMEZONE = timezone(timedelta(hours=-5))
MIN_HISTORY_DAYS = int(os.getenv("REPLENISHMENT_MIN_HISTORY_DAYS", "28"))
MIN_POSITIVE_SALES_DAYS = int(os.getenv("REPLENISHMENT_MIN_POSITIVE_SALES_DAYS", "5"))
OPEN_STATUSES = ("pending_review", "approved", "partially_received")
STATUS_LABELS = {"pending_review": "Pendiente de revisión", "approved": "Aprobada", "rejected": "Rechazada", "partially_received": "Recepción parcial", "received": "Recibida"}


def _now() -> datetime:
    return datetime.now(PERU_TIMEZONE)


def _pending_incoming(db) -> dict[int, int]:
    rows = db.execute(
        "SELECT r.product_id,SUM(r.suggested_quantity-COALESCE(received.total_received,0)) AS remaining "
        "FROM replenishment_requests r LEFT JOIN (SELECT request_id,SUM(quantity) AS total_received FROM replenishment_receipts GROUP BY request_id) received ON received.request_id=r.id "
        "WHERE r.status IN ('pending_review','approved','partially_received') GROUP BY r.product_id"
    ).fetchall()
    return {row["product_id"]: max(0, int(row["remaining"] or 0)) for row in rows}


def _suggestion_for_product(product: dict, today: date, pending: dict[int, int], forecasts: dict[int, dict[str, float]], simulation_active: bool) -> dict:
    product_id = product["id"]
    if not product.get("inventory_registered", 1):
        return {"product_id": product_id, "code": product["code"], "product": product["name"], "category": product["category"],
                "unit": product.get("unit_of_measure") or "unidades", "stock": None, "minimum_stock": product.get("minimum_stock"),
                "safety_stock": product.get("safety_stock"), "pending_incoming": pending.get(product_id, 0), "inventory_position": None,
                "lead_time_days": product.get("lead_time_days"), "daily_average": None, "demand_during_lead": None,
                "demand_source": None, "reorder_point": None, "suggested_quantity": 0, "priority": None,
                "status": "insufficient_data", "reason": "Registra el conteo físico inicial antes de calcular reposición."}
    stock = int(product["stock"])
    incoming = pending.get(product_id, 0)
    position = stock + incoming
    safety, lead = product["safety_stock"], product["lead_time_days"]
    result = {"product_id": product_id, "code": product["code"], "product": product["name"], "category": product["category"],
              "unit": product["unit_of_measure"] or "unidades", "stock": stock, "minimum_stock": product["minimum_stock"],
              "safety_stock": safety, "pending_incoming": incoming, "inventory_position": position,
              "lead_time_days": lead, "daily_average": None, "demand_during_lead": None, "demand_source": None,
              "reorder_point": None, "suggested_quantity": 0, "priority": None, "status": "not_needed", "reason": None}
    missing = []
    if safety is None:
        missing.append("stock de seguridad")
    if lead is None or int(lead) < 1:
        missing.append("plazo de entrega")
    if missing:
        result.update(status="insufficient_data", reason=f"Configura: {', '.join(missing)}.")
        return result
    forecast_demand = forecast_demand_during_lead(forecasts, product_id, today, int(lead))
    if forecast_demand is None:
        result.update(status="insufficient_data", reason="No hay un pronóstico de demanda validado y vigente que cubra el plazo de entrega; no se genera una cantidad ni una alerta de reposición.")
        return result
    demand_during_lead = forecast_demand
    daily_average = forecast_demand / int(lead)
    demand_source = "pronóstico local validado"
    reorder_point = math.ceil(demand_during_lead + int(safety))
    qty = max(0, reorder_point - position)
    result.update(daily_average=round(daily_average, 4), demand_during_lead=round(demand_during_lead, 3),
                  demand_source=demand_source, reorder_point=reorder_point, suggested_quantity=qty,
                  priority="alta" if stock < demand_during_lead else "media",
                  status="needed" if qty > 0 else "covered",
                  reason=("La posición de inventario está en o debajo del punto de reposición." if qty > 0 else
                          "El stock disponible más las cantidades pendientes cubren el punto de reposición."))
    return result


def suggestions() -> dict:
    today = _now().date()
    with connection() as db:
        products = [dict(row) for row in db.execute("SELECT id,code,name,category,price,stock,unit_of_measure,minimum_stock,safety_stock,maximum_stock,lead_time_days,inventory_registered FROM products WHERE inventory_registered=1 ORDER BY name")]
        pending = _pending_incoming(db)
        products_with_sales = db.execute("SELECT COUNT(DISTINCT product_id) FROM sale_items").fetchone()[0]
        simulation_active = bool(db.execute("SELECT EXISTS(SELECT 1 FROM sales WHERE reference LIKE 'SIM-PLAZA-%')").fetchone()[0])
        max_lead = max((int(product["lead_time_days"] or 0) for product in products), default=0)
        forecasts = latest_forecasts_by_product(db, today + timedelta(days=1), today + timedelta(days=max_lead), simulation_active) if max_lead else {}
        rows = [_suggestion_for_product(product, today, pending, forecasts, simulation_active) for product in products]
    needed = [item for item in rows if item["status"] == "needed"]
    forecasted_count = sum(bool(item["demand_source"] and "pronóstico" in item["demand_source"]) for item in rows)
    return {"items": rows, "needed_count": len(needed), "insufficient_count": sum(item["status"] == "insufficient_data" for item in rows),
            "pending_incoming_total": sum(pending.values()), "products_with_sales": products_with_sales,
            "products_with_validated_forecast": forecasted_count,
            "products_without_validated_forecast": len(rows) - forecasted_count,
            "products_missing_stock_policy": sum(item["minimum_stock"] is None or item["safety_stock"] is None for item in rows),
            "products_missing_lead_time": sum(item["lead_time_days"] is None for item in rows), "minimum_history_days": MIN_HISTORY_DAYS,
            "minimum_positive_sales_days": MIN_POSITIVE_SALES_DAYS, "calculated_at": _now().isoformat()}


def _request_payload(db, request_id: int) -> dict | None:
    row = db.execute(
        "SELECT r.*,p.code,p.name AS product,p.category,p.unit_of_measure,p.stock,u.display_name AS requester,d.display_name AS decision_maker "
        "FROM replenishment_requests r JOIN products p ON p.id=r.product_id LEFT JOIN users u ON u.id=r.created_by LEFT JOIN users d ON d.id=r.decided_by WHERE r.id=?",
        (request_id,),
    ).fetchone()
    if not row:
        return None
    receipts = db.execute("SELECT id,quantity,reference,occurred_at,received_by FROM replenishment_receipts WHERE request_id=? ORDER BY occurred_at,id", (request_id,)).fetchall()
    received = sum(item["quantity"] for item in receipts)
    data = dict(row)
    data.update(current_stock=data["stock"], status_label=STATUS_LABELS.get(data["status"], data["status"]), received_quantity=received,
                remaining_quantity=max(0, data["suggested_quantity"] - received), receipts=[dict(item) for item in receipts])
    return data


def list_requests(status: str = "all", page: int = 1, page_size: int = 25) -> dict:
    filters, params = [], []
    if status != "all":
        filters.append("r.status=?")
        params.append(status)
    where = " WHERE " + " AND ".join(filters) if filters else ""
    offset = (page - 1) * page_size
    with connection() as db:
        total = db.execute(f"SELECT COUNT(*) FROM replenishment_requests r{where}", params).fetchone()[0]
        ids = [row[0] for row in db.execute(f"SELECT r.id FROM replenishment_requests r{where} ORDER BY CASE r.status WHEN 'pending_review' THEN 0 WHEN 'approved' THEN 1 WHEN 'partially_received' THEN 2 ELSE 3 END,r.created_at DESC,r.id DESC LIMIT ? OFFSET ?", (*params, page_size, offset))]
        items = [_request_payload(db, request_id) for request_id in ids]
        counts = {row["status"]: row["total"] for row in db.execute("SELECT status,COUNT(*) AS total FROM replenishment_requests GROUP BY status")}
    return {"items": items, "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size,
            "counts": {key: counts.get(key, 0) for key in STATUS_LABELS}}


def get_request(request_id: int) -> dict | None:
    with connection() as db:
        return _request_payload(db, request_id)


def create_request(product_id: int, created_by: int, note: str = "") -> dict:
    timestamp = _now().isoformat()
    try:
        with connection() as db:
            product_row = db.execute("SELECT id,code,name,category,price,stock,unit_of_measure,minimum_stock,safety_stock,maximum_stock,lead_time_days,inventory_registered FROM products WHERE id=?", (product_id,)).fetchone()
            if not product_row:
                raise LookupError("Producto no encontrado.")
            pending = _pending_incoming(db)
            suggestion = _suggestion_for_product(db, dict(product_row), _now().date(), pending)
            if suggestion["status"] == "insufficient_data":
                raise ValueError(suggestion["reason"] or "No hay datos suficientes para crear esta solicitud.")
            if suggestion["status"] != "needed" or suggestion["suggested_quantity"] <= 0:
                raise ValueError("La posición de inventario ya cubre el punto de reposición.")
            reference = f"REP-{_now().year}-{secrets.token_hex(4).upper()}"
            inserted = db.execute(
                "INSERT INTO replenishment_requests(reference,product_id,suggested_quantity,stock_at_request,incoming_at_request,demand_during_lead,reorder_point,demand_source,lead_time_days,priority,status,request_note,created_by,created_at,open_product_key) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,'pending_review',?,?,?,?) RETURNING id",
                (reference, product_id, suggestion["suggested_quantity"], suggestion["stock"], suggestion["pending_incoming"],
                 suggestion["demand_during_lead"], suggestion["reorder_point"], suggestion["demand_source"], suggestion["lead_time_days"],
                 suggestion["priority"], note.strip(), created_by, timestamp, str(product_id)),
            ).fetchone()
            request_id = inserted["id"]
            db.execute("INSERT INTO audit_log(actor_id,actor_email,actor_role,action,target_type,target_id,summary,created_at) SELECT id,email,role,'replenishment.request_created','replenishment_request',?,?,? FROM users WHERE id=?",
                       (reference, f"Solicitud {reference} creada para {suggestion['product']}: {suggestion['suggested_quantity']} {suggestion['unit']}.", timestamp, created_by))
            return _request_payload(db, request_id)
    except IntegrityError as exc:
        raise ValueError("Ya existe una solicitud abierta para este producto; revisa las entradas pendientes.") from exc


def decide(request_id: int, decision: str, note: str, user: dict) -> dict:
    if decision not in {"approve", "reject"}:
        raise ValueError("La decisión debe ser approve o reject.")
    if len(note.strip()) > 500:
        raise ValueError("La observación no puede exceder 500 caracteres.")
    timestamp = _now().isoformat()
    with connection() as db:
        row = db.execute("SELECT id,reference,product_id,status,suggested_quantity FROM replenishment_requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise LookupError("Solicitud no encontrada.")
        if row["status"] != "pending_review":
            raise ValueError("Solo se pueden decidir solicitudes pendientes de revisión.")
        status = "approved" if decision == "approve" else "rejected"
        updated = db.execute("UPDATE replenishment_requests SET status=?,decided_by=?,decided_at=?,decision_note=?,open_product_key=? WHERE id=? AND status='pending_review'", (status, user["id"], timestamp, note.strip(), None if decision == "reject" else str(row["product_id"]), request_id))
        if updated.rowcount != 1:
            raise ValueError("La solicitud ya fue decidida por otro usuario.")
        action = "replenishment.approved" if decision == "approve" else "replenishment.rejected"
        summary = f"Solicitud {row['reference']} {'aprobada' if decision == 'approve' else 'rechazada'}; stock sin cambios." + (f" Nota: {note.strip()}" if note.strip() else "")
        db.execute("INSERT INTO audit_log(actor_id,actor_email,actor_role,action,target_type,target_id,summary,created_at) VALUES(?,?,?,?,?,?,?,?)", (user["id"], user["email"], user["role"], action, "replenishment_request", row["reference"], summary, timestamp))
        return _request_payload(db, request_id)


def receive(request_id: int, quantity: int, reference: str, user: dict, apply_movement) -> dict:
    timestamp = _now().isoformat()
    clean_reference = reference.strip()
    if not clean_reference:
        raise ValueError("Ingresa la referencia del comprobante de recepción.")
    with connection() as db:
        row = db.execute("SELECT r.id,r.reference,r.product_id,r.suggested_quantity,r.received_quantity,r.status,p.name FROM replenishment_requests r JOIN products p ON p.id=r.product_id WHERE r.id=?", (request_id,)).fetchone()
        if not row:
            raise LookupError("Solicitud no encontrada.")
        if row["status"] not in {"approved", "partially_received"}:
            raise ValueError("Solo se pueden recibir solicitudes aprobadas que aún tengan saldo pendiente.")
        already = int(row["received_quantity"])
        remaining = row["suggested_quantity"] - int(already)
        if quantity <= 0 or quantity > remaining:
            raise ValueError(f"La cantidad debe estar entre 1 y {remaining} unidades pendientes.")
        updated = db.execute(
            "UPDATE replenishment_requests SET received_quantity=received_quantity+?,status=CASE WHEN received_quantity+?=suggested_quantity THEN 'received' ELSE 'partially_received' END,open_product_key=CASE WHEN received_quantity+?=suggested_quantity THEN NULL ELSE open_product_key END "
            "WHERE id=? AND status IN ('approved','partially_received') AND received_quantity+?<=suggested_quantity RETURNING received_quantity,status",
            (quantity, quantity, quantity, request_id, quantity),
        ).fetchone()
        if not updated:
            raise ValueError("La cantidad pendiente cambió por otra recepción. Actualiza la solicitud y vuelve a intentar.")
        movement = apply_movement(db, row["product_id"], "Entrada por recepción", quantity, timestamp,
                                  f"Recepción de solicitud {row['reference']}", clean_reference, user["id"])
        db.execute("INSERT INTO replenishment_receipts(request_id,inventory_movement_id,quantity,reference,occurred_at,received_by) VALUES(?,?,?,?,?,?)", (request_id, movement["id"], quantity, clean_reference, timestamp, user["id"]))
        total_received = int(updated["received_quantity"])
        db.execute("INSERT INTO audit_log(actor_id,actor_email,actor_role,action,target_type,target_id,summary,created_at) VALUES(?,?,?,?,?,?,?,?)", (user["id"], user["email"], user["role"], "replenishment.received", "replenishment_request", row["reference"], f"Recepción de {quantity} unidades para {row['reference']}; acumulado {total_received}/{row['suggested_quantity']}.", timestamp))
        result = _request_payload(db, request_id)
    return result
