"""
models/truth_engine/shap_explainer.py

Compute SHAP values for each alert and generate plain-English explanations
for the top-3 contributing features.

SHAP values are computed at alert write time and stored in the DB.
They are NEVER recomputed on frontend request.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from models.truth_engine.train import TRUTH_ENGINE_FEATURES

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
HOUR_LABELS = {
    0: "midnight", 1: "1 AM", 2: "2 AM", 3: "3 AM", 4: "4 AM", 5: "5 AM",
    6: "6 AM", 7: "7 AM", 8: "8 AM", 9: "9 AM", 10: "10 AM", 11: "11 AM",
    12: "noon", 13: "1 PM", 14: "2 PM", 15: "3 PM", 16: "4 PM", 17: "5 PM",
    18: "6 PM", 19: "7 PM", 20: "8 PM", 21: "9 PM", 22: "10 PM", 23: "11 PM",
}


def generate_plain_english(feature: str, shap_val: float, feature_val: float, ctx: dict) -> str:
    """
    Generate a plain-English explanation for a SHAP feature contribution.

    Parameters
    ----------
    feature : str
        Feature name.
    shap_val : float
        SHAP value (positive = pushes toward tamper).
    feature_val : float
        Actual feature value.
    ctx : dict
        Context dict with meter metadata (hour_of_day, day_of_week, neighbor_count, etc.)

    Returns
    -------
    str
        Human-readable explanation.
    """
    hour = int(ctx.get("hour_of_day", 12))
    dow = int(ctx.get("day_of_week", 0))
    hour_label = HOUR_LABELS.get(hour, f"{hour}:00")
    day_label = DAYS_OF_WEEK[dow % 7]
    neighbor_count = int(ctx.get("neighbor_count", 0))
    repeat_days = int(ctx.get("repeat_days_count", 0))

    if feature == "pct_deviation_from_baseline":
        pct = abs(feature_val) if not np.isnan(feature_val) else 0
        direction = "below" if feature_val < 0 else "above"
        return (
            f"Consumption is {pct:.0f}% {direction} this meter's "
            f"28-day {day_label} {hour_label} average."
        )

    elif feature in ("peer_deviation_score", "pct_deviation_from_peer_median"):
        pct = abs(feature_val) if not np.isnan(feature_val) else 0
        direction = "below" if feature_val < 0 else "above"
        n = neighbor_count if neighbor_count > 0 else "nearby"
        return (
            f"Consumption is {pct:.0f}% {direction} the median of "
            f"{n} neighboring meters at the same time."
        )

    elif feature == "night_activity_score":
        val = feature_val if not np.isnan(feature_val) else 0
        return (
            f"Night-time usage (10 PM–5 AM) is {val:.1f}x the meter's normal level."
        )

    elif feature == "z_score":
        z = abs(feature_val) if not np.isnan(feature_val) else 0
        direction = "below" if feature_val < 0 else "above"
        return (
            f"Consumption is {z:.1f} standard deviations {direction} "
            f"the meter's historical mean."
        )

    elif feature == "is_recurring_daily_pattern":
        return (
            f"A consistent daily dip pattern has repeated on "
            f"{repeat_days} of the last 5 days."
        )

    elif feature == "pct_deviation_from_cluster_norm":
        pct = abs(feature_val) if not np.isnan(feature_val) else 0
        direction = "below" if feature_val < 0 else "above"
        return (
            f"Consumption is {pct:.0f}% {direction} the cluster norm "
            f"for this time slot."
        )

    elif feature == "is_sustained_multiday_drop":
        return "Consumption has been more than 50% below baseline for 3 or more consecutive days."

    elif feature == "trend_slope_3d":
        direction = "declining" if feature_val < 0 else "rising"
        return f"Consumption has been {direction} steadily over the past 3 days."

    elif feature in ("lag_1h", "lag_24h", "lag_48h", "lag_7d"):
        lag_labels = {
            "lag_1h": "1 hour ago",
            "lag_24h": "24 hours ago",
            "lag_48h": "48 hours ago",
            "lag_7d": "7 days ago",
        }
        val = feature_val if not np.isnan(feature_val) else 0
        return f"Consumption {lag_labels[feature]} was {val:.2f} kWh."

    elif feature == "rolling_7d_mean":
        val = feature_val if not np.isnan(feature_val) else 0
        return f"The 7-day rolling average consumption is {val:.2f} kWh."

    elif feature == "rolling_7d_std":
        val = feature_val if not np.isnan(feature_val) else 0
        return f"Consumption variability over the past 7 days is {val:.2f} kWh (std dev)."

    else:
        return f"{feature} = {feature_val:.3f} (contribution: {shap_val:+.3f})."


def explain_alert(
    alert_features: dict,
    lgbm_model: Any,
) -> list[dict]:
    """
    Compute SHAP values and return top-3 plain-English explanations.

    Parameters
    ----------
    alert_features : dict
        Feature values for the alert (keys = TRUTH_ENGINE_FEATURES).
    lgbm_model : LGBMClassifier
        Trained Truth Engine model.

    Returns
    -------
    list[dict]
        List of 3 dicts: [{feature, value, plain_english}] sorted by abs(shap_value) DESC.
    """
    try:
        import shap
    except ImportError:
        return [
            {"feature": "pct_deviation_from_baseline",
             "value": 0.0,
             "plain_english": "SHAP library not installed. Install with: pip install shap"}
        ]

    available = [f for f in TRUTH_ENGINE_FEATURES if f in alert_features]
    feature_vector = pd.DataFrame([{f: alert_features.get(f, np.nan) for f in available}])

    # Fill NaN
    feature_vector = feature_vector.fillna(0.0)

    # Cast bool-like columns
    for col in ("is_sustained_multiday_drop", "is_recurring_daily_pattern"):
        if col in feature_vector.columns:
            feature_vector[col] = feature_vector[col].astype("int8")

    try:
        explainer = shap.TreeExplainer(lgbm_model)
        shap_values = explainer.shap_values(feature_vector)

        # LightGBM returns list [class0_shap, class1_shap]
        if isinstance(shap_values, list) and len(shap_values) == 2:
            sv = shap_values[1][0]
        elif isinstance(shap_values, np.ndarray) and shap_values.ndim == 2:
            sv = shap_values[0]
        else:
            sv = np.array(shap_values).flatten()

        feature_names = feature_vector.columns.tolist()
        feature_vals = feature_vector.values[0]

        # Rank by absolute SHAP value
        ranked = sorted(
            zip(feature_names, sv, feature_vals),
            key=lambda x: abs(x[1]),
            reverse=True,
        )
        top3 = ranked[:3]

        ctx = {
            "hour_of_day": alert_features.get("hour_of_day", 12),
            "day_of_week": alert_features.get("day_of_week", 0),
            "neighbor_count": alert_features.get("neighbor_count", 0),
            "repeat_days_count": alert_features.get("repeat_days_count", 0),
        }

        result = []
        for feat_name, shap_val, feat_val in top3:
            plain = generate_plain_english(feat_name, float(shap_val), float(feat_val), ctx)
            result.append({
                "feature": feat_name,
                "value": float(shap_val),
                "plain_english": plain,
            })
        return result

    except Exception as e:
        return [
            {"feature": "error", "value": 0.0, "plain_english": f"SHAP computation failed: {e}"}
        ]


def update_alert_shap(
    alert_id: str,
    alert_features: dict,
    lgbm_model: Any,
    db_path: str = "data/synapse_grid.db",
) -> None:
    """
    Compute SHAP values for an alert and write shap_top3 to the DB.

    Parameters
    ----------
    alert_id : str
        UUID of the alert in alert_events.
    alert_features : dict
        Feature values for the alert.
    lgbm_model : Any
        Trained LightGBM model.
    db_path : str
        Path to the SQLite database.
    """
    shap_top3 = explain_alert(alert_features, lgbm_model)
    shap_json = json.dumps(shap_top3)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE alert_events SET shap_top3 = ? WHERE alert_id = ?",
        (shap_json, alert_id),
    )
    conn.commit()
    conn.close()
