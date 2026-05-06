"""
models/inference_runner.py

Orchestrates the full daily inference pipeline:
1. Load feature matrix
2. Load trained models
3. Score all meters via Truth Engine
4. Write alerts to alert_events / shadow_events
5. Compute SHAP explanations for each alert
6. Update feeder_status table with current utilization and 24h forecast
"""

from __future__ import annotations

import glob
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DB_PATH_DEFAULT = "data/synapse_grid.db"
FEATURE_MATRIX_PATH = "data/processed/feature_matrix.parquet"
MODELS_DIR = Path("models")

LOAD_FORECAST_FEATURES = [
    "rolling_7d_mean", "rolling_7d_std", "trend_slope_3d",
    "lag_24h", "lag_48h", "lag_7d", "pct_deviation_from_cluster_norm",
    "z_score", "hour_of_day", "day_of_week",
]


def run_inference_and_write_alerts(
    data_dir: str = "data/processed",
    force: bool = False,
    db_path: str = DB_PATH_DEFAULT,
) -> dict:
    """
    Run the full inference pipeline: score all meters and write alerts.

    Parameters
    ----------
    data_dir : str
        Directory containing feature_matrix.parquet.
    force : bool
        Passed through to sub-stages.
    db_path : str
        Path to the SQLite database.

    Returns
    -------
    dict
        Summary: {meters_processed, alerts_new, alerts_watching, shadow_records}
    """
    try:
        import joblib
    except ImportError:
        print("[Inference] joblib not installed. Skipping.")
        return {}

    feature_matrix_path = Path(data_dir) / "feature_matrix.parquet"
    if not feature_matrix_path.exists():
        raise FileNotFoundError(f"Feature matrix not found: {feature_matrix_path}")

    feature_matrix = pd.read_parquet(feature_matrix_path)
    feature_matrix["timestamp"] = pd.to_datetime(feature_matrix["timestamp"], utc=True)

    # --- Load Truth Engine model ---
    lgbm_files = sorted(glob.glob(str(MODELS_DIR / "truth_engine" / "lgbm_v1_*.joblib")))
    if not lgbm_files:
        print("[Inference] No Truth Engine model found. Skipping scoring.")
        return {"meters_processed": 0, "alerts_new": 0, "alerts_watching": 0, "shadow_records": 0}

    lgbm_model = joblib.load(lgbm_files[-1])
    print(f"[Inference] Loaded Truth Engine: {lgbm_files[-1]}")

    # --- Score and gate ---
    from models.truth_engine.scorer import score_and_gate, get_pipeline_summary
    summary = score_and_gate(feature_matrix, lgbm_model, db_path=db_path)
    print(f"[Inference] Alerts NEW: {summary['alerts_new']}, "
          f"WATCHING: {summary['alerts_watching']}, "
          f"Shadow: {summary['shadow_records']}")

    # --- SHAP explanations for NEW/WATCHING alerts ---
    _update_shap_for_alerts(lgbm_model, feature_matrix, db_path)

    # --- Update feeder_status table ---
    _update_feeder_status(feature_matrix, db_path)

    # Return full summary
    full_summary = get_pipeline_summary(db_path)
    full_summary["meters_processed"] = int(feature_matrix["meter_id"].nunique())
    return full_summary


