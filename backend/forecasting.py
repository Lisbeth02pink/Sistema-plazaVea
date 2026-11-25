"""Grocery demand model trained from the public Polish RSM scanner dataset."""
from __future__ import annotations

from datetime import date, timedelta
import os
from pathlib import Path
import re
import threading
import unicodedata

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingClassifier, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import f1_score, mean_absolute_error, mean_squared_error, precision_score, recall_score
from sklearn.model_selection import TimeSeriesSplit

ROOT = Path(__file__).parent
DATA = ROOT / "data" / "scanner-groceries.csv"
ARTIFACT = ROOT / "models" / "retail_demand.joblib"
PLN_PEN = float(os.getenv("RETAIL_PLN_PEN_RATE", "0.95"))
SOURCE_URL = "https://zenodo.org/records/18342253"
FEATURES = ["lag_1", "lag_2", "lag_3", "lag_6", "lag_12", "mean_3", "mean_6", "month_sin", "month_cos"]
CATEGORY_NAMES = {"RICE": "Arroz", "SUGAR": "Azúcar", "LOW-FAT MILK": "Leche baja en grasa", "FULL-FAT MILK": "Leche entera"}
WORD_TRANSLATIONS = {
    "ryz": "arroz", "bialy": "blanco", "biala": "blanca", "pelnoziarnisty": "integral", "dlugi": "largo",
    "cukier": "azúcar", "mleko": "leche", "swieze": "fresca", "wiejskie": "de campo", "polskie": "polaca", "brazowy": "moreno",
    "mikrofiltrowane": "microfiltrada", "mikrofiltrow": "microfiltrada", "brunatny": "morena", "trzcinowy": "de caña",
    "dlugoziarnisty": "grano largo", "parboiled": "precocido", "parboied": "precocido", "dry": "seco",
    "laktozy": "lactosa", "bez": "sin", "junior": "junior", "uht": "UHT", "pet": "PET",
    "laciate": "Łaciate", "mlekovita": "Mlekovita", "piatnica": "Piątnica", "diamant": "Diamant", "drydemer": "Demerara",
    "sonko": "Sonko", "kupiec": "Kupiec", "cenos": "Cenos", "halina": "Halina", "rolmlecz": "Rolmlecz",
    "zambrowskie": "Zambrowskie", "wypasione": "Wypasione", "twoj": "Twoj", "kubek": "Kubek",
    "pp": "marca propia", "mp": "marca propia",
}
_lock = threading.Lock()
_status = {"training": False, "error": None}


def _ascii_word(value: str) -> str:
    value = value.replace("ł", "l").replace("Ł", "L")
    return "".join(char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char))


def _display_name(description: str) -> str:
    words = re.findall(r"\d+(?:[.,]\d+)?%?|[\w]+|[.,%]", description.lower(), flags=re.UNICODE)
    translated = [WORD_TRANSLATIONS.get(_ascii_word(word), word) for word in words]
    text = " ".join(translated)
    text = re.sub(r"\s+([.,%])", r"\1", text)
    text = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*(kg|g|l|ml)\b", r"\1 \2", text, flags=re.I)
    text = re.sub(r"\b(\d+)\s*x\s*(\d+)\s*g\b", r"\1 × \2 g", text, flags=re.I)
    return text[:1].upper() + text[1:]


