# Implementation Tasks — Synapse-Grid


## Phase 1 — Project Scaffolding & Synthetic Data

- [x] 1. Create project directory structure and scaffolding
  - [x] 1.1 Create all required directories: `data/raw/`, `data/processed/`, `pipeline/ingest/`, `pipeline/peer_graph/`, `pipeline/impute/`, `pipeline/features/`, `pipeline/clustering/`, `models/load_forecast/`, `models/truth_engine/`, `models/eval/`, `api/routers/`, `scripts/`, `tests/`, `demo/`, `eval/`
  - [x] 1.2 Create `__init__.py` files for all Python packages: `pipeline/`, `pipeline/ingest/`, `pipeline/peer_graph/`, `pipeline/impute/`, `pipeline/features/`, `pipeline/clustering/`, `models/`, `api/`, `api/routers/`
  - [x] 1.3 Write `requirements.txt` with all pinned dependencies: pandas==2.2.2, pyarrow==16.1.0, numpy==1.26.4, geopandas==0.14.4, shapely==2.0.4, scikit-learn==1.5.0, xgboost==2.0.3, lightgbm==4.3.0, shap==0.45.1, imbalanced-learn==0.12.3, joblib==1.4.2, fastapi==0.111.0, uvicorn[standard]==0.30.1, sqlalchemy==2.0.30, aiosqlite==0.20.0, pydantic==2.7.1, pytest==8.2.2, pytest-asyncio==0.23.7, httpx==0.27.0

- [x] 2. Generate synthetic sample data
  - [x] 2.1 Write `scripts/generate_synthetic_data.py` — registry generation: 50 meters in 5 geographic clusters (10 meters each, ~100m apart), cluster centers in Bangalore area (12.97°N, 77.59°E ± 0.01°), 5 feeders (F001–F005), 2 transformers (T001–T002), sanctioned_kva from [25, 50, 63, 100] kVA
  - [x] 2.2 Implement base load generation in `generate_synthetic_data.py`: 90 days × 96 slots/day × 50 meters = 432,000 rows, hour-of-day load curve (peak 18–21h, trough 2–5h), weekday/weekend factor (1.0x/0.8x), seasonal factor (summer +20%, winter -10%), ±15% noise
  - [x] 2.3 Inject 5 tamper/theft patterns (METER_T001–METER_T005): daytime kwh drops to 5–15% of normal (≥80% drop), night activity preserved at 60–80% of normal (bypass signature), staggered start days (60, 63, 65, 67, 70), `confirmed_tamper=1` label for these meters in tamper period
  - [x] 2.4 Inject 2 grid stress events: F001 days 75–77 hours 14–20 spike_factor=1.5, F002 days 82–84 hours 11–17 spike_factor=1.4, resulting in feeder utilization >90% of transformer_rated_kva
  - [x] 2.5 Inject 2 vacation patterns (METER_V001, METER_V002): days 45–55 and 48–58, all-hours drop to 5–10% including night (distinguishes from tamper), `confirmed_tamper=0`
  - [x] 2.6 Inject 3 short-gap meters (METER_G001–G003): 1–3 consecutive NaN slots at random timestamps in days 20–70
  - [x] 2.7 Inject 2 extended-gap meters (METER_G004–G005): 4–8 consecutive NaN slots at random timestamps
  - [x] 2.8 Write `data/raw/sample_readings.csv`, `data/raw/sample_registry.csv`, and `data/raw/injected_events.json` documenting all injected patterns with meter IDs, start/end days, and parameters

- [x] 3. Scaffold React frontend
  - [x] 3.1 Write `frontend/package.json` with dependencies: react@^18.3.1, react-dom, react-leaflet@^4.2.1, leaflet@^1.9.4, recharts@^2.12.7, @tanstack/react-query@^5.40.0, zustand@^4.5.2, date-fns@^3.6.0, and devDependencies: typescript, vite, @vitejs/plugin-react, tailwindcss, autoprefixer, postcss, @types/react, @types/leaflet
  - [x] 3.2 Write `frontend/vite.config.ts` with React plugin and proxy `/api` → `http://localhost:8000`
  - [x] 3.3 Write `frontend/tailwind.config.ts` and `frontend/postcss.config.js`
  - [x] 3.4 Write `frontend/src/main.tsx` (React root with QueryClientProvider) and `frontend/src/App.tsx` (single Dashboard route)


## Phase 2 — Data Ingestion Pipeline

- [x] 4. Implement `pipeline/ingest/meter_reader.py`
  - [x] 4.1 Implement `load_readings(file_paths: list[str]) -> pd.DataFrame`: iterate file paths, call `pd.read_csv`, validate all 6 required columns present (meter_id, timestamp, kwh, voltage, power_factor, reactive_power), raise `ValueError` with missing column name if any absent
  - [x] 4.2 Cast types: meter_id → str, timestamp → datetime64[ns, UTC] via `pd.to_datetime(..., utc=True)`, kwh/voltage/power_factor/reactive_power → float64
  - [x] 4.3 Concatenate all per-file DataFrames, sort by (meter_id, timestamp) ascending, return combined DataFrame