def _update_shap_for_alerts(lgbm_model, feature_matrix: pd.DataFrame, db_path: str) -> None:
    """Compute and store SHAP values for all alerts that don't have them yet."""
    try:
        from models.truth_engine.shap_explainer import update_alert_shap
        from models.truth_engine.train import TRUTH_ENGINE_FEATURES
    except ImportError:
        return

    conn = sqlite3.connect(db_path)
    try:
        alerts = conn.execute(
            "SELECT alert_id, meter_id, triggered_at FROM alert_events "
            "WHERE shap_top3 IS NULL OR shap_top3 = '[]'"
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return

    if not alerts:
        conn.close()
        return

    # Build a lookup: meter_id → latest feature row
    latest = (
        feature_matrix.sort_values("timestamp")
        .groupby("meter_id", sort=False)
        .last()
        .reset_index()
    )
    feature_lookup = latest.set_index("meter_id").to_dict(orient="index")

    for alert_id, meter_id, triggered_at in alerts:
        if meter_id not in feature_lookup:
            continue
        row = feature_lookup[meter_id]
        # Add context fields
        ts = pd.to_datetime(triggered_at, utc=True)
        row["hour_of_day"] = ts.hour
        row["day_of_week"] = ts.dayofweek
        row["neighbor_count"] = 0  # simplified for prototype
        row["repeat_days_count"] = 0
        update_alert_shap(alert_id, row, lgbm_model, db_path=db_path)

    conn.close()
    print(f"[Inference] SHAP values computed for {len(alerts)} alerts.")


def _update_feeder_status(feature_matrix: pd.DataFrame, db_path: str) -> None:
    """Update feeder_status table with current utilization and 24h forecast."""
    try:
        import joblib
    except ImportError:
        return

    conn = sqlite3.connect(db_path)

    # Ensure feeder_status table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feeder_status (
            feeder_id               TEXT PRIMARY KEY,
            current_utilization_pct REAL,
            stress_level            TEXT,
            transformer_rated_kva   REAL,
            forecast_24h            TEXT,
            updated_at              TEXT
        )
    """)

    # Load zone profiles for current utilization
    zone_path = Path("data/processed/zone_profiles.parquet")
    if not zone_path.exists():
        conn.close()
        return

    zone_df = pd.read_parquet(zone_path)
    zone_df["timestamp"] = pd.to_datetime(zone_df["timestamp"], utc=True)

    # Get latest feeder stress per feeder
    latest_zone = (
        zone_df.sort_values("timestamp")
        .groupby("feeder_id", sort=False)
        .last()
        .reset_index()
    )

    now_iso = datetime.now(timezone.utc).isoformat()

    for _, row in latest_zone.iterrows():
        feeder_id = str(row["feeder_id"])
        utilization_pct = float(row.get("feeder_stress", 0.0) * 100.0)
        transformer_kva = float(row.get("transformer_rated_kva", 0.0))

        # Stress level
        if utilization_pct <= 70.0:
            stress_level = "GREEN"
        elif utilization_pct <= 90.0:
            stress_level = "AMBER"
        else:
            stress_level = "RED"

        # Simple 24h forecast: use XGBoost models if available
        forecast_24h = _generate_forecast(feeder_id, feature_matrix)

        conn.execute(
            "INSERT OR REPLACE INTO feeder_status "
            "(feeder_id, current_utilization_pct, stress_level, transformer_rated_kva, "
            "forecast_24h, updated_at) VALUES (?,?,?,?,?,?)",
            (feeder_id, utilization_pct, stress_level, transformer_kva,
             json.dumps(forecast_24h), now_iso),
        )

    conn.commit()
    conn.close()
    print(f"[Inference] Feeder status updated for {len(latest_zone)} feeders.")


def _generate_forecast(feeder_id: str, feature_matrix: pd.DataFrame) -> list[dict]:
    """
    Generate a simple 24h utilization forecast for a feeder.
    Uses the most recent 24h of feeder data as a naive forecast.
    """
    try:
        import joblib
        import numpy as np
    except ImportError:
        return []

    # Load XGBoost models
    xgb_files = sorted(glob.glob(str(MODELS_DIR / "load_forecast" / "xgb_*_v1_*.joblib")))
    if not xgb_files:
        return []

    # Get feeder meters
    feeder_meters = feature_matrix[feature_matrix.get("feeder_id", pd.Series()) == feeder_id]
    if "feeder_id" not in feature_matrix.columns:
        return []

    feeder_meters = feature_matrix[feature_matrix["feeder_id"] == feeder_id]
    if feeder_meters.empty:
        return []

    # Use the most recent cluster for this feeder
    if "cluster_id" not in feeder_meters.columns:
        return []

    cluster_id = int(feeder_meters["cluster_id"].mode().iloc[0])
    matching_models = [f for f in xgb_files if f"xgb_{cluster_id}_" in f]
    if not matching_models:
        return []

    model = joblib.load(matching_models[-1])
    latest = (
        feeder_meters.sort_values("timestamp")
        .groupby("meter_id", sort=False)
        .last()
        .reset_index()
    )

    available = [f for f in LOAD_FORECAST_FEATURES if f in latest.columns]
    if not available:
        return []

    X = latest[available].fillna(0.0)
    proba = model.predict_proba(X)[:, 1].mean()

    # Generate 24 hourly forecast points
    now = datetime.now(timezone.utc)
    forecast = []
    for h in range(24):
        ts = now.replace(minute=0, second=0, microsecond=0)
        ts = ts.replace(hour=(ts.hour + h) % 24)
        forecast.append({
            "timestamp": ts.isoformat(),
            "predicted_utilization_pct": float(proba * 100.0),
        })

    return forecast
