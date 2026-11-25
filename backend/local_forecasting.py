"""Daily product demand forecasting using only local PostgreSQL sales and stock records."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path
import re
import threading
import time

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

try:
    from .database import connection
except ImportError:
    from database import connection

ROOT = Path(__file__).parent
ARTIFACT = ROOT / "models" / "local_demand.joblib"
ARTIFACT_DIR = ROOT / "models" / "local_forecast_artifacts"
MODEL_VERSION = "local-daily-v1"
MODEL_NAME = "HistGradientBoostingRegressor"
SOURCE_LABEL = "Ventas locales registradas en PostgreSQL"
INSUFFICIENT_MESSAGE = "HISTORIAL INSUFICIENTE PARA GENERAR UNA PREDICCIÓN OPERATIVA."
WINDOW = 28
MIN_HISTORY_DAYS = int(os.getenv("LOCAL_FORECAST_MIN_HISTORY_DAYS", "120"))
MIN_TRAIN_ROWS = int(os.getenv("LOCAL_FORECAST_MIN_TRAIN_ROWS", "60"))
MIN_POSITIVE_DAYS = int(os.getenv("LOCAL_FORECAST_MIN_POSITIVE_DAYS", "20"))
MIN_VALIDATION_DAYS = int(os.getenv("LOCAL_FORECAST_MIN_VALIDATION_DAYS", "10"))
VALIDATION_DAYS = int(os.getenv("LOCAL_FORECAST_VALIDATION_DAYS", "14"))
MAX_WAPE_PERCENT = float(os.getenv("LOCAL_FORECAST_MAX_WAPE_PERCENT", "40"))
MAX_ABS_BIAS_PERCENT = float(os.getenv("LOCAL_FORECAST_MAX_ABS_BIAS_PERCENT", "25"))
_lock = threading.Lock()
_status_cache: dict | None = None
_status_cache_at = 0.0
_status_cache_lock = threading.Lock()
_STATUS_CACHE_TTL = 20.0
_model_cache: dict[str, tuple[int, dict]] = {}
_model_cache_lock = threading.Lock()


def _clear_status_cache() -> None:
    global _status_cache, _status_cache_at
    with _status_cache_lock:
        _status_cache = None
        _status_cache_at = 0.0


def _artifact_path(artifact_key: str) -> Path:
    if not re.fullmatch(r"\d+:\d+", artifact_key):
        raise ValueError("Identificador de modelo no válido.")
    run_id, product_id = artifact_key.split(":", 1)
    return ARTIFACT_DIR / f"run-{run_id}-product-{product_id}.joblib"


def _store_model_artifact(artifact_key: str, model: dict, cache_model: bool = True) -> None:
    path = _artifact_path(artifact_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    joblib.dump(model, temp_path)
    os.replace(temp_path, path)
    if cache_model:
        with _model_cache_lock:
            _model_cache[artifact_key] = (path.stat().st_mtime_ns, model)


def _load_model_artifact(artifact_key: str) -> dict | None:
    path = _artifact_path(artifact_key)
    if path.exists():
        modified_at = path.stat().st_mtime_ns
        with _model_cache_lock:
            cached = _model_cache.get(artifact_key)
            if cached and cached[0] == modified_at:
                return cached[1]
        model = joblib.load(path)
        with _model_cache_lock:
            _model_cache[artifact_key] = (modified_at, model)
        return model
    if not ARTIFACT.exists():
        return None
    legacy = joblib.load(ARTIFACT).get("models", {}).get(artifact_key)
    if legacy is not None:
        _store_model_artifact(artifact_key, legacy)
    return legacy


def migrate_legacy_artifacts() -> int:
    """Split the legacy all-model bundle into product-scoped files once."""
    if not ARTIFACT.exists():
        return 0
    models = joblib.load(ARTIFACT).get("models", {})
    migrated = 0
    for artifact_key, model in models.items():
        path = _artifact_path(artifact_key)
        if not path.exists():
            _store_model_artifact(artifact_key, model)
            migrated += 1
    return migrated

FEATURES = (
    "lag_1", "lag_7", "lag_14", "lag_28", "mean_7", "mean_14", "mean_28",
    "std_7", "weekday_sin", "weekday_cos", "month_sin", "month_cos",
    "is_weekend", "price_lag_1", "price_change_7",
)


def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=-5)))


def _simulation_active() -> bool:
    with connection() as db:
        return bool(db.execute("SELECT 1 FROM sales WHERE reference LIKE 'SIM-PLAZA-%' LIMIT 1").fetchone())


def _source_label() -> str:
    return SOURCE_LABEL


def _load_source():
    with connection() as db:
        products = db.execute("SELECT id,code,name,category,price,stock FROM products WHERE inventory_registered=1 ORDER BY id").fetchall()
        sales = db.execute(
            "SELECT si.product_id,substr(s.occurred_at,1,10) AS day,SUM(si.quantity) AS units,AVG(si.unit_price) AS price "
            "FROM sale_items si JOIN sales s ON s.id=si.sale_id GROUP BY si.product_id,substr(s.occurred_at,1,10) ORDER BY si.product_id,day"
        ).fetchall()
        returns = db.execute(
            "SELECT product_id,substr(occurred_at,1,10) AS day,SUM(quantity) AS units FROM inventory_movements "
            "WHERE movement_type='Devolución' GROUP BY product_id,substr(occurred_at,1,10) ORDER BY product_id,day"
        ).fetchall()
        movement_latest = db.execute(
            "SELECT product_id,MAX(occurred_at) AS latest_at FROM inventory_movements "
            "WHERE occurred_at NOT LIKE '1900-%' GROUP BY product_id"
        ).fetchall()
        movements = db.execute(
            "SELECT product_id,occurred_at,stock_before,stock_after,movement_type FROM inventory_movements "
            "WHERE stock_before=0 OR stock_after=0 OR movement_type='Ajuste positivo' ORDER BY product_id,occurred_at,id"
        ).fetchall()
    sales_by_product: dict[int, dict[str, dict]] = {}
    latest_source: dict[int, str] = {}
    for row in movement_latest:
        latest_source[row["product_id"]] = str(row["latest_at"])
    for row in sales:
        pid, day = row["product_id"], row["day"]
        sales_by_product.setdefault(pid, {})[day] = {"units": float(row["units"]), "price": float(row["price"])}
        latest_source[pid] = max(latest_source.get(pid, ""), str(day))
    returns_by_product: dict[int, dict[str, float]] = {}
    for row in returns:
        pid, day = row["product_id"], row["day"]
        returns_by_product.setdefault(pid, {})[day] = float(row["units"])
        latest_source[pid] = max(latest_source.get(pid, ""), str(day))
    movements_by_product: dict[int, list[dict]] = {}
    for row in movements:
        pid = row["product_id"]
        movements_by_product.setdefault(pid, []).append(dict(row))
    return products, sales_by_product, returns_by_product, movements_by_product, latest_source


def _stockout_days(days: list[date], movements: list[dict]) -> set[str]:
    if not days or not movements:
        return set()
    events_by_day: dict[str, list[dict]] = {}
    balance = None
    start = days[0].isoformat()
    for event in movements:
        occurred = str(event["occurred_at"])
        event_day = occurred[:10]
        if event_day < start:
            balance = event["stock_after"]
        else:
            events_by_day.setdefault(event_day, []).append(event)
    out = set()
    for day in days:
        key = day.isoformat()
        if balance == 0:
            out.add(key)
        for event in events_by_day.get(key, []):
            if event["stock_before"] == 0 or event["stock_after"] == 0:
                out.add(key)
            balance = event["stock_after"]
    return out


def _product_series(product: dict, sales: dict[str, dict], returns: dict[str, float], movements: list[dict], latest_source: str) -> dict:
    sale_days = sorted(sales)
    if not sale_days:
        return {"product": product, "has_sales": False, "latest_source_at": latest_source or None,
                "history_start": None, "history_end": None, "history_days": 0, "usable_days": 0,
                "positive_days": 0, "stockout_days": 0, "days_since_sale": None, "trainable": False,
                "reason": "Aún no hay ventas registradas para este producto."}
    today = _now().date()
    first = max(date.fromisoformat(sale_days[0]), today - timedelta(days=729))
    dates = [first + timedelta(days=i) for i in range((today - first).days + 1)]
    stockouts = _stockout_days(dates, movements)
    raw_values, price_values, observed = [], [], []
    latest_positive = None
    for day in dates:
        key = day.isoformat()
        daily = sales.get(key)
        units = max(0.0, (daily["units"] if daily else 0.0) - returns.get(key, 0.0))
        is_observed = key not in stockouts
        raw_values.append(units if is_observed else None)
        observed.append(is_observed)
        if daily and daily["units"] > returns.get(key, 0.0) and is_observed:
            latest_positive = day
        price_values.append(daily["price"] if daily else None)
    known_prices = [value for value in price_values if value is not None and value > 0]
    fallback_price = known_prices[0] if known_prices else float(product["price"] or 0)
    last_price = fallback_price
    prices = []
    for value in price_values:
        if value is not None and value > 0:
            last_price = value
        prices.append(float(last_price))
    values = []
    for value in raw_values:
        if value is not None:
            values.append(float(value))
        else:
            recent = [number for number in values[-7:] if number >= 0]
            if not recent:
                recent = [number for number in values[-28:] if number > 0]
            values.append(float(np.mean(recent)) if recent else 0.0)
    usable_days = sum(observed)
    positive_days = sum(1 for value, valid in zip(raw_values, observed) if valid and value and value > 0)
    stockout_count = len(stockouts)
    days_since_sale = (today - latest_positive).days if latest_positive else None
    reason = None
    if len(dates) < MIN_HISTORY_DAYS:
        reason = f"Se requieren {MIN_HISTORY_DAYS} días de historial; hay {len(dates)}."
    elif usable_days - WINDOW - VALIDATION_DAYS < MIN_TRAIN_ROWS:
        reason = f"Se requieren al menos {MIN_TRAIN_ROWS} observaciones de entrenamiento después de excluir faltantes de stock."
    elif positive_days < MIN_POSITIVE_DAYS:
        reason = f"Se requieren {MIN_POSITIVE_DAYS} días con venta neta; hay {positive_days}."
    elif days_since_sale is None or days_since_sale > 60:
        reason = "Las últimas ventas netas tienen más de 60 días; el historial no está vigente."
    return {"product": product, "has_sales": True, "latest_source_at": latest_source or None,
            "dates": dates, "raw_values": raw_values, "values": values, "prices": prices, "observed": observed,
            "history_start": dates[0].isoformat(), "history_end": dates[-1].isoformat(),
            "history_days": len(dates), "usable_days": usable_days, "positive_days": positive_days,
            "stockout_days": stockout_count, "days_since_sale": days_since_sale,
            "trainable": reason is None, "reason": reason}


def _features(values: list[float], prices: list[float], when: date) -> list[float]:
    def lag(period: int) -> float:
        return float(values[-period]) if len(values) >= period else float(np.mean(values) if values else 0.0)
    def mean(period: int) -> float:
        return float(np.mean(values[-period:])) if values else 0.0
    def std(period: int) -> float:
        return float(np.std(values[-period:])) if values else 0.0
    price_now = prices[-1] if prices else 0.0
    price_then = prices[-7] if len(prices) >= 7 else (prices[0] if prices else 0.0)
    price_change = (price_now - price_then) / price_then if price_then else 0.0
    weekday = 2 * np.pi * when.weekday() / 7
    month = 2 * np.pi * (when.month - 1) / 12
    return [lag(1), lag(7), lag(14), lag(28), mean(7), mean(14), mean(28), std(7),
            float(np.sin(weekday)), float(np.cos(weekday)), float(np.sin(month)), float(np.cos(month)),
            float(when.weekday() >= 5), float(price_now), float(price_change)]


def _training_rows(series: dict):
    X, y, indices = [], [], []
    for index in range(WINDOW, len(series["dates"])):
        if not series["observed"][index]:
            continue
        X.append(_features(series["values"][:index], series["prices"][:index], series["dates"][index]))
        y.append(float(series["raw_values"][index] or 0.0))
        indices.append(index)
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float), indices


def _baseline(history: list[float]) -> float:
    return max(0.0, float(history[-7] if len(history) >= 7 else (np.mean(history[-7:]) if history else 0.0)))


def _score(actual: list[float], predicted: list[float]) -> dict:
    actual_array, predicted_array = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    total = float(actual_array.sum())
    return {
        "mae": float(mean_absolute_error(actual_array, predicted_array)),
        "rmse": float(np.sqrt(mean_squared_error(actual_array, predicted_array))),
        "wape_percent": float(np.abs(actual_array - predicted_array).sum() / total * 100) if total > 0 else None,
        "bias_percent": float((predicted_array.sum() - total) / total * 100) if total > 0 else None,
    }


def _fit_product(series: dict):
    X, y, indices = _training_rows(series)
    cutoff = len(series["dates"]) - VALIDATION_DAYS
    train_mask = np.asarray([index < cutoff for index in indices])
    train_x, train_y = X[train_mask], y[train_mask]
    validation_indices = [index for index in range(cutoff, len(series["dates"])) if series["observed"][index]]
    if len(train_y) < MIN_TRAIN_ROWS:
        raise ValueError(f"Solo hay {len(train_y)} observaciones para entrenamiento; se requieren {MIN_TRAIN_ROWS}.")
    if len(validation_indices) < MIN_VALIDATION_DAYS:
        raise ValueError(f"Solo hay {len(validation_indices)} días válidos en la ventana temporal de validación.")
    holdout_actual = [float(series["raw_values"][index] or 0.0) for index in validation_indices]
    if sum(holdout_actual) <= 0:
        raise ValueError("La ventana de validación no contiene ventas netas; no se puede calcular WAPE.")

    model = HistGradientBoostingRegressor(max_iter=120, learning_rate=0.06, max_leaf_nodes=12,
                                          l2_regularization=2.0, random_state=42)
    model.fit(train_x, train_y)
    model_history = list(series["values"][:cutoff])
    baseline_history = list(series["values"][:cutoff])
    price_history = list(series["prices"][:cutoff])
    model_holdout, baseline_holdout = [], []
    validation_set = set(validation_indices)
    for index in range(cutoff, len(series["dates"])):
        feature_row = np.asarray([_features(model_history, price_history, series["dates"][index])], dtype=float)
        model_prediction = max(0.0, float(model.predict(feature_row)[0]))
        baseline_prediction = _baseline(baseline_history)
        if index in validation_set:
            model_holdout.append(model_prediction)
            baseline_holdout.append(baseline_prediction)
        model_history.append(model_prediction)
        baseline_history.append(baseline_prediction)
        price_history.append(price_history[-1] if price_history else 0.0)
    model_metrics = _score(holdout_actual, model_holdout)
    baseline_metrics = _score(holdout_actual, baseline_holdout)
    model_metrics["baseline_wape_percent"] = baseline_metrics["wape_percent"]
    beats_baseline = model_metrics["wape_percent"] is not None and baseline_metrics["wape_percent"] is not None and model_metrics["wape_percent"] < baseline_metrics["wape_percent"]
    validated = bool(beats_baseline and model_metrics["wape_percent"] <= MAX_WAPE_PERCENT and abs(model_metrics["bias_percent"] or 0) <= MAX_ABS_BIAS_PERCENT)
    final_model = HistGradientBoostingRegressor(max_iter=120, learning_rate=0.06, max_leaf_nodes=12,
                                                l2_regularization=2.0, random_state=42)
    final_model.fit(X, y)
    history_chart = [{"date": day.isoformat(), "units": round(float(value), 3) if valid else None}
                     for day, value, valid in zip(series["dates"][-90:], series["raw_values"][-90:], series["observed"][-90:])]
    return {"estimator": final_model, "history": list(series["values"]), "prices": list(series["prices"]),
            "history_chart": history_chart, "last_date": series["dates"][-1].isoformat(),
            "metrics": model_metrics, "beats_baseline": beats_baseline, "validated_operational": validated,
            "training_rows": len(y), "validation_days": len(holdout_actual)}


def _forecast_points(saved: dict, horizon: int) -> list[dict]:
    values = list(saved["history"])
    prices = list(saved["prices"])
    first_day = date.fromisoformat(saved["last_date"]) + timedelta(days=1)
    points = []
    for offset in range(horizon):
        target_day = first_day + timedelta(days=offset)
        feature = np.asarray([_features(values, prices, target_day)], dtype=float)
        units = round(max(0.0, float(saved["estimator"].predict(feature)[0])), 3)
        points.append({"date": target_day.isoformat(), "units": units})
        values.append(units)
        prices.append(prices[-1] if prices else float(saved["product"].get("price") or 0))
    return points


def _persist_run_forecasts(run_id: int, trained_artifacts: dict, created_by: int | None, horizon: int = 7) -> int:
    simulation_active = _simulation_active()
    validation_status = "validated_simulation" if simulation_active else "validated_operational"
    generated_at = _now().isoformat()
    rows_to_insert = []
    product_count = 0
    with connection() as db:
        models = db.execute(
            "SELECT id,product_id,artifact_key,model_version,source_updated_at FROM local_forecast_models "
            "WHERE training_run_id=? AND validated_operational=1 ORDER BY product_id", (run_id,),
        ).fetchall()
        for model in models:
            saved = trained_artifacts.get(model["artifact_key"])
            if not saved:
                continue
            latest_source = db.execute(
                "SELECT MAX(value) FROM (SELECT MAX(s.occurred_at) AS value FROM sales s JOIN sale_items si ON si.sale_id=s.id WHERE si.product_id=? "
                "UNION ALL SELECT MAX(occurred_at) AS value FROM inventory_movements WHERE product_id=? AND occurred_at NOT LIKE '1900-%') AS source_dates",
                (model["product_id"], model["product_id"]),
            ).fetchone()[0]
            if latest_source and latest_source > model["source_updated_at"]:
                continue
            for point in _forecast_points(saved, horizon):
                rows_to_insert.append({"model_id": model["id"], "product_id": model["product_id"],
                                       "generated_at": generated_at, "forecast_date": point["date"],
                                       "horizon_days": horizon, "estimated_units": point["units"],
                                       "model_version": model["model_version"], "validation_status": validation_status,
                                       "created_by": created_by})
            product_count += 1
        if rows_to_insert:
            db.executemany(
                "INSERT INTO demand_forecasts(model_id,product_id,generated_at,forecast_date,horizon_days,estimated_units,model_version,validation_status,created_by) "
                "VALUES(:model_id,:product_id,:generated_at,:forecast_date,:horizon_days,:estimated_units,:model_version,:validation_status,:created_by)",
                rows_to_insert,
            )
    return product_count


def _status_from_data(series_list: list[dict], latest_run: dict | None, models: list[dict], training: bool = False) -> dict:
    simulation_active = _simulation_active()
    source_label = SOURCE_LABEL
    model_by_product = {}
    for model in models:
        if model["product_id"] not in model_by_product:
            model_by_product[model["product_id"]] = model
    statuses = []
    validated_count = 0
    for series in series_list:
        product = series["product"]
        model = model_by_product.get(product["id"])
        source_updated = series.get("latest_source_at") or ""
        stale = bool(model and source_updated > (model["source_updated_at"] or ""))
        if model and model["validated_operational"] and not stale:
            model_status = "validated_operational"
            validated_count += 1
        elif model:
            model_status = "stale" if stale else "trained_local"
        elif series["trainable"]:
            model_status = "ready_to_train"
        elif series["has_sales"]:
            model_status = "insufficient_history"
        else:
            model_status = "no_local_sales"
        statuses.append({
            "id": product["id"], "code": product["code"], "name": product["name"], "category": product["category"],
            "history_days": series["history_days"], "usable_days": series["usable_days"],
            "positive_days": series["positive_days"], "stockout_days": series["stockout_days"],
            "days_since_sale": series["days_since_sale"], "trainable": series["trainable"],
            "reason": series["reason"], "status": model_status,
            "model_id": model["id"] if model else None,
            "model_version": model["model_version"] if model else None,
            "model_name": model["model_name"] if model else None,
            "trained_at": model["trained_at"] if model else None,
            "history_end": model["history_end"] if model else None,
            "validated_operational": model_status == "validated_operational",
            "metrics": {key: model[key] for key in ("mae", "rmse", "wape_percent", "bias_percent", "baseline_wape_percent", "beats_baseline") if model and key in model} if model else None,
        })
    product_with_sales = sum(item["has_sales"] for item in series_list)
    trainable_count = sum(item["trainable"] for item in series_list)
    if validated_count:
        state = "simulation_ready" if simulation_active else "validated_operational"
        message = ("Hay modelos que superaron la validación temporal, pero aún no están habilitados para uso operativo."
                   if simulation_active else "Hay modelos locales que superaron la validación temporal y la comparación con la referencia estacional.")
    elif any(item.get("status") == "trained_local" for item in statuses):
        state = "trained_not_validated"
        message = "Hay modelos entrenados con ventas locales, pero ninguno supera los criterios de validación operativa."
    elif trainable_count:
        state = "ready_to_train"
        message = "Hay historial local suficiente para iniciar entrenamiento y validación."
    else:
        state = "insufficient_history"
        message = INSUFFICIENT_MESSAGE
    return {
        "state": state, "operational_ready": validated_count > 0 and not simulation_active, "training": training,
        "message": message, "source_label": source_label,
        "data_notice": "Los días sin comprobantes se consideran cero ventas. Los días con stock agotado se excluyen de entrenamiento y validación. Las devoluciones registradas se descuentan de las unidades del día; registre las ventas completas para evitar sesgo.",
        "quality_gate": {"minimum_history_days": MIN_HISTORY_DAYS, "minimum_training_rows": MIN_TRAIN_ROWS,
                         "minimum_positive_days": MIN_POSITIVE_DAYS, "validation_days": VALIDATION_DAYS,
                         "minimum_valid_days": MIN_VALIDATION_DAYS, "maximum_wape_percent": MAX_WAPE_PERCENT,
                         "maximum_absolute_bias_percent": MAX_ABS_BIAS_PERCENT,
                         "validation_rule": "Debe superar al baseline estacional semanal, tener WAPE dentro del máximo y sesgo absoluto dentro del máximo."},
        "summary": {"products": len(series_list), "products_with_sales": product_with_sales,
                    "products_trainable": trainable_count, "products_validated": validated_count,
                    "sales_records_source": "PostgreSQL"},
        "latest_run": latest_run, "products": statuses,
    }


def status(force_refresh: bool = False) -> dict:
    global _status_cache, _status_cache_at
    now = time.monotonic()
    if not force_refresh:
        with _status_cache_lock:
            if _status_cache is not None and now - _status_cache_at < _STATUS_CACHE_TTL:
                return _status_cache
    products, sales, returns, movements, latest_source = _load_source()
    series_list = [_product_series(dict(product), sales.get(product["id"], {}), returns.get(product["id"], {}), movements.get(product["id"], []), latest_source.get(product["id"], "")) for product in products]
    with connection() as db:
        latest = db.execute("SELECT id,started_at,finished_at,status,source,message,products_evaluated,products_trained,products_validated FROM forecast_training_runs ORDER BY id DESC LIMIT 1").fetchone()
        models = db.execute("SELECT m.*,p.code,p.name FROM local_forecast_models m JOIN products p ON p.id=m.product_id ORDER BY m.product_id,m.trained_at DESC,m.id DESC").fetchall()
    latest_run = dict(latest) if latest else None
    result = _status_from_data(series_list, latest_run, [dict(row) for row in models])
    with _status_cache_lock:
        _status_cache = result
        _status_cache_at = time.monotonic()
    return result


def train(requested_by: int | None = None) -> dict:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("Ya hay un entrenamiento local en curso.")
    _clear_status_cache()
    started_at = _now().isoformat()
    run_id = None
    try:
        products, sales, returns, movements, latest_source = _load_source()
        series_list = [_product_series(dict(product), sales.get(product["id"], {}), returns.get(product["id"], {}), movements.get(product["id"], []), latest_source.get(product["id"], "")) for product in products]
        trainable = [item for item in series_list if item["trainable"]]
        with connection() as db:
            inserted = db.execute("INSERT INTO forecast_training_runs(started_at,status,source,message,products_evaluated,products_trained,products_validated,requested_by) VALUES(?,?,?,?,?,?,?,?) RETURNING id", (started_at, "training", _source_label(), "", len(series_list), 0, 0, requested_by)).fetchone()
            run_id = inserted["id"]
        if not trainable:
            with connection() as db:
                db.execute("UPDATE forecast_training_runs SET finished_at=?,status='insufficient_history',message=? WHERE id=?", (_now().isoformat(), INSUFFICIENT_MESSAGE, run_id))
            return status(force_refresh=True)

        trained, validated, artifact_models, validated_products = [], 0, {}, []
        for series in trainable:
            try:
                result = _fit_product(series)
            except Exception as exc:
                series["reason"] = str(exc)
                continue
            product_id = series["product"]["id"]
            artifact_key = f"{run_id}:{product_id}"
            model_status = "validated_operational" if result["validated_operational"] else "trained_local"
            if result["validated_operational"]:
                validated += 1
                validated_products.append(product_id)
            trained_at = _now().isoformat()
            model_version = f"{MODEL_VERSION}-r{run_id}"
            result.update({"product": {key: series["product"][key] for key in ("id", "code", "name", "category", "price")},
                           "artifact_key": artifact_key, "model_version": model_version, "trained_at": trained_at})
            artifact_models[artifact_key] = result
            metrics = result["metrics"]
            with connection() as db:
                db.execute(
                    "INSERT INTO local_forecast_models(training_run_id,product_id,model_version,model_name,trained_at,source_updated_at,history_start,history_end,history_days,positive_days,stockout_days,validation_days,mae,rmse,wape_percent,bias_percent,baseline_wape_percent,beats_baseline,validated_operational,artifact_key,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, product_id, model_version, MODEL_NAME, trained_at, series["latest_source_at"] or started_at,
                     series["history_start"], series["history_end"], series["usable_days"], series["positive_days"],
                     series["stockout_days"], result["validation_days"], metrics["mae"], metrics["rmse"], metrics["wape_percent"],
                     metrics["bias_percent"], metrics["baseline_wape_percent"], int(result["beats_baseline"]),
                     int(result["validated_operational"]), artifact_key, model_status),
                )
            trained.append(series["product"]["id"])
        if not trained:
            message = "No se pudo entrenar ningún producto con las ventanas temporales disponibles."
            with connection() as db:
                db.execute("UPDATE forecast_training_runs SET finished_at=?,status='failed',message=? WHERE id=?", (_now().isoformat(), message, run_id))
            raise RuntimeError(message)
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        for artifact_key, saved_model in artifact_models.items():
            _store_model_artifact(artifact_key, saved_model, cache_model=False)
        simulation_active = _simulation_active()
        run_status = ("trained_simulation" if validated and simulation_active else
                      "validated_operational" if validated else "trained_not_validated")
        message = (f"Se entrenaron {len(trained)} productos; {validated} superaron la validación, pero no están habilitados para uso operativo."
                   if validated and simulation_active else
                   f"Se entrenaron {len(trained)} productos locales; {validated} superaron la validación operativa."
                   if validated else "Se entrenaron modelos locales, pero no superaron todos los criterios de validación operativa.")
        with connection() as db:
            db.execute("UPDATE forecast_training_runs SET finished_at=?,status=?,message=?,products_trained=?,products_validated=? WHERE id=?", (_now().isoformat(), run_status, message, len(trained), validated, run_id))
        _persist_run_forecasts(run_id, artifact_models, requested_by, 14)
        return status(force_refresh=True)
    except Exception as exc:
        if run_id is not None:
            with connection() as db:
                db.execute("UPDATE forecast_training_runs SET finished_at=?,status='failed',message=? WHERE id=?", (_now().isoformat(), str(exc), run_id))
        raise
    finally:
        _lock.release()


def generate_run_forecasts(run_id: int, created_by: int | None = None, horizon: int = 7) -> int:
    """Generate persisted predictions for a completed run with one artifact load."""
    if horizon not in (7, 14, 30):
        raise ValueError("El horizonte debe ser de 7, 14 o 30 días.")
    with connection() as db:
        artifact_keys = [row["artifact_key"] for row in db.execute(
            "SELECT artifact_key FROM local_forecast_models WHERE training_run_id=? ORDER BY product_id", (run_id,)
        ).fetchall()]
    saved_models = {key: model for key in artifact_keys if (model := _load_model_artifact(key)) is not None}
    return _persist_run_forecasts(run_id, saved_models, created_by, horizon)


def predict(product_id: int, horizon: int, created_by: int | None = None) -> dict:
    if horizon not in (7, 14, 30):
        raise ValueError("El horizonte debe ser de 7, 14 o 30 días.")
    try:
        from .replenishment import _pending_incoming, _suggestion_for_product
    except ImportError:
        from replenishment import _pending_incoming, _suggestion_for_product
    with connection() as db:
        model = db.execute("SELECT m.*,p.code,p.name,p.category,p.price,p.stock,p.minimum_stock,p.safety_stock,p.lead_time_days,p.unit_of_measure,p.inventory_registered FROM local_forecast_models m JOIN products p ON p.id=m.product_id WHERE m.product_id=? ORDER BY m.trained_at DESC,m.id DESC LIMIT 1", (product_id,)).fetchone()
        if not model or not model["validated_operational"]:
            raise RuntimeError("Este producto no tiene un modelo local validado para uso operativo.")
        latest_source = db.execute("SELECT MAX(value) FROM (SELECT MAX(s.occurred_at) AS value FROM sales s JOIN sale_items si ON si.sale_id=s.id WHERE si.product_id=? UNION ALL SELECT MAX(occurred_at) AS value FROM inventory_movements WHERE product_id=? AND occurred_at NOT LIKE '1900-%') AS latest_sources", (product_id, product_id)).fetchone()[0]
        pending = _pending_incoming(db)
        simulation_active = bool(db.execute("SELECT EXISTS(SELECT 1 FROM sales WHERE reference LIKE 'SIM-PLAZA-%')").fetchone()[0])
    if latest_source and latest_source > model["source_updated_at"]:
        raise RuntimeError("Hay ventas o movimientos posteriores al entrenamiento. Reentrena el modelo antes de generar un pronóstico.")
    if not ARTIFACT.exists():
        raise RuntimeError("No se encontró el artefacto del modelo local; vuelve a entrenar.")
    saved = _load_model_artifact(model["artifact_key"])
    if not saved:
        raise RuntimeError("No se encontró el modelo local persistido; vuelve a entrenar.")
    values = list(saved["history"])
    prices = list(saved["prices"])
    first_day = date.fromisoformat(saved["last_date"]) + timedelta(days=1)
    points = []
    for offset in range(horizon):
        target_day = first_day + timedelta(days=offset)
        feature = np.asarray([_features(values, prices, target_day)], dtype=float)
        units = round(max(0.0, float(saved["estimator"].predict(feature)[0])), 3)
        points.append({"date": target_day.isoformat(), "units": units})
        values.append(units)
        prices.append(prices[-1] if prices else float(saved["product"].get("price") or 0))
    generated_at = _now().isoformat()
    forecast_status = "validated_simulation" if simulation_active else "validated_operational"
    forecast_by_product = {product_id: {point["date"]: point["units"] for point in points}}
    replenishment = _suggestion_for_product(dict(model), _now().date(), pending, forecast_by_product, simulation_active)
    with connection() as db:
        db.executemany(
            "INSERT INTO demand_forecasts(model_id,product_id,generated_at,forecast_date,horizon_days,estimated_units,model_version,validation_status,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
            [(model["id"], product_id, generated_at, point["date"], horizon, point["units"], model["model_version"], forecast_status, created_by) for point in points],
        )
    return {
        "product": {key: model[key] for key in ("id", "code", "name", "category")},
        "history": saved["history_chart"], "forecast": points, "horizon": horizon,
        "estimated_units": round(sum(point["units"] for point in points), 2),
        "model": {"name": model["model_name"], "version": model["model_version"],
                  "mae": model["mae"], "rmse": model["rmse"], "wape_percent": model["wape_percent"],
                  "bias_percent": model["bias_percent"], "baseline_wape_percent": model["baseline_wape_percent"],
                  "beats_baseline": bool(model["beats_baseline"]), "validated_operational": not simulation_active},
        "replenishment": replenishment,
        "generated_at": generated_at, "source_label": _source_label(), "validation_status": forecast_status,
    }
