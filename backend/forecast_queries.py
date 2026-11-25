"""Batched queries shared by the dashboard, replenishment and alerts."""
from __future__ import annotations

from datetime import date, timedelta


def latest_forecasts_by_product(db, start_day: date, end_day: date, simulation_active: bool) -> dict[int, dict[str, float]]:
    """Return fresh predictions for each SKU without issuing queries per product."""
    if start_day > end_day:
        return {}
    validation_status = "validated_simulation" if simulation_active else "validated_operational"
    models = db.execute(
        "SELECT m.id,m.product_id,m.source_updated_at FROM local_forecast_models m "
        "WHERE m.validated_operational=1 AND NOT EXISTS (SELECT 1 FROM local_forecast_models newer "
        "WHERE newer.product_id=m.product_id AND (newer.trained_at>m.trained_at OR "
        "(newer.trained_at=m.trained_at AND newer.id>m.id)))"
    ).fetchall()
    if not models:
        return {}
    model_by_id = {row["id"]: row for row in models}
    source_rows = db.execute(
        "SELECT product_id,MAX(source_date) AS latest_source FROM ("
        "SELECT si.product_id,MAX(s.occurred_at) AS source_date FROM sales s JOIN sale_items si ON si.sale_id=s.id GROUP BY si.product_id "
        "UNION ALL SELECT product_id,MAX(occurred_at) AS source_date FROM inventory_movements "
        "WHERE occurred_at NOT LIKE '1900-%' GROUP BY product_id) source_dates GROUP BY product_id"
    ).fetchall()
    latest_source = {row["product_id"]: row["latest_source"] for row in source_rows}
    fresh_model_ids = {
        row["id"] for row in models
        if not latest_source.get(row["product_id"]) or latest_source[row["product_id"]] <= row["source_updated_at"]
    }
    if not fresh_model_ids:
        return {}
    # Keep the SQL batch small and portable by querying the latest run for all SKUs,
    # then discard stale models in Python.
    rows = db.execute(
        "SELECT f.model_id,f.product_id,f.forecast_date,f.estimated_units FROM demand_forecasts f "
        "JOIN (SELECT model_id,MAX(generated_at) AS generated_at FROM demand_forecasts "
        "WHERE validation_status=? GROUP BY model_id) latest "
        "ON latest.model_id=f.model_id AND latest.generated_at=f.generated_at "
        "WHERE f.validation_status=? AND f.forecast_date>=? AND f.forecast_date<=? ORDER BY f.product_id,f.forecast_date",
        (validation_status, validation_status, start_day.isoformat(), end_day.isoformat()),
    ).fetchall()
    result: dict[int, dict[str, float]] = {}
    for row in rows:
        model = model_by_id.get(row["model_id"])
        if model and model["id"] in fresh_model_ids:
            result.setdefault(row["product_id"], {})[row["forecast_date"]] = float(row["estimated_units"])
    return result


def demand_during_lead(forecasts: dict[int, dict[str, float]], product_id: int, today: date, lead_days: int) -> float | None:
    if lead_days <= 0:
        return None
    daily = forecasts.get(product_id, {})
    dates = [(today + timedelta(days=offset)).isoformat() for offset in range(1, lead_days + 1)]
    if not all(day in daily for day in dates):
        return None
    return sum(daily[day] for day in dates)