- [x] 5. Implement `pipeline/ingest/meter_registry.py`
  - [x] 5.1 Implement `load_registry(file_path: str) -> tuple[pd.DataFrame, dict]`: call `pd.read_csv`, validate all 9 required columns (meter_id, lat, lng, feeder_id, transformer_id, zone, consumer_category, sanctioned_kva, connection_date), raise `ValueError` with missing column name if any absent
  - [x] 5.2 Cast types: sanctioned_kva → float64 (raise if non-positive), connection_date → datetime.date, lat/lng → float64
  - [x] 5.3 Build O(1) lookup dict via `df.set_index("meter_id").to_dict(orient="index")`, return `(df, registry_dict)` tuple

- [x] 6. Implement `pipeline/ingest/validator.py`
  - [x] 6.1 Implement `validate_readings(df: pd.DataFrame, db_path: str = "data/processed/data_quality_log.db") -> pd.DataFrame`: open SQLite connection, create `quality_violations` table if not exists with columns (id, meter_id, timestamp, violation_type, field_name, observed_value)
  - [x] 6.2 Rule 1 — Negative kWh: flag rows where kwh < 0, log violation_type="NEGATIVE_KWH"
  - [x] 6.3 Rule 2 — Non-monotonic timestamp: group by meter_id, flag rows where timestamp ≤ previous timestamp for that meter, log violation_type="NON_MONOTONIC_TIMESTAMP"
  - [x] 6.4 Rule 3 — Voltage out of range: flag rows where voltage < 180 or voltage > 260, log violation_type="VOLTAGE_OUT_OF_RANGE", do NOT drop the row
  - [x] 6.5 Rule 4 — Power factor out of range: flag rows where power_factor < 0 or power_factor > 1, log violation_type="POWER_FACTOR_OUT_OF_RANGE"
  - [x] 6.6 Process all rows in single pass without halting, commit all violations in one transaction, return original DataFrame unmodified

- [x] 7. Implement `pipeline/impute/gap_handler.py`
  - [x] 7.1 Implement `handle_gaps(df: pd.DataFrame, flags_path: str = "data/processed/hardware_issue_flags.csv") -> pd.DataFrame`: for each meter_id, reindex time series to complete 15-minute grid using `pd.date_range(freq="15min")`
  - [x] 7.2 Detect consecutive NaN runs using run-length encoding on the kwh column per meter
  - [x] 7.3 Short gap (1–3 consecutive NaN slots): for each missing slot, compute 7-day same-slot median (same hour_of_day and day_of_week from preceding 28 days, minimum 1 valid value), fill gap with median
  - [x] 7.4 Extended gap (>3 consecutive NaN slots): do NOT impute, write record to hardware_issue_flags.csv with columns (meter_id, gap_start, gap_end, gap_length_slots)
  - [x] 7.5 Write hardware_issue_flags.csv with header even if no extended gaps exist; return imputed DataFrame (extended-gap slots remain NaN)

- [x] 8. Implement `pipeline/peer_graph/builder.py`
  - [x] 8.1 Implement `build_peer_graph(registry_df: pd.DataFrame, output_path: str = "data/processed/peer_graph.json") -> dict`: for each pair of meters (i, j), compute Haversine distance using R=6371000m formula
  - [x] 8.2 If distance ≤ 200m, add j to i's neighbor list and i to j's neighbor list (bidirectional)
  - [x] 8.3 For meters with no neighbors within 200m, store empty list; serialize adjacency dict to JSON and write to peer_graph.json; return the dict

- [x] 9. Write pytest unit tests for Phase 2 modules
  - [x] 9.1 `tests/test_meter_reader.py`: test schema validation raises on missing column, test typed output (timestamp is datetime64 UTC, kwh is float64), test multi-file concatenation and sort order
  - [x] 9.2 `tests/test_meter_registry.py`: test column validation raises on missing column, test O(1) lookup returns correct record, test sanctioned_kva parsed as float and connection_date as date
  - [x] 9.3 `tests/test_validator.py`: test kwh<0 logged as NEGATIVE_KWH, test non-monotonic timestamp logged, test voltage OOB logged but row not dropped, test power_factor OOB logged, test all violations logged in single pass
  - [x] 9.4 `tests/test_gap_handler.py`: test short gap (1–3 slots) → imputed with 7-day same-slot median, test extended gap (>3 slots) → hardware_issue_flags.csv written and slots remain NaN, test no gap → DataFrame unchanged
  - [x] 9.5 `tests/test_peer_graph.py`: test meters within 200m are neighbors, test meters >200m apart are not neighbors, test isolated meter has empty list, test output is valid JSON, test idempotency (re-run produces identical output)



## Phase 3 — Feature Engineering & Clustering

- [x] 10. Implement `pipeline/features/baseline.py`
  - [x] 10.1 Implement `compute_baseline(df: pd.DataFrame, output_path: str = "data/processed/baseline_lookup.parquet") -> pd.DataFrame`: add hour_of_day and day_of_week columns from timestamp
  - [x] 10.2 For each (meter_id, hour_of_day, day_of_week) group, compute rolling 28-day median using only data strictly preceding each target timestamp (no leakage); use `.rolling(window=28, min_periods=1).median()` on same-slot values
  - [x] 10.3 Write result to baseline_lookup.parquet with columns (meter_id, hour_of_day, day_of_week, baseline_kwh); return DataFrame; idempotent on re-run

