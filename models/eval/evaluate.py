"""
models/eval/evaluate.py

Evaluate both models against a held-out temporal test split.
Outputs:
- models/eval/eval_report.json (machine-readable metrics)
- models/eval/confusion_matrix.png (confusion matrix plot)
"""

from __future__ import annotations

import glob
import json
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

EVAL_DIR = Path("models/eval")
TRUTH_ENGINE_FEATURES = [
    "z_score", "peer_deviation_score", "is_sustained_multiday_drop",
    "is_recurring_daily_pattern", "night_activity_score",
    "pct_deviation_from_baseline", "pct_deviation_from_peer_median",
    "pct_deviation_from_cluster_norm", "lag_1h", "lag_24h", "lag_48h",
    "lag_7d", "rolling_7d_mean", "rolling_7d_std", "trend_slope_3d",
]
LOAD_FORECAST_FEATURES = [
    "rolling_7d_mean", "rolling_7d_std", "trend_slope_3d",
    "lag_24h", "lag_48h", "lag_7d", "pct_deviation_from_cluster_norm",
    "z_score", "hour_of_day", "day_of_week",
]


def evaluate_models(
    data_dir: str = "data/processed",
    models_dir: str = "models",
    force: bool = False,
) -> dict:
    """
    Evaluate Truth Engine and Load Forecast models on a temporal test split.

    Parameters
    ----------
    data_dir : str
        Directory containing feature_matrix.parquet.
    models_dir : str
        Directory containing trained model files.
    force : bool
        If False and eval_report.json exists, skip.

    Returns
    -------
    dict
        Evaluation metrics for both models.
    """
    try:
        import joblib
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from sklearn.metrics import (
            precision_score, recall_score, f1_score,
            roc_auc_score, confusion_matrix,
        )
    except ImportError as e:
        print(f"[Evaluate] Missing dependency: {e}. Skipping.")
        return {}

    report_path = EVAL_DIR / "eval_report.json"
    if not force and report_path.exists():
        print(f"[Evaluate] Report already exists: {report_path}. Skipping.")
        with open(report_path) as f:
            return json.load(f)

    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    feature_matrix_path = Path(data_dir) / "feature_matrix.parquet"
    if not feature_matrix_path.exists():
        print(f"[Evaluate] Feature matrix not found: {feature_matrix_path}. Skipping.")
        return {}

    feature_matrix = pd.read_parquet(feature_matrix_path)
    feature_matrix["timestamp"] = pd.to_datetime(feature_matrix["timestamp"], utc=True)
    feature_matrix = feature_matrix.sort_values("timestamp").reset_index(drop=True)

    # Temporal test split: last 20% of data
    split_idx = int(len(feature_matrix) * 0.80)
    test_df = feature_matrix.iloc[split_idx:].copy()

    # Add hour/dow for load forecast
    test_df["hour_of_day"] = test_df["timestamp"].dt.hour
    test_df["day_of_week"] = test_df["timestamp"].dt.dayofweek

    # Cast bool columns
    for col in ("is_sustained_multiday_drop", "is_recurring_daily_pattern"):
        if col in test_df.columns:
            test_df[col] = test_df[col].astype("int8")

    report = {"evaluated_at": datetime.utcnow().isoformat()}

    # --- Truth Engine evaluation ---
    lgbm_files = sorted(glob.glob(str(Path(models_dir) / "truth_engine" / "lgbm_v1_*.joblib")))
    if lgbm_files and "confirmed_tamper" in test_df.columns:
        lgbm_model = joblib.load(lgbm_files[-1])
        available = [f for f in TRUTH_ENGINE_FEATURES if f in test_df.columns]
        X_test = test_df[available].fillna(0.0)
        y_test = test_df["confirmed_tamper"].astype(int)

        if y_test.sum() > 0:
            y_pred_proba = lgbm_model.predict_proba(X_test)[:, 1]
            y_pred = (y_pred_proba >= 0.5).astype(int)
            report["truth_engine"] = {
                "precision": float(precision_score(y_test, y_pred, zero_division=0)),
                "recall": float(recall_score(y_test, y_pred, zero_division=0)),
                "f1": float(f1_score(y_test, y_pred, zero_division=0)),
                "auc": float(roc_auc_score(y_test, y_pred_proba)),
                "model_file": lgbm_files[-1],
            }
            _save_confusion_matrix(
                confusion_matrix(y_test, y_pred),
                "Truth Engine",
                str(EVAL_DIR / "confusion_matrix.png"),
                plt,
            )
            print(f"[Evaluate] Truth Engine — AUC: {report['truth_engine']['auc']:.4f}, "
                  f"Precision: {report['truth_engine']['precision']:.4f}, "
                  f"Recall: {report['truth_engine']['recall']:.4f}")
        else:
            report["truth_engine"] = {"note": "No positive samples in test split"}
    else:
        report["truth_engine"] = {"note": "Model or labels not found"}

    # --- Load Forecast evaluation (aggregate across clusters) ---
    xgb_files = sorted(glob.glob(str(Path(models_dir) / "load_forecast" / "xgb_*_v1_*.joblib")))
    if xgb_files and "is_high_stress_zone" in test_df.columns:
        all_y_true, all_y_pred, all_y_proba = [], [], []
        for xgb_file in xgb_files:
            # Extract cluster_id from filename
            stem = Path(xgb_file).stem  # e.g. xgb_0_v1_20240101
            parts = stem.split("_")
            try:
                cluster_id = int(parts[1])
            except (IndexError, ValueError):
                continue

            if "cluster_id" not in test_df.columns:
                continue
            cluster_test = test_df[test_df["cluster_id"] == cluster_id]
            if cluster_test.empty:
                continue

            model = joblib.load(xgb_file)
            available = [f for f in LOAD_FORECAST_FEATURES if f in cluster_test.columns]
            X_c = cluster_test[available].fillna(0.0)
            y_c = cluster_test["is_high_stress_zone"].astype(int)

            if len(X_c) == 0:
                continue

            y_proba = model.predict_proba(X_c)[:, 1]
            y_pred_c = (y_proba >= 0.5).astype(int)
            all_y_true.extend(y_c.tolist())
            all_y_pred.extend(y_pred_c.tolist())
            all_y_proba.extend(y_proba.tolist())

        if all_y_true and sum(all_y_true) > 0:
            import numpy as np
            yt = np.array(all_y_true)
            yp = np.array(all_y_pred)
            ypr = np.array(all_y_proba)
            report["load_forecast"] = {
                "precision": float(precision_score(yt, yp, zero_division=0)),
                "recall": float(recall_score(yt, yp, zero_division=0)),
                "f1": float(f1_score(yt, yp, zero_division=0)),
                "auc": float(roc_auc_score(yt, ypr)),
                "n_models": len(xgb_files),
            }
            print(f"[Evaluate] Load Forecast — AUC: {report['load_forecast']['auc']:.4f}, "
                  f"Precision: {report['load_forecast']['precision']:.4f}")
        else:
            report["load_forecast"] = {"note": "No positive samples in test split"}
    else:
        report["load_forecast"] = {"note": "Models or labels not found"}

    # Write report
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"[Evaluate] Report written → {report_path}")

    return report


def _save_confusion_matrix(cm, title: str, path: str, plt) -> None:
    """Save a confusion matrix plot to disk."""
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(cm, interpolation="nearest", cmap=plt.cm.Blues)
    plt.colorbar(im, ax=ax)
    ax.set_title(f"Confusion Matrix — {title}")
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
    classes = ["Normal", "Anomaly"]
    tick_marks = range(len(classes))
    ax.set_xticks(tick_marks)
    ax.set_xticklabels(classes)
    ax.set_yticks(tick_marks)
    ax.set_yticklabels(classes)
    thresh = cm.max() / 2.0
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            ax.text(j, i, str(cm[i, j]),
                    ha="center", va="center",
                    color="white" if cm[i, j] > thresh else "black")
    plt.tight_layout()
    plt.savefig(path, dpi=100)
    plt.close(fig)
    print(f"[Evaluate] Confusion matrix saved → {path}")