def _read_grocery_series() -> tuple[pd.DataFrame, dict[str, dict]]:
    if not DATA.exists():
        raise RuntimeError(f"No se encontró el conjunto de productos en {DATA}.")
    table = pd.read_csv(DATA, sep=";", decimal=",", encoding="utf-8")
    table["time"] = pd.to_datetime(table["time"], errors="coerce")
    table["quantities"] = pd.to_numeric(table["quantities"], errors="coerce")
    table["prices"] = pd.to_numeric(table["prices"], errors="coerce")
    table = table.dropna(subset=["time", "description", "quantities", "prices"])
    table = table[(table.quantities > 0) & (table.prices > 0)].copy()
    table["period"] = table.time.dt.to_period("M").dt.to_timestamp()
    totals = table.groupby(["category", "description"], as_index=False).agg(units=("quantities", "sum"), price_pln=("prices", "mean"))
    selected = []
    for category in ("RICE", "SUGAR", "LOW-FAT MILK", "FULL-FAT MILK"):
        selected.extend(totals[totals.category == category].nlargest(10, "units").description.tolist())
    table = table[table.description.isin(selected)]
    grouped = table.groupby(["description", "period"], as_index=False).quantities.sum()
    metadata = {}
    rows = []
    end = table.period.max()
    for description in selected:
        records = table[table.description == description]
        if records.empty:
            continue
        category_code = str(records.category.iloc[0])
        item_id = str(records.retailer_code.mode().iloc[0])
        series_id = f"{category_code}:{item_id}:{description}"
        first = records.period.min()
        index = pd.date_range(first, end, freq="MS")
        monthly = grouped[grouped.description == description].set_index("period").quantities
        for month in index:
            rows.append({"series_id": series_id, "date": month, "sales": float(monthly.get(month, 0))})
        metadata[series_id] = {
            "id": series_id, "item_id": item_id, "name": _display_name(description),
            "category": CATEGORY_NAMES[category_code], "store": "Supermercado de referencia",
            "last_date": end.date().isoformat(), "price_pen": round(float(records.prices.mean()) * PLN_PEN, 2),
        }
    return pd.DataFrame(rows), metadata


def _features(history: list[float], when: pd.Timestamp) -> dict:
    def lag(n: int) -> float:
        return float(history[-n]) if len(history) >= n else 0.0
    month_angle = 2 * np.pi * (when.month - 1) / 12
    return {"lag_1": lag(1), "lag_2": lag(2), "lag_3": lag(3), "lag_6": lag(6), "lag_12": lag(12),
            "mean_3": float(np.mean(history[-3:])) if history else 0.0,
            "mean_6": float(np.mean(history[-6:])) if history else 0.0,
            "month_sin": float(np.sin(month_angle)), "month_cos": float(np.cos(month_angle))}