- [x] 11. Implement `pipeline/features/deviations.py`
  - [x] 11.1 Implement `compute_deviations(df: pd.DataFrame, baseline_df: pd.DataFrame, peer_graph: dict) -> pd.DataFrame`: join readings with baseline on (meter_id, hour_of_day, day_of_week)
  - [x] 11.2 Compute pct_deviation_from_baseline = (kwh - baseline_kwh) / baseline_kwh * 100; handle baseline_kwh == 0 by setting result to NaN
  - [x] 11.3 Compute z_score = (kwh - rolling_28d_mean) / rolling_28d_std per meter using rolling(window=2688, min_periods=672) (28 days × 96 slots)
  - [x] 11.4 Compute peer_deviation_score: for each (meter_id, timestamp), load neighbor list from peer_graph, compute median kwh of all neighbors at same timestamp, compute (kwh - neighbor_median) / neighbor_median * 100; alias as pct_deviation_from_peer_median
  - [x] 11.5 Set peer_deviation_flag = True when pct_deviation_from_baseline <= -80 AND at least ceil(0.75 * len(neighbors)) neighbors (min 6 of 8) have kwh >= their baseline_kwh * 0.95

- [x] 12. Implement `pipeline/features/temporal_lags.py`
  - [x] 12.1 Implement `compute_lag_features(df: pd.DataFrame) -> pd.DataFrame`: sort by (meter_id, timestamp), group by meter_id
  - [x] 12.2 Compute lag features using .shift() within each group: lag_1h (4 slots), lag_24h (96 slots), lag_48h (192 slots), lag_7d (672 slots)
  - [x] 12.3 Compute rolling_7d_mean and rolling_7d_std using .rolling(window=672, min_periods=96) within each group
  - [x] 12.4 Compute trend_slope_3d: for each row, collect same (hour_of_day, day_of_week) slot values from preceding 3 days (3 data points), fit np.polyfit(x=[0,1,2], y=values, deg=1), store slope; use NaN when fewer than 2 points available
  - [x] 12.5 Populate all lag fields with NaN when insufficient history exists (do not raise); return DataFrame with added columns

- [x] 13. Implement `pipeline/features/pattern_fingerprint.py`
  - [x] 13.1 Implement `compute_pattern_fingerprints(df: pd.DataFrame) -> pd.DataFrame`: compute daily mean kwh per meter per day
  - [x] 13.2 Compute is_sustained_multiday_drop: set daily_drop=True when day's mean kwh is >= 50% below day's mean baseline; set is_sustained_multiday_drop=True for all rows in a day that is part of a run of >= 3 consecutive daily_drop=True days
  - [x] 13.3 Compute night_activity_score: mean kwh where hour in [22,23,0,1,2,3,4] divided by overall mean kwh per meter; handle overall_mean == 0 by setting to NaN
  - [x] 13.4 Compute is_recurring_daily_pattern: for each meter and day D, check if a consumption dip (kwh < 50% of baseline) occurs at consistent hours (same ±1 hour window) on >= 3 of the 5 days [D-4, D-3, D-2, D-1, D]; set True for all rows in day D if condition holds
  - [x] 13.5 Apply vacation/bypass disambiguation: if is_sustained_multiday_drop=True AND night_activity_score < 0.2, override is_recurring_daily_pattern=False for that period (vacation pattern, not bypass)

