"""
models/load_forecast/train.py

Train one XGBoost binary classifier per cluster_id on the feature matrix.
Target: is_high_stress_zone (feeder utilization > 90% in next 24h).

Uses TimeSeriesSplit (never shuffle). Handles class imbalance with scale_pos_weight.
Applies SMOTE on training split only.

Saves each model as: models/load_forecast/xgb_{cluster_id}_v1_{YYYYMMDD}.joblib
"""

from __future__ import annotations

import warnings
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)

LOAD_FORECAST_FEATURES = [
    "rolling_7d_mean",
    "rolling_7d_std",
    "trend_slope_3d",
    "lag_24h",
    "lag_48h",
    "lag_7d",
    "pct_deviation_from_cluster_norm",
    "z_score",
]

MODEL_DIR = Path("models/load_forecast")


def train_load_forecast_models(
    data_dir: str = "data/processed",
    force: bool = False,
) -> dict[int, str]:
    """
    Train one XGBoost classifier per cluster on the feature matrix.

    Parameters
    ----------
    data_dir : str
        Directory containing feature_matrix.parquet.
    force : bool
        If False and models already exist for today, skip.

    Returns
    -------
    dict[int, str]
        Mapping of cluster_id → saved model path.
    """
    try:
        import xgboost as xgb
        from sklearn.model_selection import TimeSeriesSplit
        from imblearn.over_sampling import SMOTE
    except ImportError as e:
        print(f"[Train Load Forecast] Missing dependency: {e}. Skipping.")
        return {}

    feature_matrix_path = Path(data_dir) / "feature_matrix.parquet"
    if not feature_matrix_path.exists():
        raise FileNotFoundError(f"Feature matrix not found: {feature_matrix_path}")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.today().strftime("%Y%m%d")

    feature_matrix = pd.read_parquet(feature_matrix_path)

    # Add hour_of_day and day_of_week as features
    feature_matrix["timestamp"] = pd.to_datetime(feature_matrix["timestamp"], utc=True)
    feature_matrix["hour_of_day"] = feature_matrix["timestamp"].dt.hour
    feature_matrix["day_of_week"] = feature_matrix["timestamp"].dt.dayofweek

    all_features = LOAD_FORECAST_FEATURES + ["hour_of_day", "day_of_week"]

    # Ensure is_high_stress_zone exists
    if "is_high_stress_zone" not in feature_matrix.columns:
        feature_matrix["is_high_stress_zone"] = False

    model_paths: dict[int, str] = {}
    cluster_ids = sorted(feature_matrix["cluster_id"].dropna().unique().astype(int).tolist())

    for cluster_id in cluster_ids:
        cluster_df = feature_matrix[feature_matrix["cluster_id"] == cluster_id].copy()
        cluster_df = cluster_df.sort_values("timestamp").reset_index(drop=True)

        available_features = [f for f in all_features if f in cluster_df.columns]
        X = cluster_df[available_features].copy()
        y = cluster_df["is_high_stress_zone"].astype(int)

        # Fill NaN with column median
        X = X.fillna(X.median(numeric_only=True))

        if len(X) < 100 or y.sum() == 0:
            print(f"[Cluster {cluster_id}] Insufficient data or no positive samples, skipping.")
            continue

        # Compute scale_pos_weight
        neg_count = (y == 0).sum()
        pos_count = (y == 1).sum()
        scale_pos_weight = float(neg_count / pos_count) if pos_count > 0 else 1.0

        tscv = TimeSeriesSplit(n_splits=5)
        best_model = None
        best_score = -1.0

        for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
            X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
            X_val, y_val = X.iloc[val_idx], y.iloc[val_idx]

            # SMOTE on training split ONLY
            if y_train.sum() >= 2 and (y_train == 0).sum() >= 2:
                try:
                    smote = SMOTE(random_state=42, k_neighbors=min(5, y_train.sum() - 1))
                    X_train_res, y_train_res = smote.fit_resample(X_train, y_train)
                except Exception:
                    X_train_res, y_train_res = X_train, y_train
            else:
                X_train_res, y_train_res = X_train, y_train

            model = xgb.XGBClassifier(
                n_estimators=200,
                max_depth=6,
                learning_rate=0.05,
                scale_pos_weight=scale_pos_weight,
                eval_metric="logloss",
                random_state=42,
                verbosity=0,
                use_label_encoder=False,
            )
            model.fit(
                X_train_res,
                y_train_res,
                eval_set=[(X_val, y_val)],
                verbose=False,
            )

            # Track best fold by validation logloss (lower is better)
            val_pred = model.predict_proba(X_val)[:, 1]
            from sklearn.metrics import roc_auc_score
            if y_val.sum() > 0:
                score = roc_auc_score(y_val, val_pred)
                if score > best_score:
                    best_score = score
                    best_model = model

        if best_model is None:
            # Fall back: train on all data
            best_model = xgb.XGBClassifier(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.1,
                scale_pos_weight=scale_pos_weight,
                eval_metric="logloss",
                random_state=42,
                verbosity=0,
                use_label_encoder=False,
            )
            best_model.fit(X, y, verbose=False)

        model_path = str(MODEL_DIR / f"xgb_{cluster_id}_v1_{date_str}.joblib")
        joblib.dump(best_model, model_path)
        model_paths[cluster_id] = model_path
        print(f"[Cluster {cluster_id}] Saved model → {model_path} (best AUC: {best_score:.3f})")

    return model_paths