def _feature_rows(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for series_id, group in frame.groupby("series_id", sort=False):
        group = group.sort_values("date")
        sales = group.sales.to_list()
        dates = group.date.to_list()
        for i in range(3, len(group)):
            values = _features(sales[:i], pd.Timestamp(dates[i]))
            values.update(series_id=series_id, target=float(sales[i]), date=dates[i])
            rows.append(values)
    return pd.DataFrame(rows)


def _regressor():
    return HistGradientBoostingRegressor(max_iter=120, learning_rate=0.08, max_leaf_nodes=20,
                                         l2_regularization=2.0, random_state=42)


def _candidate_models():
    return {
        "HistGradientBoosting": _regressor,
        "RandomForest": lambda: RandomForestRegressor(n_estimators=120, min_samples_leaf=4, max_features=0.9,
                                                       n_jobs=-1, random_state=42),
        "ExtraTrees": lambda: ExtraTreesRegressor(n_estimators=120, min_samples_leaf=4, max_features=0.9,
                                                   n_jobs=-1, random_state=42),
    }


def train() -> dict:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("Ya hay un entrenamiento en curso.")
    _status.update(training=True, error=None)
    try:
        frame, metadata = _read_grocery_series()
        last_day = frame.date.max()
        features = _feature_rows(frame)
        monthly_dates = np.sort(features.date.unique())
        if len(monthly_dates) < 10:
            raise RuntimeError("Se requieren al menos 10 meses de datos para entrenar y validar el modelo.")
        holdout_months = monthly_dates[-2:]
        cutoff = holdout_months[0]
        # Split on unique dates, never on individual rows, so a fold cannot
        # train on one product from a date while validating another product
        # from that same date.
        # Keep the final two monthly periods completely outside model selection.
        dates = monthly_dates[:-2]
        splitter = TimeSeriesSplit(n_splits=3, test_size=1)
        candidate_factories = _candidate_models()
        cv_actual = {name: [] for name in candidate_factories}
        cv_predictions = {name: [] for name in candidate_factories}
        cv_baseline_actual, cv_baseline_predictions = [], []
        for train_idx, test_idx in splitter.split(dates):
            train_dates, test_dates = dates[train_idx], dates[test_idx]
            fold_train = features[features.date.isin(train_dates)]
            fold_test = features[features.date.isin(test_dates)]
            if fold_train.empty or fold_test.empty:
                continue
            for name, factory in candidate_factories.items():
                candidate = factory()
                candidate.fit(fold_train[FEATURES], fold_train.target)
                forecast = np.maximum(0.0, candidate.predict(fold_test[FEATURES]))
                cv_actual[name].extend(fold_test.target.astype(float).tolist())
                cv_predictions[name].extend(forecast.astype(float).tolist())
            cv_baseline_actual.extend(fold_test.target.astype(float).tolist())
            cv_baseline_predictions.extend(np.maximum(0.0, fold_test.lag_1.astype(float)).tolist())
        cv_wape = {
            name: float(np.abs(np.asarray(cv_actual[name]) - np.asarray(cv_predictions[name])).sum()) /
                  max(float(np.asarray(cv_actual[name]).sum()), 1.0) * 100
            for name in candidate_factories if cv_actual[name]
        }
        selected_model = min(cv_wape, key=cv_wape.get) if cv_wape else "HistGradientBoosting"
        baseline_actual_array = np.asarray(cv_baseline_actual, dtype=float)
        baseline_prediction_array = np.asarray(cv_baseline_predictions, dtype=float)
        baseline_wape = (float(np.abs(baseline_actual_array - baseline_prediction_array).sum()) /
                         max(float(baseline_actual_array.sum()), 1.0) * 100) if len(baseline_actual_array) else None
        raw_model_wape = cv_wape.get(selected_model, float("inf"))
        blend_model_weight = 1.0
        blended_cv_wape = raw_model_wape
        model_cv_predictions = np.asarray(cv_predictions.get(selected_model, []), dtype=float)
        if len(model_cv_predictions) and len(model_cv_predictions) == len(baseline_prediction_array):
            options = []
            for weight in np.linspace(0.0, 1.0, 21):
                combined = weight * model_cv_predictions + (1.0 - weight) * baseline_prediction_array
                score = float(np.abs(baseline_actual_array - combined).sum()) / max(float(baseline_actual_array.sum()), 1.0) * 100
                options.append((score, float(weight)))
            blended_cv_wape, blend_model_weight = min(options)
        train_rows = features[features.date < cutoff]
        model = candidate_factories[selected_model]()
        model.fit(train_rows[FEATURES], train_rows.target)

        thresholds = {}
        for series_id, group in train_rows.groupby("series_id", sort=False):
            nonzero = group.loc[group.target > 0, "target"].to_numpy()
            if len(nonzero):
                thresholds[series_id] = float(np.quantile(nonzero, .75))
        train_labels = np.array([int(row.target > 0 and row.target >= thresholds.get(row.series_id, float("inf")))
                                 for row in train_rows.itertuples()])
        classifier = HistGradientBoostingClassifier(max_iter=100, learning_rate=.08, max_leaf_nodes=15,
                                                    l2_regularization=2.0, random_state=42)
        class_counts = np.bincount(train_labels, minlength=2)
        class_weights = np.array([len(train_labels) / max(2 * count, 1) for count in class_counts])
        classifier.fit(train_rows[FEATURES], train_labels, sample_weight=class_weights[train_labels])

        actuals, predictions = [], []
        stored_series = {}
        for series_id, group in frame.groupby("series_id", sort=False):
            group = group.sort_values("date")
            history_rows = group[group.date < cutoff]
            future_rows = group[group.date >= cutoff]
            history = history_rows.sales.astype(float).to_list()
            if len(history) < 3 or future_rows.empty:
                continue
            for row in future_rows.itertuples():
                x = pd.DataFrame([_features(history, pd.Timestamp(row.date))])[FEATURES]
                model_prediction = max(0.0, float(model.predict(x)[0]))
                seasonal_prediction = max(0.0, float(history[-1])) if history else 0.0
                prediction = max(0.0, blend_model_weight * model_prediction +
                                 (1.0 - blend_model_weight) * seasonal_prediction)
                actual = float(row.sales)
                actuals.append(actual)
                predictions.append(prediction)
                history.append(prediction)
            meta = metadata[series_id]
            stored_series[series_id] = {**meta, "sales_history": group.sales.astype(float).to_list(),
                                        "history": [{"date": (pd.Timestamp(r.date) + pd.offsets.MonthEnd(0)).date().isoformat(),
                                                     "units": round(float(r.sales) / pd.Period(r.date, freq="M").days_in_month, 2)}
                                                    for r in group.itertuples()]}

        validation_rows = features[features.date >= cutoff].copy()
        validation_rows = validation_rows[validation_rows.series_id.isin(thresholds)]
        true_high = np.array([int(row.target > 0 and row.target >= thresholds[row.series_id])
                              for row in validation_rows.itertuples()])
        predicted_high = classifier.predict(validation_rows[FEATURES])
        series_f1 = [f1_score(
            [int(row.target > 0 and row.target >= thresholds[series_id]) for row in group.itertuples()],
            classifier.predict(group[FEATURES]), zero_division=0)
            for series_id, group in validation_rows.groupby("series_id", sort=False)]
        actual_array, predicted_array = np.array(actuals), np.array(predictions)
        total_actual = float(actual_array.sum())
        metrics = {
            "selected_model": selected_model,
            "cv_folds": len(cv_actual[selected_model]) and 3 or 0,
            "cv_wape_percent": round(blended_cv_wape, 1) if np.isfinite(blended_cv_wape) else None,
            "model_cv_wape_percent": round(raw_model_wape, 1) if np.isfinite(raw_model_wape) else None,
            "blend_model_weight_percent": round(blend_model_weight * 100, 1),
            "baseline_wape_percent": round(baseline_wape, 1) if baseline_wape is not None else None,
            "beats_baseline": baseline_wape is not None and blended_cv_wape < baseline_wape,
            "wape_percent": round(float(np.abs(actual_array - predicted_array).sum()) / total_actual * 100, 1) if total_actual else None,
            "bias_percent": round(float((predicted_array.sum() - actual_array.sum()) / total_actual * 100), 1) if total_actual else None,
            "mae_units": round(float(mean_absolute_error(actuals, predictions)), 2),
            "rmse_units": round(float(np.sqrt(mean_squared_error(actuals, predictions))), 2),
            "f1_percent": round(float(f1_score(true_high, predicted_high, zero_division=0)) * 100, 1) if len(true_high) else 0.0,
            "precision_percent": round(float(precision_score(true_high, predicted_high, zero_division=0)) * 100, 1) if len(true_high) else 0.0,
            "recall_percent": round(float(recall_score(true_high, predicted_high, zero_division=0)) * 100, 1) if len(true_high) else 0.0,
            "macro_f1_percent": round(float(np.mean(series_f1)) * 100, 1) if series_f1 else 0.0,
            "series_count": len(stored_series), "validation_months": len(holdout_months),
            "f1_definition": "Clasificador de meses de demanda alta; positivo si las unidades mensuales alcanzan el percentil 75 de ventas positivas del producto en entrenamiento. Evaluación en los dos últimos meses.",
        }
        final_model = candidate_factories[selected_model]()
        final_model.fit(features[FEATURES], features.target)
        all_thresholds = {}
        for series_id, group in features.groupby("series_id", sort=False):
            nonzero = group.loc[group.target > 0, "target"].to_numpy()
            if len(nonzero):
                all_thresholds[series_id] = float(np.quantile(nonzero, .75))
        all_labels = np.array([int(row.target > 0 and row.target >= all_thresholds.get(row.series_id, float("inf")))
                               for row in features.itertuples()])
        all_counts = np.bincount(all_labels, minlength=2)
        all_weights = np.array([len(all_labels) / max(2 * count, 1) for count in all_counts])
        final_classifier = HistGradientBoostingClassifier(max_iter=100, learning_rate=.08, max_leaf_nodes=15,
                                                          l2_regularization=2.0, random_state=42)
        final_classifier.fit(features[FEATURES], all_labels, sample_weight=all_weights[all_labels])
        ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": final_model, "blend_model_weight": blend_model_weight,
                     "classifier": final_classifier, "series": stored_series,
                     "trained_at": pd.Timestamp.now(tz="UTC").isoformat(), "metrics": metrics,
                     "source": SOURCE_URL, "pln_pen_rate": PLN_PEN}, ARTIFACT)
        _status["training"] = False
        return status()
    except Exception as exc:
        _status["error"] = str(exc)
        raise
    finally:
        _status["training"] = False
        _lock.release()