- [x] 14. Implement `pipeline/features/zone_profiles.py`
  - [x] 14.1 Implement `compute_zone_profiles(df: pd.DataFrame, registry_df: pd.DataFrame, output_path: str = "data/processed/zone_profiles.parquet") -> pd.DataFrame`: join readings with registry on meter_id to get feeder_id and transformer_id
  - [x] 14.2 Group by (feeder_id, timestamp), sum kwh -> total_load_kwh; join with transformer capacity (sum of sanctioned_kva for all meters on that feeder's transformer)
  - [x] 14.3 Compute feeder_stress = total_load_kwh / transformer_rated_kva; set is_high_stress_zone = feeder_stress > 0.90
  - [x] 14.4 Compute pct_deviation_from_cluster_norm per meter: join cluster assignments, compute cluster median load at each timestamp, then (kwh - cluster_median) / cluster_median * 100
  - [x] 14.5 Write to zone_profiles.parquet with columns (feeder_id, timestamp, total_load_kwh, feeder_stress, is_high_stress_zone, transformer_rated_kva); return DataFrame

- [x] 15. Implement `pipeline/clustering/seasonal.py`
  - [x] 15.1 Implement `fit_seasonal_clusters(df: pd.DataFrame, n_clusters: int = 8, output_dir: str = "data/processed") -> pd.DataFrame`: for each meter, build month x hour load shape vector (12 x 24 = 288 dimensions) by computing mean kwh per (month, hour_of_day) combination
  - [x] 15.2 Normalize each vector by dividing by meter's overall mean kwh; stack all 50 meter vectors into matrix of shape (50, 288)
  - [x] 15.3 Fit KMeans(n_clusters=8, random_state=42, n_init=10) on the matrix; assign cluster_id (0-7) to each meter
  - [x] 15.4 Write cluster_assignments.csv with columns (meter_id, cluster_id); compute cluster centroid profiles and serialize as {cluster_id: {month: {hour: mean_kwh}}} to seasonal_profiles.json
  - [x] 15.5 Return cluster assignments DataFrame; idempotent on re-run

- [x] 16. Implement `pipeline/features/build_matrix.py`
  - [x] 16.1 Implement `build_feature_matrix(data_dir: str = "data/processed", output_path: str = "data/processed/feature_matrix.parquet") -> pd.DataFrame`: check all required source files exist (baseline_lookup.parquet, zone_profiles.parquet, cluster_assignments.csv, peer_graph.json); raise FileNotFoundError with path if any missing
  - [x] 16.2 Start with imputed readings as base; left-join baseline_lookup on (meter_id, hour_of_day, day_of_week); merge deviation columns, lag columns, fingerprint columns
  - [x] 16.3 Left-join cluster_assignments on meter_id; left-join zone_profiles on (feeder_id, timestamp) after joining feeder_id from registry
  - [x] 16.4 Add confirmed_tamper column: read injected_events.json, set confirmed_tamper=1 for tamper meters during their tamper period, 0 otherwise
  - [x] 16.5 Write final matrix to feature_matrix.parquet using pyarrow engine; idempotent on re-run; return DataFrame

- [x] 17. Write pytest unit tests for Phase 3 modules
  - [x] 17.1 `tests/test_pattern_fingerprint.py`: test vacation pattern (sustained multiday drop + night_activity_score < 0.2) does NOT trigger is_recurring_daily_pattern=True
  - [x] 17.2 Test bypass pattern (recurring hourly dip + non-zero night_activity_score) DOES trigger is_recurring_daily_pattern=True
  - [x] 17.3 Test is_sustained_multiday_drop=True only when >= 3 consecutive days of >= 50% below baseline
  - [x] 17.4 Test night_activity_score computed correctly as mean(22:00-05:00 kwh) / overall_mean


## Phase 4 — ML Models & Truth Engine

- [x] 18. Implement `models/load_forecast/train.py`
  - [x] 18.1 Implement `train_load_forecast_models(data_dir: str = "data/processed", force: bool = False) -> dict`: load feature_matrix.parquet; define LOAD_FORECAST_FEATURES list (rolling_7d_mean, rolling_7d_std, trend_slope_3d, lag_24h, lag_48h, lag_7d, feeder_stress, pct_deviation_from_cluster_norm, hour_of_day, day_of_week)
  - [x] 18.2 For each cluster_id (0-7): filter feature_matrix to meters in that cluster; set X = cluster_df[LOAD_FORECAST_FEATURES], y = is_high_stress_zone.astype(int)
  - [x] 18.3 Compute scale_pos_weight = neg_count / pos_count (handle pos_count=0 by defaulting to 1.0); use TimeSeriesSplit(n_splits=5); apply SMOTE on training split only (never on val/test)
  - [x] 18.4 Train XGBClassifier(n_estimators=200, max_depth=6, learning_rate=0.05, scale_pos_weight=scale_pos_weight, eval_metric="logloss", random_state=42) with early_stopping_rounds=20 on val set
  - [x] 18.5 Save final model to models/load_forecast/xgb_{cluster_id}_v1_{YYYYMMDD}.joblib; return dict of {cluster_id: model_path}

- [x] 19. Implement `models/truth_engine/train.py`
  - [x] 19.1 Implement `train_truth_engine(data_dir: str = "data/processed", force: bool = False) -> str`: load feature_matrix.parquet; define TRUTH_ENGINE_FEATURES list (all 15 features: z_score, peer_deviation_score, is_sustained_multiday_drop, is_recurring_daily_pattern, night_activity_score, pct_deviation_from_baseline, pct_deviation_from_peer_median, pct_deviation_from_cluster_norm, lag_1h, lag_24h, lag_48h, lag_7d, rolling_7d_mean, rolling_7d_std, trend_slope_3d)
  - [x] 19.2 Cast bool columns to int8; fill NaN with column median computed on training split only; set X = feature_matrix[TRUTH_ENGINE_FEATURES], y = confirmed_tamper.astype(int)
  - [x] 19.3 Use TimeSeriesSplit(n_splits=5); for each fold train LGBMClassifier(n_estimators=300, max_depth=6, learning_rate=0.05, num_leaves=31, class_weight="balanced", random_state=42) with early_stopping(30)
  - [x] 19.4 Track best fold by AUC on validation set; save best model to models/truth_engine/lgbm_v1_{YYYYMMDD}.joblib; return model path

- [x] 20. Implement `models/truth_engine/scorer.py`
  - [x] 20.1 Implement `score_and_gate(feature_matrix: pd.DataFrame, lgbm_model, db_session) -> dict`: get one row per meter (most recent reading); compute anomaly_confidence = lgbm_model.predict_proba(X)[:, 1] for all candidates
  - [x] 20.2 Implement `count_anomaly_days_in_last_5(meter_id, db_session) -> int`: query alert_events for distinct days in last 5 calendar days where meter had an alert
  - [x] 20.3 Implement `count_consecutive_anomaly_days(meter_id, db_session) -> int`: query alert_events for most recent streak of consecutive days with alerts
  - [x] 20.4 Apply routing logic: score >= 0.90 AND (consecutive_days >= 2 OR repeat_days >= 3) → write to alert_events with state=NEW; score >= 0.90 AND neither condition → write to alert_events with state=WATCHING; score < 0.90 → write to shadow_events only
  - [x] 20.5 Implement `build_alert_object(row, score) -> dict`: populate all canonical Alert Object Schema fields including alert_id (UUID v4), triggered_at (ISO8601 UTC), feeder_id (from registry), peer_status_summary (count normal/elevated/anomalous neighbors)
  - [x] 20.6 Implement alert deduplication: before writing, check if alert for same (meter_id, DATE(triggered_at)) already exists in alert_events; if so, update existing record rather than inserting duplicate

- [x] 21. Implement `models/truth_engine/shap_explainer.py`
  - [x] 21.1 Implement `explain_alert(alert_features: dict, lgbm_model) -> list[dict]`: create shap.TreeExplainer(lgbm_model); compute shap_values for alert's feature vector; handle LightGBM list output (use shap_values[1][0] for class=1)
  - [x] 21.2 Rank features by abs(shap_value) descending; extract top-3 (feature_name, shap_val, feature_val) tuples
  - [x] 21.3 Implement `generate_plain_english(feature, value, ctx) -> str` with templates for all 9 features: pct_deviation_from_baseline, peer_deviation_score, night_activity_score, z_score, is_recurring_daily_pattern, pct_deviation_from_peer_median, pct_deviation_from_cluster_norm, is_sustained_multiday_drop, trend_slope_3d
  - [x] 21.4 Return list of 3 dicts [{feature, value, plain_english}] sorted by abs(shap_value) descending

- [x] 22. Implement `models/eval/evaluate.py`
  - [x] 22.1 Implement `evaluate_models(data_dir: str = "data/processed", force: bool = False) -> dict`: load feature_matrix.parquet; use last 20% of data (by timestamp) as temporal test split — no shuffling
  - [x] 22.2 Load latest lgbm_v1_*.joblib and all xgb_{cluster_id}_v1_*.joblib models; compute precision, recall, F1, AUC for Truth Engine on test split
  - [x] 22.3 Compute precision, recall, F1, AUC for Load Forecast models (aggregate across all clusters) on test split
  - [x] 22.4 Write eval_report.json to models/eval/ with structure {truth_engine: {precision, recall, f1, auc}, load_forecast: {precision, recall, f1, auc}, evaluated_at: ISO8601}
  - [x] 22.5 Save confusion matrix plot to models/eval/confusion_matrix.png using matplotlib; return eval metrics dict

- [x] 23. Implement `models/inference_runner.py`
  - [x] 23.1 Implement `run_inference_and_write_alerts(data_dir: str = "data/processed", force: bool = False) -> dict`: load feature_matrix.parquet; load latest lgbm_v1_*.joblib and xgb_{cluster_id}_v1_*.joblib models
  - [x] 23.2 Call score_and_gate() to score all meters and write alert_events / shadow_events rows; call explain_alert() for each alert written to alert_events and update shap_top3 field
  - [x] 23.3 Update feeder_status table: for each feeder, compute current_utilization_pct from latest zone_profiles data; use XGBoost models to generate 24h forecast as list of {timestamp, predicted_utilization_pct}; compute stress_level (GREEN/AMBER/RED)
  - [x] 23.4 Implement `get_pipeline_summary() -> dict`: return {meters_processed, alerts_new, alerts_watching, shadow_records} by querying alert_events and shadow_events tables

- [x] 24. Write pytest unit tests for Phase 4 modules
  - [x] 24.1 `tests/test_alert_gating.py`: test score=0.89 → written to shadow_events only, NOT in alert_events
  - [x] 24.2 Test score=0.91 + repeat_days >= 2 (consecutive) → written to alert_events with state=NEW and all canonical schema fields present
  - [x] 24.3 Test score=0.91 + repeat_days=0 (no repeat) → written to alert_events with state=WATCHING
  - [x] 24.4 Test alert deduplication: running scorer twice on same data does not create duplicate alert rows


## Phase 5 — FastAPI Backend

- [x] 25. Implement `api/database.py`
  - [x] 25.1 Define SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/synapse_grid.db")
  - [x] 25.2 Create async engine with create_async_engine(url, connect_args={"check_same_thread": False}); create AsyncSessionLocal via async_sessionmaker
  - [x] 25.3 Define Base = declarative_base(); implement async init_db() that calls Base.metadata.create_all(engine) via engine.begin(); implement get_db() dependency that yields AsyncSession per request

- [x] 26. Implement `api/models.py` — SQLAlchemy ORM models
  - [x] 26.1 Define AlertEvent model (table: alert_events) with all canonical schema columns: alert_id (String PK, UUID default), meter_id, alert_type, state (default "NEW"), anomaly_confidence, pattern_type, triggered_at, pct_deviation_from_baseline, pct_deviation_from_peer_median, pct_deviation_from_cluster_norm, z_score, shap_top3 (Text/JSON), peer_status_summary (Text/JSON), repeat_days_count, dispatch_action, dismiss_reason, resolved_at, resolver_id, feeder_id (indexed)
  - [x] 26.2 Define ShadowEvent model (table: shadow_events) with identical column definitions as AlertEvent
  - [x] 26.3 Define DispatchAuditLog model (table: dispatch_audit_log): id (Integer PK autoincrement), alert_id (FK to alert_events), action, reason_code, resolver_id, resolved_at
  - [x] 26.4 Define MeterReading model (table: meter_readings): id (Integer PK autoincrement), meter_id (indexed), timestamp, kwh, voltage, power_factor, reactive_power; composite index on (meter_id, timestamp)
  - [x] 26.5 Define MeterRegistryCache model (table: meter_registry_cache): meter_id (String PK), lat, lng, feeder_id (indexed), transformer_id, zone, consumer_category, sanctioned_kva, connection_date
  - [x] 26.6 Define FeederStatus model (table: feeder_status): feeder_id (String PK), current_utilization_pct, stress_level, transformer_rated_kva, forecast_24h (Text/JSON), updated_at

- [x] 27. Implement `api/main.py`
  - [x] 27.1 Create FastAPI app with title="Synapse-Grid API", version="1.0.0"; add CORSMiddleware allowing origins=["http://localhost:5173"], all methods and headers
  - [x] 27.2 Add @app.on_event("startup") handler that calls await init_db()
  - [x] 27.3 Include alerts_router, meters_router, feeders_router all with prefix="/api/v1"; add GET /health endpoint returning {"status": "ok"}

- [x] 28. Implement `api/routers/alerts.py`
  - [x] 28.1 Define Pydantic schemas: AlertSummary (alert_id, meter_id, alert_type, state, anomaly_confidence, pattern_type, triggered_at, feeder_id), ShapEntry (feature, value, plain_english), AlertDetail (extends AlertSummary with all deviation fields, shap_top3 as List[ShapEntry], peer_status_summary as dict, repeat_days_count, dispatch_action, dismiss_reason, resolved_at, resolver_id), ActionRequest (action: Literal["DISPATCH_LINEMAN","LOAD_BALANCE","DISMISS"], reason_code: Optional[Literal[...]], resolver_id: str)
  - [x] 28.2 Implement GET /alerts: query alert_events with optional filters (state, alert_type, feeder_id via Query params), order by anomaly_confidence DESC; return List[AlertSummary]
  - [x] 28.3 Implement GET /alerts/{alert_id}: fetch single alert, raise 404 if not found; parse shap_top3 and peer_status_summary from JSON strings; return AlertDetail
  - [x] 28.4 Implement PATCH /alerts/{alert_id}/action: validate action=DISMISS requires non-empty reason_code (raise HTTP 422 if absent); check alert not already in DISPATCHED/DISMISSED state (raise HTTP 409 if terminal); apply state transition (DISPATCHED for DISPATCH_LINEMAN/LOAD_BALANCE, DISMISSED for DISMISS); write DispatchAuditLog row; commit; return updated AlertDetail

- [x] 29. Implement `api/routers/meters.py`
  - [x] 29.1 Define Pydantic schemas: ReadingPoint (timestamp, kwh, voltage, power_factor), BaselinePoint (hour_of_day, day_of_week, baseline_kwh), PeerStatus (meter_id, lat, lng, status: Literal["normal","elevated","anomalous"])
  - [x] 29.2 Implement GET /meters/{meter_id}/readings: query meter_readings for last 14 days (cutoff = now - 14 days), order by timestamp ASC; raise 404 if no readings found; return List[ReadingPoint]
  - [x] 29.3 Implement GET /meters/{meter_id}/baseline: read baseline_lookup.parquet, filter to meter_id; raise 503 if file not found (pipeline not run yet); raise 404 if meter not in baseline; return List[BaselinePoint]
  - [x] 29.4 Implement GET /meters/{meter_id}/peers: load peer_graph.json (raise 503 if missing); for each neighbor, get registry entry (lat/lng) and latest reading; classify status by comparing latest kwh to baseline (normal: within ±20%, elevated: >20% above, anomalous: >20% below); return List[PeerStatus]

- [x] 30. Implement `api/routers/feeders.py`
  - [x] 30.1 Define Pydantic schemas: ForecastPoint (timestamp, predicted_utilization_pct), FeederStatusResponse (feeder_id, current_utilization_pct, stress_level, transformer_rated_kva, forecast_24h: List[ForecastPoint])
  - [x] 30.2 Implement GET /feeders: query all feeder_status rows; recompute stress_level at serve time (<=70% GREEN, 70-90% AMBER, >90% RED); parse forecast_24h from JSON string; return List[FeederStatusResponse]


## Phase 6 — CLI Pipeline Orchestrator

- [x] 31. Implement `run_pipeline.py`
  - [x] 31.1 Add argparse with --data-dir (default "data/raw") and --force (store_true) arguments; print pipeline header with timestamp and config on start
  - [x] 31.2 Define 16-stage sequence list: (stage_name, stage_fn) pairs covering Synthetic Data Check, Ingest Readings, Validate, Impute Gaps, Build Peer Graph, Compute Baseline, Compute Deviations, Compute Lag Features, Pattern Fingerprints, Zone Profiles, Seasonal Clustering, Build Feature Matrix, Train Load Forecast, Train Truth Engine, Run Inference, Evaluate Models
  - [x] 31.3 Execute each stage in a try/except block: print "[stage] Starting...", call stage_fn(data_dir=args.data_dir, force=args.force), print "[stage] Done." or "[stage] Skipped (output up to date)." based on return value; on exception print "[stage] FAILED: {e}" to stderr and sys.exit(1)
  - [x] 31.4 Implement `_is_output_fresh(output_path, *input_paths) -> bool` helper: return True if output_path exists AND its mtime is newer than all input_paths; used by each stage for idempotency check
  - [x] 31.5 After all stages complete, call get_pipeline_summary() and print formatted summary: meters processed, alerts NEW, alerts WATCHING, shadow records, eval report path


## Phase 7 — React Frontend

- [x] 32. Implement `frontend/src/lib/api.ts`
  - [x] 32.1 Define all TypeScript interfaces matching the canonical Alert Object Schema: AlertSummary, ShapEntry, AlertDetail, ReadingPoint, BaselinePoint, PeerStatus, FeederStatusResponse, ForecastPoint, ActionRequest
  - [x] 32.2 Implement `apiFetch<T>(path, options?) -> Promise<T>` helper: fetch from /api/v1{path}, throw Error with detail message on non-ok response
  - [x] 32.3 Implement typed fetch functions: fetchAlerts(params?), fetchAlertDetail(alertId), submitAlertAction(alertId, body), fetchMeterReadings(meterId), fetchMeterBaseline(meterId), fetchMeterPeers(meterId), fetchFeeders()

- [x] 33. Implement `frontend/src/stores/alertStore.ts`
  - [x] 33.1 Define AlertStore interface with state fields: selectedAlertId (string|null), feederFilter (string|null), pendingAction (ActionType|null), pendingReasonCode (ReasonCode|null), showConfirmModal (bool)
  - [x] 33.2 Define action methods: selectAlert(id), clearSelection(), setFeederFilter(feederId), setPendingAction(action), setPendingReasonCode(code), openConfirmModal(), closeConfirmModal(), resetDispatch()
  - [x] 33.3 Implement store with create<AlertStore>(): selectAlert resets pendingAction/reasonCode/modal; setPendingAction clears reasonCode when action changes; resetDispatch clears all dispatch state

- [x] 34. Implement TanStack Query hooks in `frontend/src/hooks/`
  - [x] 34.1 `useAlertQueue.ts`: useQuery with queryKey=["alerts", feederFilter], queryFn=fetchAlerts({feeder_id}), refetchInterval=5*60*1000, staleTime=60*1000
  - [x] 34.2 `useAlertDetail.ts`: useQuery with queryKey=["alert", alertId], queryFn=fetchAlertDetail(alertId!), enabled=!!alertId, staleTime=30*1000
  - [x] 34.3 `useFeederStatus.ts`: useQuery with queryKey=["feeders"], queryFn=fetchFeeders, refetchInterval=5*60*1000, staleTime=60*1000
  - [x] 34.4 `useMeterReadings.ts`: export useMeterReadings(meterId), useMeterBaseline(meterId), useMeterPeers(meterId) — all with enabled=!!meterId and appropriate staleTime

- [x] 35. Implement `frontend/src/pages/Dashboard.tsx`
  - [x] 35.1 Implement 3-panel CSS Grid layout: grid-template-columns="25% 35% 40%", height=100vh, overflow hidden per panel
  - [x] 35.2 Left panel renders FeederStressMap; center panel renders AlertQueue; right panel renders AlertDetailPane (DispatchPanel + AlertDetail) only when selectedAlertId is set in Zustand store
  - [x] 35.3 Show skeleton loader in right panel while useAlertDetail is loading

- [x] 36. Implement `frontend/src/components/FeederStressMap.tsx`
  - [x] 36.1 Render react-leaflet MapContainer centered on Bangalore (12.97°N, 77.59°E), zoom=13; add OpenStreetMap TileLayer
  - [x] 36.2 For each feeder from useFeederStatus(), render CircleMarker at feeder centroid coordinates; color: GREEN=#22c55e, AMBER=#f59e0b, RED=#ef4444; radius=18px
  - [x] 36.3 Add Popup per CircleMarker showing feeder_id, utilization %, stress_level; onClick calls setFeederFilter(feeder_id) in Zustand store to filter AlertQueue

- [x] 37. Implement `frontend/src/components/AlertQueue.tsx`
  - [x] 37.1 Display header "Alert Queue" with count badge showing total alerts; add state filter dropdown (ALL | NEW | WATCHING | UNDER_REVIEW | DISPATCHED | DISMISSED)
  - [x] 37.2 Render sorted alert list from useAlertQueue(); each AlertRow shows: meter_id (bold) + feeder_id (muted text), confidence % badge (>=95% red, >=90% amber, else gray), pattern_type badge, state badge with color coding (NEW=blue, WATCHING=yellow, UNDER_REVIEW=purple, DISPATCHED=green, DISMISSED=gray)
  - [x] 37.3 onClick on AlertRow calls selectAlert(alert_id) in Zustand store

- [x] 38. Implement `frontend/src/components/AlertDetail.tsx`
  - [x] 38.1 Render header with meter_id, alert_type badge, triggered_at timestamp; fetch data via useAlertDetail(selectedAlertId), useMeterReadings(meter_id), useMeterBaseline(meter_id), useMeterPeers(meter_id)
  - [x] 38.2 Implement 14-day Recharts ComposedChart: merge readings + baseline + peer median into ChartDataPoint[] array; X-axis with one tick per day (interval=96), Y-axis in kWh; three Line series (Actual=blue solid, Baseline=gray dashed, Peer Median=orange dashed); ReferenceArea for triggered_at ± 24h with yellow fill
  - [x] 38.3 Render DeviationMetricsCard: three rows showing pct_deviation_from_baseline, pct_deviation_from_peer_median, pct_deviation_from_cluster_norm as percentages; color red if negative, green if positive
  - [x] 38.4 Render ShapExplanationCard: title "Why this alert?"; three rows each showing feature name + plain_english string from shap_top3; small bar showing relative SHAP magnitude
  - [x] 38.5 Render NeighborhoodMiniMap: small react-leaflet MapContainer (200px height); CircleMarker for alerted meter (red, larger radius=12); CircleMarker per peer colored by status (normal=green, elevated=amber, anomalous=red)

- [x] 39. Implement `frontend/src/components/DispatchPanel.tsx`
  - [x] 39.1 Render three action buttons: "Dispatch Lineman" (red), "Load Balance" (amber), "Dismiss" (gray); buttons disabled when alert is in DISPATCHED or DISMISSED state; selected button shows filled color, unselected shows light variant
  - [x] 39.2 Render ReasonCodeDropdown (shadcn/ui Select) visible only when pendingAction === "DISMISS"; options: VACATION, PLANNED_OUTAGE, FALSE_POSITIVE, OTHER; calls setPendingReasonCode on change
  - [x] 39.3 Render Submit button: disabled when no action selected OR (action=DISMISS AND no reason_code selected) OR mutation.isPending; onClick calls openConfirmModal()
  - [x] 39.4 Implement useMutation calling submitAlertAction(alertId, {action, reason_code, resolver_id: "dispatcher-001"}); onSuccess: call resetDispatch() and invalidate ["alerts"] and ["alert", alertId] query caches
  - [x] 39.5 Show ResolutionDisplay after successful submission: action taken + resolved_at timestamp + resolver_id; show terminal state message when alert is DISPATCHED or DISMISSED

- [x] 40. Implement `frontend/src/components/ConfirmationModal.tsx`
  - [x] 40.1 Implement shadcn/ui Dialog component accepting props: action, reasonCode, meterId, onConfirm, onCancel
  - [x] 40.2 Dialog title: "Confirm Action"; body shows "Confirm: {action} for meter {meterId}?" and if DISMISS shows "Reason: {reasonCode}"
  - [x] 40.3 Two buttons: "Confirm" (calls onConfirm) and "Cancel" (calls onCancel)


## Phase 8 — Documentation

- [x] 41. Write `README.md`
  - [x] 41.1 Write Prerequisites section: Python 3.11+, Node.js 18+, pip, npm
  - [x] 41.2 Write Setup section: git clone, cd synapse_grid, pip install -r requirements.txt, cd frontend && npm install
  - [x] 41.3 Write three one-command entry points with exact commands: (1) `python run_pipeline.py` — loads data, trains models, writes alerts; (2) `uvicorn api.main:app --reload` — starts API on port 8000; (3) `cd frontend && npm run dev` — starts dashboard on port 5173
  - [x] 41.4 Write expected output section showing sample pipeline summary output and API /docs URL

- [x] 42. Write `demo/scenarios.md`
  - [x] 42.1 Write Scenario 1 — Meter Theft Detection: step-by-step walkthrough navigating to METER_T001 alert in the queue, reading the SHAP explanation ("Consumption is 88% below this meter's 28-day baseline"), clicking Dispatch Lineman, confirming in modal, verifying alert moves to DISPATCHED state
  - [x] 42.2 Write Scenario 2 — Vacation vs Bypass Disambiguation: compare METER_V001 (vacation: sustained drop + near-zero night activity, is_recurring_daily_pattern=False, pattern_type=SUSTAINED_DROP) vs METER_T002 (bypass: recurring hourly dip + non-zero night activity, is_recurring_daily_pattern=True, pattern_type=RECURRING_DAILY_DIP); show how the SHAP explanation and night_activity_score distinguish them; demonstrate DISMISS with reason=VACATION for the vacation meter
  - [x] 42.3 Write Scenario 3 — 24-Hour Load Spike Prediction: navigate to Feeder Stress Map, identify F001 as RED zone, click to filter alert queue to F001 alerts, open LOAD_STRESS alert, read 24h forecast showing predicted utilization >90%, click Load Balance action

- [x] 43. Write `eval/ablation_notes.md`
  - [x] 43.1 Write Ablation Study 1 — Peer Comparison vs Baseline-Alone: describe experiment (remove peer_deviation_score and pct_deviation_from_peer_median from Truth Engine features, retrain, compare precision/recall/AUC); document expected finding (peer features improve precision by reducing false positives from meters with unusual but consistent personal patterns)
  - [x] 43.2 Write Ablation Study 2 — Per-Cluster Models vs Global Model: describe experiment (train single global XGBoost model on all meters vs 8 per-cluster models, compare AUC on test split); document expected finding (per-cluster models outperform global on feeders with distinct seasonal profiles, e.g. industrial vs residential)
