"""
models/truth_engine/train.py

Train a LightGBM classifier to produce anomaly_confidence_score.
Target: confirmed_tamper (ground truth from injected_events.json).

Uses TimeSeriesSplit (never shuffle). Saves best fold model by AUC.
Saves model as: models/truth_engine/lgbm_v1_{YYYYMMDD}.joblib
"""

from __future__ import annotations

import warnings
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)

TRUTH_ENGINE_FEATURES = [
    "z_score",
    "peer_deviation_score",
    "is_sustained_multiday_drop",
    "is_recurring_daily_pattern",
    "night_activity_score",
    "pct_deviation_from_baseline",
    "pct_deviation_from_peer_median",
    "pct_deviation_from_cluster_norm",
    "lag_1h",
    "lag_24h",
    "lag_48h",
    "lag_7d",
    "rolling_7d_mean",
    "rolling_7d_std",
    "trend_slope_3d",
]

MODEL_DIR = Path("models/truth_engine")


def train_truth_engine(
    data_dir: str = "data/processed",
    force: bool = False,
) -> str:
    """
    Train the LightGBM Truth Engine classifier.

    Parameters
    ----------
    data_dir : str
        Directory containing feature_matrix.parquet.
    force : bool
        If False and a model already exists for today, skip.

    Returns
    -------
    str
        Path to the saved model file.
    """
    try:
        import lightgbm as lgb
        from sklearn.model_selection import TimeSeriesSplit
        from sklearn.metrics import roc_auc_score
    except ImportError as e:
        print(f"[Train Truth Engine] Missing dependency: {e}. Skipping.")
        return ""

    feature_matrix_path = Path(data_dir) / "feature_matrix.parquet"
    if not feature_matrix_path.exists():
        raise FileNotFoundError(f"Feature matrix not found: {feature_matrix_path}")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.today().strftime("%Y%m%d")
    model_path = str(MODEL_DIR / f"lgbm_v1_{date_str}.joblib")

    if not force and Path(model_path).exists():
        print(f"[Train Truth Engine] Model already exists: {model_path}. Skipping.")
        return model_path

    feature_matrix = pd.read_parquet(feature_matrix_path)
    feature_matrix = feature_matrix.sort_values("timestamp").reset_index(drop=True)

    # Cast bool columns to int8
    for col in ("is_sustained_multiday_drop", "is_recurring_daily_pattern"):
        if col in feature_matrix.columns:
            feature_matrix[col] = feature_matrix[col].astype("int8")

    available_features = [f for f in TRUTH_ENGINE_FEATURES if f in feature_matrix.columns]
    X = feature_matrix[available_features].copy()

    # Fill NaN with column median (computed on full dataset for simplicity in prototype)
    col_medians = X.median(numeric_only=True)
    X = X.fillna(col_medians)

    if "confirmed_tamper" not in feature_matrix.columns:
        raise ValueError("confirmed_tamper column missing from feature matrix")

    y = feature_matrix["confirmed_tamper"].astype(int)

    if y.sum() == 0:
        print("[Train Truth Engine] No positive samples found. Training with synthetic labels.")
        # For demo: create synthetic labels based on pct_deviation_from_baseline
        if "pct_deviation_from_baseline" in X.columns:
            y = (X["pct_deviation_from_baseline"] < -70).astype(int)

    tscv = TimeSeriesSplit(n_splits=5)
    best_model = None
    best_auc = 0.0

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
        X_val, y_val = X.iloc[val_idx], y.iloc[val_idx]

        if y_train.sum() == 0 or y_val.sum() == 0:
            continue

        model = lgb.LGBMClassifier(
            n_estimators=300,
            max_depth=6,
            learning_rate=0.05,
            num_leaves=31,
            class_weight="balanced",
            random_state=42,
            verbose=-1,
        )
        model.fit(
            X_train,
            y_train,
            eval_set=[(X_val, y_val)],
            callbacks=[
                lgb.early_stopping(30, verbose=False),
                lgb.log_evaluation(-1),
            ],
        )

        val_pred = model.predict_proba(X_val)[:, 1]
        fold_auc = roc_auc_score(y_val, val_pred)
        print(f"[Train Truth Engine] Fold {fold + 1} AUC: {fold_auc:.4f}")

        if fold_auc > best_auc:
            best_auc = fold_auc
            best_model = model

    if best_model is None:
        print("[Train Truth Engine] No valid fold found. Training on full dataset.")
        best_model = lgb.LGBMClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            num_leaves=15,
            class_weight="balanced",
            random_state=42,
            verbose=-1,
        )
        best_model.fit(X, y)

    joblib.dump(best_model, model_path)
    print(f"[Train Truth Engine] Saved model → {model_path} (best AUC: {best_auc:.4f})")
    return model_path