def status() -> dict:
    base = {"ready": False, "training": _status["training"], "error": _status["error"],
            "source_label": "Datos públicos de supermercado con productos de arroz, azúcar y leche",
            "data_notice": "La fuente registra ventas mensuales de arroz, azúcar y leche en supermercados de Polonia; el ritmo mensual se prorratea al horizonte diario. No contiene aceite ni ventas o inventarios de Plaza Vea.",
            "price_notice": f"Precios convertidos de PLN a soles con tasa configurable: 1 PLN = S/ {PLN_PEN:.4f}.",
            "pln_pen_rate": PLN_PEN}
    if ARTIFACT.exists():
        try:
            saved = joblib.load(ARTIFACT)
            base.update(ready=True, trained_at=saved["trained_at"], metrics=saved["metrics"],
                        products=[{key: value[key] for key in ("id", "item_id", "name", "category", "store")} for value in saved["series"].values()])
        except Exception as exc:
            base["error"] = f"No se pudo leer el modelo: {exc}"
    return base


def predict(series_id: str, horizon: int) -> dict:
    if not ARTIFACT.exists():
        raise RuntimeError("Entrena el modelo para generar una predicción.")
    saved = joblib.load(ARTIFACT)
    item = saved["series"].get(series_id)
    if not item:
        raise KeyError("El producto no existe en el catálogo de referencia.")
    history = list(item["sales_history"])
    current = pd.Timestamp(date.fromisoformat(item["last_date"]))
    next_month = current + pd.offsets.MonthBegin(1)
    x = pd.DataFrame([_features(history, next_month)])[FEATURES]
    model_value = max(0.0, float(saved["model"].predict(x)[0]))
    previous_month = max(0.0, float(history[-1])) if history else 0.0
    model_weight = float(saved.get("blend_model_weight", 1.0))
    monthly_units = max(0.0, model_weight * model_value + (1.0 - model_weight) * previous_month)
    daily_rate = monthly_units / next_month.days_in_month
    first_day = next_month.date()
    points = [{"date": (first_day + timedelta(days=offset)).isoformat(), "units": round(daily_rate, 2)}
              for offset in range(horizon)]
    return {"product": {key: item[key] for key in ("id", "item_id", "name", "category", "store")},
            "history": item["history"], "forecast": points,
            "price_pen": item["price_pen"], "horizon": horizon,
            "estimated_units": round(daily_rate * horizon, 1),
            "model": saved["metrics"],
            "trained_at": saved["trained_at"], "source_label": "Scanner de supermercado · Polonia",
            "pln_pen_rate": PLN_PEN}
