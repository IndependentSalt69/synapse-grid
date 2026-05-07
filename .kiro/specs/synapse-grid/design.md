# Technical Design Document — Synapse-Grid


> **Hackathon Prototype** — No APScheduler, no Alembic, no JWT auth, no React Testing Library tests. Manual CLI trigger only. SQLite + SQLAlchemy `create_all()`. CSV input only.

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Project Directory Structure](#2-project-directory-structure)
3. [Data Pipeline Design](#3-data-pipeline-design)
4. [ML Model Design](#4-ml-model-design)
5. [Database Schema](#5-database-schema)
6. [FastAPI Backend Design](#6-fastapi-backend-design)
7. [React Frontend Design](#7-react-frontend-design)
8. [run_pipeline.py Orchestration Design](#8-run_pipelinepy-orchestration-design)
9. [Synthetic Data Generation Design](#9-synthetic-data-generation-design)

---

## 1. System Architecture Overview

### 1.1 End-to-End Data Flow

```
CSV Files (data/raw/)
        │
        ▼
run_pipeline.py  ──────────────────────────────────────────────────────────────
        │                                                                       │
        │  Stage 1: Ingest & Validate                                           │
        │    meter_reader.py  →  DataFrame (50 meters × 432,000 rows)          │
        │    meter_registry.py → lookup dict (50 entries)                      │
        │    validator.py     → data_quality_log.db                            │
        │                                                                       │
        │  Stage 2: Impute                                                      │
        │    gap_handler.py   → clean DataFrame + hardware_issue_flags.csv     │
        │                                                                       │
        │  Stage 3: Graph & Features                                            │
        │    builder.py       → peer_graph.json                                │
        │    baseline.py      → baseline_lookup.parquet                        │
        │    deviations.py    → deviation columns                              │
        │    temporal_lags.py → lag columns                                    │
        │    pattern_fingerprint.py → fingerprint columns                      │
        │    zone_profiles.py → zone_profiles.parquet                          │
        │    build_matrix.py  → feature_matrix.parquet                         │
        │                                                                       │
        │  Stage 4: Cluster & Train                                             │
        │    seasonal.py      → cluster_assignments.csv                        │
        │    load_forecast/train.py → xgb_{cluster_id}_v1_{date}.joblib        │
        │    truth_engine/train.py  → lgbm_v1_{date}.joblib                    │
        │                                                                       │
        │  Stage 5: Inference & Write                                           │
        │    inference_runner.py → alert_events table (NEW/WATCHING)           │
        │                       → shadow_events table (sub-threshold)          │
        │    shap_explainer.py  → shap_top3 JSON written to alert row          │
        │                                                                       │
        │  Stage 6: Evaluate                                                    │
        │    evaluate.py      → eval_report.json + confusion_matrix.png        │
        └───────────────────────────────────────────────────────────────────────

SQLite Databases (data/)
  ├── synapse_grid.db          ← alert_events, shadow_events, dispatch_audit_log,
  │                               meter_readings, meter_registry_cache, feeder_status
  └── data_quality_log.db      ← quality violation records

FastAPI (api/)
  ├── GET  /api/v1/alerts                  ← routers/alerts.py
  ├── GET  /api/v1/alerts/{id}
  ├── PATCH /api/v1/alerts/{id}/action
  ├── GET  /api/v1/meters/{id}/readings    ← routers/meters.py
  ├── GET  /api/v1/meters/{id}/baseline
  ├── GET  /api/v1/meters/{id}/peers
  └── GET  /api/v1/feeders                 ← routers/feeders.py

React Dashboard (frontend/src/)
  ├── FeederStressMap   ← react-leaflet, polls /api/v1/feeders every 5 min
  ├── AlertQueue        ← polls /api/v1/alerts every 5 min
  └── AlertDetail + DispatchPanel  ← loads on alert select, PATCH on action
```

### 1.2 Component Diagram (Mermaid)

```mermaid
graph LR
    %% ── Layer 1: Input ──────────────────────────────────────────
    subgraph Input["📂 Input (data/raw/)"]
        direction TB
        CSV1[sample_readings.csv]
        CSV2[sample_registry.csv]
        JSON1[injected_events.json]
    end

    %% ── Layer 2: Pipeline ───────────────────────────────────────
    subgraph Pipeline["⚙️ run_pipeline.py"]
        direction TB
        P1[Ingest]
        P2[Validate]
        P3[Impute Gaps]
        P4[Peer Graph]
        P5[Features]
        P6[Cluster k=8]
        P7[Feature Matrix]
        P8[Train Models]
        P9[Score + SHAP]
        P10[Evaluate]
        P1 --> P2 --> P3 --> P4 --> P5 --> P6 --> P7 --> P8 --> P9 --> P10
    end

    %% ── Layer 3: Storage ────────────────────────────────────────
    subgraph DB["🗄️ SQLite (data/)"]
        direction TB
        DB1[(synapse_grid.db)]
        DB2[(data_quality_log.db)]
    end

    %% ── Layer 4: API ────────────────────────────────────────────
    subgraph API["🔌 FastAPI :8000"]
        direction TB
        R1[/alerts]
        R2[/meters]
        R3[/feeders]
    end

    %% ── Layer 5: Frontend ───────────────────────────────────────
    subgraph Frontend["🖥️ React + Vite :5173"]
        direction TB
        F1[FeederStressMap]
        F2[AlertQueue]
        F3[AlertDetail + SHAP]
        F4[DispatchPanel]
        F5[ConfirmationModal]
    end

    %% ── Data flow ───────────────────────────────────────────────
    Input  --> Pipeline
    P9     --> DB1
    P2     --> DB2
    DB1    --> API
    API    --> Frontend
    F4     -->|PATCH /action| R1
```

### 1.3 Three Entry Points

| Command | Purpose | Port |
|---|---|---|
| `python run_pipeline.py` | Full pipeline: ingest → validate → impute → features → train → score → evaluate. Writes all SQLite tables. Run once before starting the API. | — |
| `uvicorn api.main:app --reload` | Starts the FastAPI REST server. Calls `create_all()` on startup. Serves all `/api/v1/` endpoints. | 8000 |
| `npm run dev` (inside `frontend/`) | Starts the Vite dev server. Proxies `/api/` to `localhost:8000`. | 5173 |

All three commands are independent. The pipeline must complete before the API serves meaningful data. The API must be running before the frontend is useful.

---

## 2. Project Directory Structure

```
synapse_grid/
├── run_pipeline.py                        # Single CLI entry point for full pipeline
├── requirements.txt                       # All Python dependencies, pinned versions
├── data/
│   ├── raw/
│   │   ├── sample_readings.csv            # 432,000 rows: meter_id,timestamp,kwh,voltage,power_factor,reactive_power
│   │   ├── sample_registry.csv            # 50 rows: meter_id,lat,lng,feeder_id,transformer_id,zone,consumer_category,sanctioned_kva,connection_date
│   │   └── injected_events.json           # Ground-truth labels for tamper/stress/vacation/gap meters
│   └── processed/
│       ├── peer_graph.json                # {meter_id: [neighbor_ids]} adjacency dict
│       ├── hardware_issue_flags.csv       # meter_id,gap_start,gap_end,gap_length_slots
│       ├── baseline_lookup.parquet        # meter_id,hour_of_day,day_of_week,baseline_kwh
│       ├── cluster_assignments.csv        # meter_id,cluster_id
│       ├── seasonal_profiles.json         # {cluster_id: {month: {hour: mean_kwh}}}
│       ├── zone_profiles.parquet          # feeder_id,timestamp,total_load_kwh,feeder_stress,is_high_stress_zone
│       └── feature_matrix.parquet         # All features joined: meter_id,timestamp + 15 feature columns
├── pipeline/
│   ├── __init__.py
│   ├── ingest/
│   │   ├── __init__.py
│   │   ├── meter_reader.py                # Parse readings CSV → typed DataFrame
│   │   ├── meter_registry.py              # Parse registry CSV → O(1) lookup dict
│   │   └── validator.py                   # Domain rule checks → data_quality_log.db
│   ├── peer_graph/
│   │   ├── __init__.py
│   │   └── builder.py                     # Haversine 200m radius → peer_graph.json
│   ├── impute/
│   │   ├── __init__.py
│   │   └── gap_handler.py                 # Short gap median impute; extended gap → hardware flag
│   └── features/
│       ├── __init__.py
│       ├── baseline.py                    # Rolling 28-day median per meter×hour×dow slot
│       ├── deviations.py                  # pct_deviation, z_score, peer_deviation_score
│       ├── temporal_lags.py               # lag_1h, lag_24h, lag_48h, lag_7d, rolling stats, trend slope
│       ├── pattern_fingerprint.py         # is_sustained_multiday_drop, is_recurring_daily_pattern, night_activity_score
│       ├── zone_profiles.py               # Per-feeder load aggregation and stress ratio
│       └── build_matrix.py                # Join all feature sources → feature_matrix.parquet
├── pipeline/clustering/
│   ├── __init__.py
│   └── seasonal.py                        # KMeans k=8 on month×hour vectors → cluster_assignments.csv
├── models/
│   ├── load_forecast/
│   │   └── train.py                       # XGBoost per cluster, TimeSeriesSplit, SMOTE on train only
│   ├── truth_engine/
│   │   ├── train.py                       # LightGBM classifier, TimeSeriesSplit
│   │   ├── scorer.py                      # Confidence gate + repeat-pattern gate → alert_events / shadow_events
│   │   └── shap_explainer.py              # TreeExplainer top-3 → plain_english strings
│   ├── eval/
│   │   └── evaluate.py                    # Precision/recall/F1/AUC + confusion matrix PNG
│   └── inference_runner.py                # Orchestrates scorer + explainer for all candidate meters
├── api/
│   ├── __init__.py
│   ├── main.py                            # FastAPI app, startup init_db(), CORS, router includes
│   ├── database.py                        # Async engine, session factory, Base, init_db()
│   ├── models.py                          # SQLAlchemy ORM models for all tables
│   └── routers/
│       ├── __init__.py
│       ├── alerts.py                      # GET /alerts, GET /alerts/{id}, PATCH /alerts/{id}/action
│       ├── meters.py                      # GET /meters/{id}/readings, /baseline, /peers
│       └── feeders.py                     # GET /feeders
├── scripts/
│   └── generate_synthetic_data.py         # Generates sample_readings.csv, sample_registry.csv, injected_events.json
├── tests/
│   ├── test_meter_reader.py               # Schema validation, typed output, multi-file concat
│   ├── test_meter_registry.py             # Column validation, O(1) lookup, type parsing
│   ├── test_validator.py                  # kwh<0, non-monotonic ts, voltage OOB, power_factor OOB
│   ├── test_gap_handler.py                # Short gap → imputed, extended gap → hardware flag, no gap → unchanged
│   ├── test_peer_graph.py                 # 200m radius, empty list for isolated meters, idempotency
│   ├── test_pattern_fingerprint.py        # Vacation vs bypass disambiguation logic
│   └── test_alert_gating.py              # score=0.89 → shadow only; score=0.91+repeat → NEW; score=0.91 no repeat → WATCHING
├── frontend/
│   ├── package.json
│   ├── vite.config.ts                     # Proxy /api → localhost:8000
│   ├── tailwind.config.ts
│   ├── src/
│   │   ├── main.tsx                       # React root, QueryClientProvider, App
│   │   ├── App.tsx                        # Router (single route: Dashboard)
│   │   ├── pages/
│   │   │   └── Dashboard.tsx              # 3-panel CSS Grid layout
│   │   ├── components/
│   │   │   ├── FeederStressMap.tsx        # react-leaflet map with CircleMarkers
│   │   │   ├── AlertQueue.tsx             # Sorted alert list with state badges
│   │   │   ├── AlertDetail.tsx            # 14-day chart + deviation card + SHAP card + mini-map
│   │   │   ├── DispatchPanel.tsx          # Three action buttons + reason code dropdown
│   │   │   └── ConfirmationModal.tsx      # shadcn/ui Dialog with action summary
│   │   ├── hooks/
│   │   │   ├── useAlertQueue.ts           # TanStack Query, 5-min refetch
│   │   │   ├── useAlertDetail.ts          # TanStack Query, enabled when alertId set
│   │   │   ├── useFeederStatus.ts         # TanStack Query, 5-min refetch
│   │   │   └── useMeterReadings.ts        # TanStack Query, enabled when meterId set
│   │   ├── stores/
│   │   │   └── alertStore.ts              # Zustand: selectedAlertId, pendingAction, modal state
│   │   └── lib/
│   │       └── api.ts                     # Typed fetch wrappers for all API endpoints
├── demo/
│   └── scenarios.md                       # Step-by-step demo script for hackathon judges
└── eval/
    └── ablation_notes.md                  # Notes on feature ablation experiments
```

---

## 3. Data Pipeline Design

### 3.1 `pipeline/ingest/meter_reader.py`

**Purpose:** Parse one or more CSV files of 15-minute interval smart meter readings into a single validated, typed DataFrame.

**Input:** One or more CSV file paths (passed as a list). Each CSV must contain the six required columns.

**Output:** `pd.DataFrame` with schema defined below. Returned in memory; not persisted to disk by this module.

**Key Logic:**
1. For each file path in the input list, call `pd.read_csv(path)`.
2. Check that all six required columns are present: `meter_id`, `timestamp`, `kwh`, `voltage`, `power_factor`, `reactive_power`. If any column is missing, raise `ValueError(f"Missing required column: {col}")`.
3. Cast types: `meter_id` → `str`, `timestamp` → `pd.Timestamp` (UTC-aware via `pd.to_datetime(..., utc=True)`), `kwh` / `voltage` / `power_factor` / `reactive_power` → `float64`.
4. Concatenate all per-file DataFrames with `pd.concat(..., ignore_index=True)`.
5. Sort by `(meter_id, timestamp)` ascending.
6. Return the combined DataFrame.

**Output Data Contract:**

| Column | Type | Notes |
|---|---|---|
| `meter_id` | `str` | Unique meter identifier |
| `timestamp` | `datetime64[ns, UTC]` | 15-min interval, UTC |
| `kwh` | `float64` | Energy consumption in kWh |
| `voltage` | `float64` | RMS voltage in Volts |
| `power_factor` | `float64` | Dimensionless, 0–1 |
| `reactive_power` | `float64` | kVAR |

---

### 3.2 `pipeline/ingest/meter_registry.py`

**Purpose:** Load the meter registry CSV and expose an O(1) lookup interface for meter metadata.

**Input:** Single CSV file path (`data/raw/sample_registry.csv`).

**Output:** Python `dict` mapping `meter_id → dict` of all metadata fields. Also returns a `pd.DataFrame` for bulk operations.

**Key Logic:**
1. Call `pd.read_csv(path)`.
2. Validate all nine required columns are present: `meter_id`, `lat`, `lng`, `feeder_id`, `transformer_id`, `zone`, `consumer_category`, `sanctioned_kva`, `connection_date`. Raise `ValueError` on any missing column.
3. Cast types: `sanctioned_kva` → `float64` (must be > 0; raise on non-positive), `connection_date` → `datetime.date` via `pd.to_datetime(...).dt.date`, `lat`/`lng` → `float64`.
4. Build lookup dict: `registry = df.set_index("meter_id").to_dict(orient="index")`.
5. Return `(df, registry)` tuple.

**Output Data Contract:**

| Column | Type | Notes |
|---|---|---|
| `meter_id` | `str` | Primary key |
| `lat` | `float64` | Decimal degrees, WGS84 |
| `lng` | `float64` | Decimal degrees, WGS84 |
| `feeder_id` | `str` | e.g. `F001`–`F005` |
| `transformer_id` | `str` | e.g. `T001`–`T002` |
| `zone` | `str` | Administrative zone name |
| `consumer_category` | `str` | e.g. `RESIDENTIAL`, `COMMERCIAL` |
| `sanctioned_kva` | `float64` | Positive, e.g. 25.0, 50.0, 63.0, 100.0 |
| `connection_date` | `datetime.date` | Date of meter installation |

---

### 3.3 `pipeline/ingest/validator.py`

**Purpose:** Apply domain-rule validation to all readings and log violations to `data_quality_log.db`. Does not drop records — flags only.

**Input:** `pd.DataFrame` from `meter_reader.py` (all readings).

**Output:** Same DataFrame (unchanged). Side effect: violation records written to `data/processed/data_quality_log.db`, table `quality_violations`.

**Key Logic:**
1. Open SQLite connection to `data/processed/data_quality_log.db`. Create table `quality_violations` if not exists with columns: `id INTEGER PRIMARY KEY AUTOINCREMENT`, `meter_id TEXT`, `timestamp TEXT`, `violation_type TEXT`, `field_name TEXT`, `observed_value REAL`.
2. **Rule 1 — Negative kWh:** For each row where `kwh < 0`, log `violation_type="NEGATIVE_KWH"`, `field_name="kwh"`, `observed_value=kwh`.
3. **Rule 2 — Non-monotonic timestamp:** Group by `meter_id`. Within each group (sorted by timestamp), flag any row where `timestamp <= previous_timestamp` for that meter. Log `violation_type="NON_MONOTONIC_TIMESTAMP"`, `field_name="timestamp"`.
4. **Rule 3 — Voltage out of range:** For each row where `voltage < 180` or `voltage > 260`, log `violation_type="VOLTAGE_OUT_OF_RANGE"`, `field_name="voltage"`, `observed_value=voltage`. Do NOT drop the row.
5. **Rule 4 — Power factor out of range:** For each row where `power_factor < 0` or `power_factor > 1`, log `violation_type="POWER_FACTOR_OUT_OF_RANGE"`, `field_name="power_factor"`, `observed_value=power_factor`.
6. Process all rows in a single pass (do not halt on first violation). Commit all violations in one transaction.
7. Return the original DataFrame unmodified.

**Output Data Contract (quality_violations table):**

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` | Auto-increment PK |
| `meter_id` | `TEXT` | |
| `timestamp` | `TEXT` | ISO8601 |
| `violation_type` | `TEXT` | One of the four rule codes above |
| `field_name` | `TEXT` | Column that violated the rule |
| `observed_value` | `REAL` | The actual value that triggered the flag |

---

### 3.4 `pipeline/impute/gap_handler.py`

**Purpose:** Detect and handle missing readings per meter. Short gaps are imputed; extended gaps are flagged as hardware issues.

**Input:** `pd.DataFrame` from validator (may contain `NaN` in `kwh` column for missing slots).

**Output:**
- `pd.DataFrame` with short gaps filled (same schema as input).
- Side effect: `data/processed/hardware_issue_flags.csv` written for extended gaps.

**Key Logic:**
1. For each `meter_id`, reindex the time series to a complete 15-minute grid covering the meter's full date range using `pd.date_range(freq="15min")`. Missing slots become `NaN` rows.
2. Identify consecutive `NaN` runs using `(df["kwh"].isna().astype(int).groupby(...).cumsum())` or equivalent run-length encoding.
3. **Short gap (1–3 consecutive NaN slots):** For each missing slot, compute the 7-day same-slot median: filter rows where `hour_of_day == slot.hour` and `day_of_week == slot.dayofweek` from the 28 days preceding the gap. Use `median()` of available values (minimum 1 value required; if zero valid values exist, leave as NaN). Fill the gap slot with this median.
4. **Extended gap (> 3 consecutive NaN slots):** Do NOT impute. Record the gap: `meter_id`, `gap_start` (first NaN timestamp), `gap_end` (last NaN timestamp), `gap_length_slots` (count of NaN slots). Append to `hardware_issue_flags.csv`.
5. Write `hardware_issue_flags.csv` with header `meter_id,gap_start,gap_end,gap_length_slots`. If no extended gaps exist, write an empty file with header only.
6. Return the imputed DataFrame (extended-gap slots remain NaN).

**Output Data Contract (hardware_issue_flags.csv):**

| Column | Type | Notes |
|---|---|---|
| `meter_id` | `str` | |
| `gap_start` | `str` | ISO8601 timestamp of first missing slot |
| `gap_end` | `str` | ISO8601 timestamp of last missing slot |
| `gap_length_slots` | `int` | Number of consecutive missing 15-min slots |

---

### 3.5 `pipeline/peer_graph/builder.py`

**Purpose:** Build a geographic adjacency graph of meters within 200 m of each other using the Haversine formula.

**Input:** `pd.DataFrame` from `meter_registry.py` (columns: `meter_id`, `lat`, `lng`).

**Output:** `data/processed/peer_graph.json` — adjacency dict `{meter_id: [neighbor_meter_id, ...]}`.

**Key Logic:**
1. For each pair of meters `(i, j)` where `i != j`, compute Haversine distance:
   ```python
   R = 6371000  # Earth radius in meters
   phi1, phi2 = radians(lat1), radians(lat2)
   dphi = radians(lat2 - lat1)
   dlambda = radians(lng2 - lng1)
   a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dlambda/2)**2
   distance = 2 * R * asin(sqrt(a))
   ```
2. If `distance <= 200`, add `j` to `i`'s neighbor list and `i` to `j`'s neighbor list.
3. For meters with no neighbors within 200 m, store an empty list: `{meter_id: []}`.
4. Serialize the adjacency dict to JSON and write to `data/processed/peer_graph.json`.
5. Idempotent: overwrite on re-run.

**Output Data Contract (peer_graph.json):**
```json
{
  "METER_001": ["METER_002", "METER_003", "METER_007"],
  "METER_002": ["METER_001", "METER_003"],
  "METER_050": []
}
```

---

### 3.6 `pipeline/features/` — Feature Engineering Modules

#### 3.6.1 `baseline.py`

**Purpose:** Compute rolling 28-day median consumption per meter per `(hour_of_day, day_of_week)` slot.

**Input:** Imputed readings DataFrame (meter_id, timestamp, kwh).

**Output:** `data/processed/baseline_lookup.parquet`

**Key Logic:**
1. Add `hour_of_day = timestamp.dt.hour` and `day_of_week = timestamp.dt.dayofweek` columns.
2. For each `(meter_id, hour_of_day, day_of_week)` combination, compute the rolling 28-day median using only data from the 28 days strictly preceding each target timestamp (no leakage).
3. Implementation: group by `(meter_id, hour_of_day, day_of_week)`, then apply a rolling window of 28 × 1 occurrence per day = 28 samples, using `.rolling(window=28, min_periods=1).median()`.
4. Write result to `baseline_lookup.parquet`.

**Output columns:** `meter_id (str)`, `hour_of_day (int8)`, `day_of_week (int8)`, `baseline_kwh (float64)`

#### 3.6.2 `deviations.py`

**Purpose:** Compute three deviation metrics and the peer deviation flag for each reading.

**Input:** Imputed readings DataFrame + `baseline_lookup.parquet` + `peer_graph.json`.

**Output:** DataFrame with added columns (merged back into feature matrix).

**Key Logic:**
1. **`pct_deviation_from_baseline`:** Join readings with baseline on `(meter_id, hour_of_day, day_of_week)`. Compute `(kwh - baseline_kwh) / baseline_kwh * 100`. Handle `baseline_kwh == 0` by setting result to `NaN`.
2. **`z_score`:** For each meter, compute rolling 28-day mean and std. `z_score = (kwh - rolling_mean) / rolling_std`. Use `min_periods=7`.
3. **`peer_deviation_score`:** For each `(meter_id, timestamp)`, load neighbor list from peer_graph. Compute median kwh of all neighbors at the same timestamp. `peer_deviation_score = (kwh - neighbor_median) / neighbor_median * 100`.
4. **`pct_deviation_from_peer_median`:** Same as peer_deviation_score (alias for clarity in the feature matrix).
5. **Peer deviation flag:** Set `peer_deviation_flag = True` when `pct_deviation_from_baseline <= -80` AND at least `ceil(0.75 * len(neighbors))` neighbors (minimum 6 of 8) have `kwh >= their own baseline_kwh * 0.95` (stable or rising).

**Output columns added:** `pct_deviation_from_baseline (float64)`, `z_score (float64)`, `peer_deviation_score (float64)`, `pct_deviation_from_peer_median (float64)`, `peer_deviation_flag (bool)`

#### 3.6.3 `temporal_lags.py`

**Purpose:** Compute lag features and rolling statistics for temporal pattern detection.

**Input:** Imputed readings DataFrame sorted by `(meter_id, timestamp)`.

**Output:** DataFrame with added lag and rolling columns.

**Key Logic:**
1. Sort by `(meter_id, timestamp)`. Group by `meter_id`.
2. **Lag features** (using `.shift()` within each group):
   - `lag_1h`: shift by 4 slots (4 × 15 min = 1 hour)
   - `lag_24h`: shift by 96 slots
   - `lag_48h`: shift by 192 slots
   - `lag_7d`: shift by 672 slots
3. **Rolling statistics** (within each group):
   - `rolling_7d_mean`: `.rolling(window=672, min_periods=96).mean()`
   - `rolling_7d_std`: `.rolling(window=672, min_periods=96).std()`
4. **Trend slope** (`trend_slope_3d`): For each row, collect the same `(hour_of_day, day_of_week)` slot values from the preceding 3 days (3 data points). Fit `np.polyfit(x=[0,1,2], y=values, deg=1)` and store the slope coefficient. Use `NaN` when fewer than 2 points are available.
5. When insufficient history exists for any lag, populate with `NaN` (do not raise).

**Output columns added:** `lag_1h`, `lag_24h`, `lag_48h`, `lag_7d`, `rolling_7d_mean`, `rolling_7d_std`, `trend_slope_3d` — all `float64`

#### 3.6.4 `pattern_fingerprint.py`

**Purpose:** Compute vacation/bypass disambiguation features.

**Input:** Imputed readings DataFrame with baseline columns already joined.

**Output:** DataFrame with added fingerprint columns.

**Key Logic:**
1. **`is_sustained_multiday_drop`:** For each meter, compute a daily flag: `daily_drop = True` if the day's mean kwh is ≥ 50% below the day's mean baseline. Then set `is_sustained_multiday_drop = True` for all rows in a day if that day is part of a run of ≥ 3 consecutive `daily_drop=True` days.
2. **`night_activity_score`:** For each meter, compute `night_mean = mean(kwh where hour in [22,23,0,1,2,3,4])`. Compute `overall_mean = mean(kwh)`. `night_activity_score = night_mean / overall_mean`. Handle `overall_mean == 0` → set to `NaN`.
3. **`is_recurring_daily_pattern`:** For each meter and each day D, check if a consumption dip (kwh < 50% of baseline) occurs at consistent hours (same ±1 hour window) on ≥ 3 of the 5 days [D-4, D-3, D-2, D-1, D]. Set `True` for all rows in day D if this condition holds.
4. **Vacation vs bypass disambiguation:** If `is_sustained_multiday_drop=True` AND `night_activity_score < 0.2` (near-zero night activity), override `is_recurring_daily_pattern = False` for that period (vacation pattern, not bypass).

**Output columns added:** `is_sustained_multiday_drop (bool)`, `night_activity_score (float64)`, `is_recurring_daily_pattern (bool)`

#### 3.6.5 `zone_profiles.py`

**Purpose:** Aggregate per-feeder load profiles and compute stress ratios.

**Input:** Imputed readings DataFrame + meter registry (for `feeder_id`, `transformer_id`, `sanctioned_kva`).

**Output:** `data/processed/zone_profiles.parquet`

**Key Logic:**
1. Join readings with registry on `meter_id` to get `feeder_id` and `transformer_id`.
2. Group by `(feeder_id, timestamp)`, sum `kwh` → `total_load_kwh`.
3. Join with transformer capacity: for each feeder, look up `transformer_rated_kva` (sum of `sanctioned_kva` for all meters on that feeder's transformer, or a fixed rated value from registry).
4. Compute `feeder_stress = total_load_kwh / transformer_rated_kva`.
5. Set `is_high_stress_zone = feeder_stress > 0.90`.
6. Also compute `pct_deviation_from_cluster_norm` per meter: join cluster assignments, compute cluster median load at each timestamp, then `(kwh - cluster_median) / cluster_median * 100`.
7. Write to `zone_profiles.parquet`.

**Output columns:** `feeder_id (str)`, `timestamp (datetime64)`, `total_load_kwh (float64)`, `feeder_stress (float64)`, `is_high_stress_zone (bool)`, `transformer_rated_kva (float64)`

#### 3.6.6 `build_matrix.py`

**Purpose:** Join all feature sources into a single feature matrix Parquet file.

**Input:** All intermediate DataFrames and Parquet files produced by the above modules.

**Output:** `data/processed/feature_matrix.parquet`

**Key Logic:**
1. Start with the imputed readings DataFrame as the base (meter_id, timestamp, kwh).
2. Left-join `baseline_lookup.parquet` on `(meter_id, hour_of_day, day_of_week)`.
3. Merge deviation columns (already computed in-memory or from intermediate Parquet).
4. Merge lag and rolling columns.
5. Merge fingerprint columns.
6. Left-join `cluster_assignments.csv` on `meter_id`.
7. Left-join `zone_profiles.parquet` on `(feeder_id, timestamp)` (after joining feeder_id from registry).
8. If any required source file is missing, raise `FileNotFoundError(f"Required feature source missing: {path}")`.
9. Write final matrix to `feature_matrix.parquet` using `pyarrow` engine.
10. Idempotent: overwrite on re-run.

**Output columns (full feature matrix):**

| Column | Type |
|---|---|
| `meter_id` | `str` |
| `timestamp` | `datetime64[ns, UTC]` |
| `kwh` | `float64` |
| `cluster_id` | `int8` |
| `feeder_id` | `str` |
| `z_score` | `float64` |
| `peer_deviation_score` | `float64` |
| `is_sustained_multiday_drop` | `bool` |
| `is_recurring_daily_pattern` | `bool` |
| `night_activity_score` | `float64` |
| `pct_deviation_from_baseline` | `float64` |
| `pct_deviation_from_peer_median` | `float64` |
| `pct_deviation_from_cluster_norm` | `float64` |
| `lag_1h` | `float64` |
| `lag_24h` | `float64` |
| `lag_48h` | `float64` |
| `lag_7d` | `float64` |
| `rolling_7d_mean` | `float64` |
| `rolling_7d_std` | `float64` |
| `trend_slope_3d` | `float64` |
| `is_high_stress_zone` | `bool` |
| `confirmed_tamper` | `int8` |

---

### 3.7 `pipeline/clustering/seasonal.py`

**Purpose:** Cluster meters into 8 groups based on seasonal load shape similarity.

**Input:** Imputed readings DataFrame.

**Output:** `data/processed/cluster_assignments.csv`, `data/processed/seasonal_profiles.json`

**Key Logic:**
1. For each meter, build a `month × hour` load shape vector: compute mean kwh for each `(month, hour_of_day)` combination → 12 × 24 = 288-dimensional vector.
2. Normalize each vector by dividing by the meter's overall mean kwh (unit-normalize the shape, not the magnitude).
3. Stack all 50 meter vectors into a matrix of shape `(50, 288)`.
4. Fit `sklearn.cluster.KMeans(n_clusters=8, random_state=42, n_init=10)` on this matrix.
5. Assign `cluster_id` (0–7) to each meter.
6. Write `cluster_assignments.csv` with columns `meter_id, cluster_id`.
7. Compute cluster centroid profiles: for each cluster, compute mean load shape vector. Serialize as `{cluster_id: {month: {hour: mean_kwh}}}` to `seasonal_profiles.json`.
8. Idempotent: overwrite on re-run with same input.

**Output Data Contract (cluster_assignments.csv):**

| Column | Type |
|---|---|
| `meter_id` | `str` |
| `cluster_id` | `int` (0–7) |

---

## 4. ML Model Design

### 4a. Feature List for Truth Engine (LightGBM)

All 15 features used as input to the Truth Engine classifier. All are numeric (bool columns cast to int8 before training):

| # | Feature Name | Description | Source Module |
|---|---|---|---|
| 1 | `z_score` | Standard deviations from meter's rolling 28-day mean | `deviations.py` |
| 2 | `peer_deviation_score` | % deviation from median of geographic neighbors at same timestamp | `deviations.py` |
| 3 | `is_sustained_multiday_drop` | 1 if meter has been ≥50% below baseline for ≥3 consecutive days | `pattern_fingerprint.py` |
| 4 | `is_recurring_daily_pattern` | 1 if consistent hourly dip repeats on ≥3 of last 5 days | `pattern_fingerprint.py` |
| 5 | `night_activity_score` | Mean 22:00–05:00 consumption / overall mean consumption | `pattern_fingerprint.py` |
| 6 | `pct_deviation_from_baseline` | % deviation from meter's own 28-day hour×dow median | `deviations.py` |
| 7 | `pct_deviation_from_peer_median` | % deviation from neighbor median (alias of peer_deviation_score) | `deviations.py` |
| 8 | `pct_deviation_from_cluster_norm` | % deviation from cluster median at same timestamp | `zone_profiles.py` |
| 9 | `lag_1h` | kwh value 1 hour prior | `temporal_lags.py` |
| 10 | `lag_24h` | kwh value 24 hours prior | `temporal_lags.py` |
| 11 | `lag_48h` | kwh value 48 hours prior | `temporal_lags.py` |
| 12 | `lag_7d` | kwh value 7 days prior (same slot) | `temporal_lags.py` |
| 13 | `rolling_7d_mean` | 7-day rolling mean kwh | `temporal_lags.py` |
| 14 | `rolling_7d_std` | 7-day rolling standard deviation of kwh | `temporal_lags.py` |
| 15 | `trend_slope_3d` | Linear regression slope over 3-day same-slot window | `temporal_lags.py` |

**Preprocessing before training:**
- Cast `bool` columns to `int8`.
- Fill remaining `NaN` with column median (computed on training split only, applied to val/test).
- No scaling required (LightGBM is tree-based).

---

### 4b. Load Forecast Model (`models/load_forecast/train.py`)

**Purpose:** One XGBoost binary classifier per cluster predicting whether a feeder zone will exceed 90% utilization in the next 24 hours.

**Input features:** Zone-level features from `feature_matrix.parquet` filtered to meters in each cluster:
- `rolling_7d_mean`, `rolling_7d_std`, `trend_slope_3d`, `lag_24h`, `lag_48h`, `lag_7d`
- `feeder_stress` (current utilization ratio from `zone_profiles.parquet`)
- `pct_deviation_from_cluster_norm`
- Hour-of-day and day-of-week as integer features (extracted from timestamp)

**Target:** `is_high_stress_zone` (bool → int8) from `zone_profiles.parquet`. Label = 1 when feeder utilization > 90% in the next 24-hour window.

**Training procedure:**
```python
from sklearn.model_selection import TimeSeriesSplit
from imblearn.over_sampling import SMOTE
import xgboost as xgb

tscv = TimeSeriesSplit(n_splits=5)
for cluster_id in range(8):
    cluster_df = feature_matrix[feature_matrix["cluster_id"] == cluster_id]
    X = cluster_df[LOAD_FORECAST_FEATURES]
    y = cluster_df["is_high_stress_zone"].astype(int)
    
    neg_count = (y == 0).sum()
    pos_count = (y == 1).sum()
    scale_pos_weight = neg_count / pos_count if pos_count > 0 else 1.0
    
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
        X_val, y_val = X.iloc[val_idx], y.iloc[val_idx]
        
        # SMOTE on training split ONLY
        smote = SMOTE(random_state=42)
        X_train_res, y_train_res = smote.fit_resample(X_train, y_train)
        
        model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.05,
            scale_pos_weight=scale_pos_weight,
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
        )
        model.fit(X_train_res, y_train_res,
                  eval_set=[(X_val, y_val)],
                  early_stopping_rounds=20,
                  verbose=False)
    
    # Save final model trained on all data (last fold for demo)
    date_str = datetime.today().strftime("%Y%m%d")
    joblib.dump(model, f"models/load_forecast/xgb_{cluster_id}_v1_{date_str}.joblib")
```

**Output:** `models/load_forecast/xgb_{cluster_id}_v1_{YYYYMMDD}.joblib` — one file per cluster (8 total).

---

### 4c. Truth Engine (`models/truth_engine/train.py`)

**Purpose:** LightGBM binary classifier producing `anomaly_confidence` (probability of tamper/theft) for each meter reading.

**Input features:** All 15 features listed in Section 4a.

**Target:** `confirmed_tamper` (int8) — 1 for injected tamper meters (METER_T001–METER_T005) during days 60–90, 0 otherwise. Ground truth sourced from `injected_events.json`.

**Training procedure:**
```python
import lightgbm as lgb
from sklearn.model_selection import TimeSeriesSplit

TRUTH_ENGINE_FEATURES = [
    "z_score", "peer_deviation_score", "is_sustained_multiday_drop",
    "is_recurring_daily_pattern", "night_activity_score",
    "pct_deviation_from_baseline", "pct_deviation_from_peer_median",
    "pct_deviation_from_cluster_norm", "lag_1h", "lag_24h", "lag_48h",
    "lag_7d", "rolling_7d_mean", "rolling_7d_std", "trend_slope_3d",
]

tscv = TimeSeriesSplit(n_splits=5)
X = feature_matrix[TRUTH_ENGINE_FEATURES].fillna(feature_matrix[TRUTH_ENGINE_FEATURES].median())
y = feature_matrix["confirmed_tamper"].astype(int)

best_model = None
best_auc = 0.0

for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
    X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
    X_val, y_val = X.iloc[val_idx], y.iloc[val_idx]
    
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
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(30), lgb.log_evaluation(-1)],
    )
    fold_auc = roc_auc_score(y_val, model.predict_proba(X_val)[:, 1])
    if fold_auc > best_auc:
        best_auc = fold_auc
        best_model = model

date_str = datetime.today().strftime("%Y%m%d")
joblib.dump(best_model, f"models/truth_engine/lgbm_v1_{date_str}.joblib")
```

**Output:** `models/truth_engine/lgbm_v1_{YYYYMMDD}.joblib`

---

### 4d. Confidence Gating Logic (`models/truth_engine/scorer.py`)

**Purpose:** Apply the two-gate filter (confidence gate + repeat-pattern gate) and write results to the correct queue.

**Full decision logic:**

```python
def score_and_gate(feature_matrix: pd.DataFrame, lgbm_model, db_session):
    """
    For each meter's most recent anomalous reading, score and route to
    alert_events (NEW/WATCHING) or shadow_events.
    """
    TRUTH_ENGINE_FEATURES = [...]  # 15 features as listed in 4a
    
    # Get one row per meter: the most recent reading
    candidates = feature_matrix.sort_values("timestamp").groupby("meter_id").last().reset_index()
    
    X = candidates[TRUTH_ENGINE_FEATURES].fillna(candidates[TRUTH_ENGINE_FEATURES].median())
    scores = lgbm_model.predict_proba(X)[:, 1]  # P(tamper=1)
    
    for i, row in candidates.iterrows():
        meter_id = row["meter_id"]
        score = scores[i]
        
        # Count anomaly history from existing alert_events
        repeat_days = count_anomaly_days_in_last_5(meter_id, db_session)
        consecutive_days = count_consecutive_anomaly_days(meter_id, db_session)
        
        alert = build_alert_object(row, score)  # Populates all alert fields
        
        if score >= 0.90:
            if consecutive_days >= 2 or repeat_days >= 3:
                # Confidence gate PASSED + Repeat-pattern gate PASSED → actionable
                alert["state"] = "NEW"
                write_to_alert_events(alert, db_session)
            else:
                # Confidence gate PASSED, repeat-pattern gate NOT met → watching
                alert["state"] = "WATCHING"
                write_to_alert_events(alert, db_session)
        else:
            # Sub-threshold → shadow queue only, never shown to dispatcher
            write_to_shadow_events(alert, db_session)


def count_anomaly_days_in_last_5(meter_id: str, db_session) -> int:
    """Count distinct days in last 5 calendar days where meter had an alert."""
    cutoff = datetime.utcnow() - timedelta(days=5)
    rows = db_session.execute(
        "SELECT COUNT(DISTINCT DATE(triggered_at)) FROM alert_events "
        "WHERE meter_id = ? AND triggered_at >= ?",
        (meter_id, cutoff.isoformat())
    ).fetchone()
    return rows[0] if rows else 0


def count_consecutive_anomaly_days(meter_id: str, db_session) -> int:
    """Count the current streak of consecutive days with alerts for this meter."""
    rows = db_session.execute(
        "SELECT DISTINCT DATE(triggered_at) FROM alert_events "
        "WHERE meter_id = ? ORDER BY triggered_at DESC LIMIT 10",
        (meter_id,)
    ).fetchall()
    if not rows:
        return 0
    dates = [datetime.strptime(r[0], "%Y-%m-%d").date() for r in rows]
    streak = 1
    for j in range(1, len(dates)):
        if (dates[j-1] - dates[j]).days == 1:
            streak += 1
        else:
            break
    return streak
```

**Routing summary:**

| Score | Repeat Gate | Destination | State |
|---|---|---|---|
| ≥ 0.90 | consecutive_days ≥ 2 OR repeat_days ≥ 3 | `alert_events` | `NEW` |
| ≥ 0.90 | Neither condition met | `alert_events` | `WATCHING` |
| < 0.90 | (not evaluated) | `shadow_events` | `NEW` |

---

### 4e. SHAP Explainability (`models/truth_engine/shap_explainer.py`)

**Purpose:** Compute SHAP values for each alert and generate plain-English explanations for the top-3 contributing features.

**Implementation:**

```python
import shap
import joblib

def explain_alert(alert_features: dict, lgbm_model) -> list[dict]:
    """
    Returns a list of 3 dicts: [{feature, value, plain_english}, ...]
    sorted by abs(shap_value) descending.
    """
    explainer = shap.TreeExplainer(lgbm_model)
    feature_vector = pd.DataFrame([alert_features])[TRUTH_ENGINE_FEATURES]
    shap_values = explainer.shap_values(feature_vector)
    
    # shap_values shape: (1, 15) for binary classification (class=1 values)
    if isinstance(shap_values, list):
        sv = shap_values[1][0]  # LightGBM returns list of [class0, class1]
    else:
        sv = shap_values[0]
    
    # Rank by absolute SHAP value
    ranked = sorted(
        zip(TRUTH_ENGINE_FEATURES, sv, feature_vector.values[0]),
        key=lambda x: abs(x[1]),
        reverse=True
    )
    top3 = ranked[:3]
    
    result = []
    for feature_name, shap_val, feature_val in top3:
        plain = generate_plain_english(feature_name, feature_val, alert_features)
        result.append({
            "feature": feature_name,
            "value": float(shap_val),
            "plain_english": plain,
        })
    return result
```

**Plain-English template per feature:**

```python
def generate_plain_english(feature: str, value: float, ctx: dict) -> str:
    pct = abs(value)
    direction = "below" if value < 0 else "above"
    
    templates = {
        "pct_deviation_from_baseline": (
            f"Consumption is {abs(ctx['pct_deviation_from_baseline']):.0f}% "
            f"{'below' if ctx['pct_deviation_from_baseline'] < 0 else 'above'} "
            f"this meter's 28-day {ctx['day_of_week_name']} "
            f"{ctx['hour_label']} average."
        ),
        "peer_deviation_score": (
            f"Consumption is {abs(ctx['peer_deviation_score']):.0f}% below the "
            f"median of {ctx['neighbor_count']} neighboring meters at the same time."
        ),
        "night_activity_score": (
            f"Night-time usage (10 PM–5 AM) is "
            f"{ctx['night_activity_score']:.1f}x the meter's normal level."
        ),
        "z_score": (
            f"Consumption is {abs(ctx['z_score']):.1f} standard deviations "
            f"{'below' if ctx['z_score'] < 0 else 'above'} "
            f"the meter's historical mean."
        ),
        "is_recurring_daily_pattern": (
            f"A consistent daily dip pattern has repeated on "
            f"{ctx['repeat_days_count']} of the last 5 days."
        ),
        "pct_deviation_from_peer_median": (
            f"Consumption is {abs(ctx['pct_deviation_from_peer_median']):.0f}% "
            f"{'below' if ctx['pct_deviation_from_peer_median'] < 0 else 'above'} "
            f"the peer median."
        ),
        "pct_deviation_from_cluster_norm": (
            f"Consumption is {abs(ctx['pct_deviation_from_cluster_norm']):.0f}% "
            f"{'below' if ctx['pct_deviation_from_cluster_norm'] < 0 else 'above'} "
            f"the cluster norm for this time slot."
        ),
        "is_sustained_multiday_drop": (
            "Consumption has been more than 50% below baseline for 3 or more consecutive days."
        ),
        "trend_slope_3d": (
            f"Consumption has been {'declining' if ctx['trend_slope_3d'] < 0 else 'rising'} "
            f"steadily over the past 3 days."
        ),
    }
    return templates.get(feature, f"{feature} = {value:.3f}")
```

**SHAP values are computed once at alert write time** (in `inference_runner.py`) and stored as a JSON array in the `shap_top3` column of `alert_events`. They are never recomputed on API request.

---

## 5. Database Schema

All tables live in `data/synapse_grid.db` (SQLite). Schema is created via SQLAlchemy `Base.metadata.create_all(engine)` on API startup. No Alembic migrations.

### 5.1 `alert_events`

Primary table for actionable alerts (confidence ≥ 0.90). Both `NEW` and `WATCHING` state alerts live here.

```sql
CREATE TABLE alert_events (
    alert_id                    TEXT PRIMARY KEY,           -- UUID v4
    meter_id                    TEXT NOT NULL,
    alert_type                  TEXT NOT NULL,              -- THEFT_SUSPECT | LOAD_STRESS | HARDWARE_ISSUE
    state                       TEXT NOT NULL DEFAULT 'NEW',-- NEW | UNDER_REVIEW | WATCHING | DISPATCHED | DISMISSED
    anomaly_confidence          REAL NOT NULL,              -- float [0.0, 1.0]
    pattern_type                TEXT,                       -- SUSTAINED_DROP | RECURRING_DAILY_DIP | SPIKE
    triggered_at                TEXT NOT NULL,              -- ISO8601 UTC
    pct_deviation_from_baseline REAL,
    pct_deviation_from_peer_median REAL,
    pct_deviation_from_cluster_norm REAL,
    z_score                     REAL,
    shap_top3                   TEXT,                       -- JSON: [{feature, value, plain_english}]
    peer_status_summary         TEXT,                       -- JSON: {normal: int, elevated: int, anomalous: int}
    repeat_days_count           INTEGER DEFAULT 0,
    dispatch_action             TEXT,                       -- NULL until resolved; DISPATCH_LINEMAN | LOAD_BALANCE | DISMISS
    dismiss_reason              TEXT,                       -- NULL unless state=DISMISSED; VACATION | PLANNED_OUTAGE | FALSE_POSITIVE | OTHER
    resolved_at                 TEXT,                       -- ISO8601 UTC, NULL until resolved
    resolver_id                 TEXT,                       -- NULL until resolved
    feeder_id                   TEXT                        -- Denormalized from meter registry for fast filtering
);
```

**SQLAlchemy ORM model:**
```python
class AlertEvent(Base):
    __tablename__ = "alert_events"
    alert_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    meter_id = Column(String, nullable=False, index=True)
    alert_type = Column(String, nullable=False)
    state = Column(String, nullable=False, default="NEW")
    anomaly_confidence = Column(Float, nullable=False)
    pattern_type = Column(String)
    triggered_at = Column(String, nullable=False)
    pct_deviation_from_baseline = Column(Float)
    pct_deviation_from_peer_median = Column(Float)
    pct_deviation_from_cluster_norm = Column(Float)
    z_score = Column(Float)
    shap_top3 = Column(Text)           # JSON string
    peer_status_summary = Column(Text) # JSON string
    repeat_days_count = Column(Integer, default=0)
    dispatch_action = Column(String)
    dismiss_reason = Column(String)
    resolved_at = Column(String)
    resolver_id = Column(String)
    feeder_id = Column(String, index=True)
```

---

### 5.2 `shadow_events`

Identical schema to `alert_events`. Holds sub-threshold records (confidence < 0.90) for model improvement only. Never served to the frontend.

```sql
CREATE TABLE shadow_events (
    -- Identical column definitions as alert_events
    alert_id                    TEXT PRIMARY KEY,
    meter_id                    TEXT NOT NULL,
    alert_type                  TEXT NOT NULL,
    state                       TEXT NOT NULL DEFAULT 'NEW',
    anomaly_confidence          REAL NOT NULL,
    pattern_type                TEXT,
    triggered_at                TEXT NOT NULL,
    pct_deviation_from_baseline REAL,
    pct_deviation_from_peer_median REAL,
    pct_deviation_from_cluster_norm REAL,
    z_score                     REAL,
    shap_top3                   TEXT,
    peer_status_summary         TEXT,
    repeat_days_count           INTEGER DEFAULT 0,
    dispatch_action             TEXT,
    dismiss_reason              TEXT,
    resolved_at                 TEXT,
    resolver_id                 TEXT,
    feeder_id                   TEXT
);
```

---

### 5.3 `dispatch_audit_log`

Immutable audit trail of every dispatch action taken by a resolver.

```sql
CREATE TABLE dispatch_audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id    TEXT NOT NULL REFERENCES alert_events(alert_id),
    action      TEXT NOT NULL,      -- DISPATCH_LINEMAN | LOAD_BALANCE | DISMISS
    reason_code TEXT,               -- NULL unless action=DISMISS
    resolver_id TEXT NOT NULL,
    resolved_at TEXT NOT NULL       -- ISO8601 UTC
);
```

**SQLAlchemy ORM model:**
```python
class DispatchAuditLog(Base):
    __tablename__ = "dispatch_audit_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(String, ForeignKey("alert_events.alert_id"), nullable=False)
    action = Column(String, nullable=False)
    reason_code = Column(String)
    resolver_id = Column(String, nullable=False)
    resolved_at = Column(String, nullable=False)
```

---

### 5.4 `meter_readings`

Stores the last 14+ days of per-meter readings for the API to serve to the frontend chart.

```sql
CREATE TABLE meter_readings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    meter_id     TEXT NOT NULL,
    timestamp    TEXT NOT NULL,   -- ISO8601 UTC
    kwh          REAL,
    voltage      REAL,
    power_factor REAL,
    reactive_power REAL
);
CREATE INDEX idx_meter_readings_meter_ts ON meter_readings (meter_id, timestamp);
```

**SQLAlchemy ORM model:**
```python
class MeterReading(Base):
    __tablename__ = "meter_readings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    meter_id = Column(String, nullable=False, index=True)
    timestamp = Column(String, nullable=False)
    kwh = Column(Float)
    voltage = Column(Float)
    power_factor = Column(Float)
    reactive_power = Column(Float)
    __table_args__ = (Index("idx_meter_readings_meter_ts", "meter_id", "timestamp"),)
```

---

### 5.5 `meter_registry_cache`

Cached copy of the meter registry for fast API lookups without reading the CSV on every request.

```sql
CREATE TABLE meter_registry_cache (
    meter_id          TEXT PRIMARY KEY,
    lat               REAL,
    lng               REAL,
    feeder_id         TEXT,
    transformer_id    TEXT,
    zone              TEXT,
    consumer_category TEXT,
    sanctioned_kva    REAL,
    connection_date   TEXT
);
```

**SQLAlchemy ORM model:**
```python
class MeterRegistryCache(Base):
    __tablename__ = "meter_registry_cache"
    meter_id = Column(String, primary_key=True)
    lat = Column(Float)
    lng = Column(Float)
    feeder_id = Column(String, index=True)
    transformer_id = Column(String)
    zone = Column(String)
    consumer_category = Column(String)
    sanctioned_kva = Column(Float)
    connection_date = Column(String)
```

---

### 5.6 `feeder_status`

Current utilization and 24-hour forecast per feeder. Written by `inference_runner.py` after each pipeline run.

```sql
CREATE TABLE feeder_status (
    feeder_id                TEXT PRIMARY KEY,
    current_utilization_pct  REAL,
    stress_level             TEXT,   -- GREEN | AMBER | RED (computed at write time)
    transformer_rated_kva    REAL,
    forecast_24h             TEXT,   -- JSON: [{timestamp: ISO8601, predicted_utilization_pct: float}]
    updated_at               TEXT    -- ISO8601 UTC
);
```

**SQLAlchemy ORM model:**
```python
class FeederStatus(Base):
    __tablename__ = "feeder_status"
    feeder_id = Column(String, primary_key=True)
    current_utilization_pct = Column(Float)
    stress_level = Column(String)
    transformer_rated_kva = Column(Float)
    forecast_24h = Column(Text)   # JSON string
    updated_at = Column(String)
```

**Stress level computation (applied at write time in inference_runner.py):**
```python
def compute_stress_level(utilization_pct: float) -> str:
    if utilization_pct <= 70.0:
        return "GREEN"
    elif utilization_pct <= 90.0:
        return "AMBER"
    else:
        return "RED"
```

---

## 6. FastAPI Backend Design

### 6a. `api/database.py`

```python
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

# SQLite only for this prototype. No PostgreSQL, no Alembic.
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./data/synapse_grid.db"
)

engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()


async def init_db() -> None:
    """Create all tables if they do not already exist. Called on API startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """FastAPI dependency: yields an AsyncSession per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
```

**Dependencies required:** `sqlalchemy>=2.0`, `aiosqlite>=0.19`

---

### 6b. `api/main.py`

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.database import init_db
from api.routers.alerts import router as alerts_router
from api.routers.meters import router as meters_router
from api.routers.feeders import router as feeders_router

app = FastAPI(
    title="Synapse-Grid API",
    description="Proactive grid intelligence for BESCOM dispatchers.",
    version="1.0.0",
)

# Allow Vite dev server to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def on_startup():
    await init_db()

# All routers share the /api/v1 prefix
app.include_router(alerts_router, prefix="/api/v1", tags=["alerts"])
app.include_router(meters_router, prefix="/api/v1", tags=["meters"])
app.include_router(feeders_router, prefix="/api/v1", tags=["feeders"])

@app.get("/health")
async def health():
    return {"status": "ok"}
```

---

### 6c. `api/routers/alerts.py`

#### Pydantic Schemas

```python
from pydantic import BaseModel
from typing import Optional, Literal, List, Any

class AlertSummary(BaseModel):
    alert_id: str
    meter_id: str
    alert_type: str
    state: str
    anomaly_confidence: float
    pattern_type: Optional[str]
    triggered_at: str
    feeder_id: Optional[str]

    class Config:
        from_attributes = True


class ShapEntry(BaseModel):
    feature: str
    value: float
    plain_english: str


class AlertDetail(BaseModel):
    alert_id: str
    meter_id: str
    alert_type: str
    state: str
    anomaly_confidence: float
    pattern_type: Optional[str]
    triggered_at: str
    pct_deviation_from_baseline: Optional[float]
    pct_deviation_from_peer_median: Optional[float]
    pct_deviation_from_cluster_norm: Optional[float]
    z_score: Optional[float]
    shap_top3: List[ShapEntry]          # Parsed from JSON string in DB
    peer_status_summary: dict           # Parsed from JSON string in DB
    repeat_days_count: int
    dispatch_action: Optional[str]
    dismiss_reason: Optional[str]
    resolved_at: Optional[str]
    resolver_id: Optional[str]
    feeder_id: Optional[str]

    class Config:
        from_attributes = True


class ActionRequest(BaseModel):
    action: Literal["DISPATCH_LINEMAN", "LOAD_BALANCE", "DISMISS"]
    reason_code: Optional[Literal["VACATION", "PLANNED_OUTAGE", "FALSE_POSITIVE", "OTHER"]] = None
    resolver_id: str
```

#### Route Implementations

```python
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import json
from datetime import datetime, timezone

from api.database import get_db
from api.models import AlertEvent, DispatchAuditLog

router = APIRouter()


@router.get("/alerts", response_model=List[AlertSummary])
async def list_alerts(
    state: Optional[str] = Query(None),
    alert_type: Optional[str] = Query(None),
    feeder_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Return all alerts from alert_events, filtered and sorted by confidence DESC."""
    stmt = select(AlertEvent)
    if state:
        stmt = stmt.where(AlertEvent.state == state)
    if alert_type:
        stmt = stmt.where(AlertEvent.alert_type == alert_type)
    if feeder_id:
        stmt = stmt.where(AlertEvent.feeder_id == feeder_id)
    stmt = stmt.order_by(AlertEvent.anomaly_confidence.desc())
    
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/alerts/{alert_id}", response_model=AlertDetail)
async def get_alert(alert_id: str, db: AsyncSession = Depends(get_db)):
    """Return full alert detail with parsed shap_top3 and peer_status_summary."""
    result = await db.execute(select(AlertEvent).where(AlertEvent.alert_id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    
    # Parse JSON fields before returning
    detail = AlertDetail.from_orm(alert)
    detail.shap_top3 = json.loads(alert.shap_top3 or "[]")
    detail.peer_status_summary = json.loads(alert.peer_status_summary or "{}")
    return detail


@router.patch("/alerts/{alert_id}/action", response_model=AlertDetail)
async def dispatch_action(
    alert_id: str,
    body: ActionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Apply a dispatch action to an alert. Enforces business rules."""
    # Validate DISMISS requires reason_code
    if body.action == "DISMISS" and not body.reason_code:
        raise HTTPException(
            status_code=422,
            detail="reason_code is required when action is DISMISS"
        )
    
    result = await db.execute(select(AlertEvent).where(AlertEvent.alert_id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    
    # Terminal state check
    if alert.state in ("DISPATCHED", "DISMISSED"):
        raise HTTPException(
            status_code=409,
            detail=f"Alert {alert_id} is already in terminal state {alert.state}"
        )
    
    # Apply state transition
    now_iso = datetime.now(timezone.utc).isoformat()
    new_state = "DISPATCHED" if body.action in ("DISPATCH_LINEMAN", "LOAD_BALANCE") else "DISMISSED"
    
    alert.state = new_state
    alert.dispatch_action = body.action
    alert.dismiss_reason = body.reason_code
    alert.resolved_at = now_iso
    alert.resolver_id = body.resolver_id
    
    # Write audit log entry
    audit = DispatchAuditLog(
        alert_id=alert_id,
        action=body.action,
        reason_code=body.reason_code,
        resolver_id=body.resolver_id,
        resolved_at=now_iso,
    )
    db.add(audit)
    await db.commit()
    await db.refresh(alert)
    
    detail = AlertDetail.from_orm(alert)
    detail.shap_top3 = json.loads(alert.shap_top3 or "[]")
    detail.peer_status_summary = json.loads(alert.peer_status_summary or "{}")
    return detail
```

---

### 6d. `api/routers/meters.py`

#### Pydantic Schemas

```python
class ReadingPoint(BaseModel):
    timestamp: str
    kwh: Optional[float]
    voltage: Optional[float]
    power_factor: Optional[float]

class BaselinePoint(BaseModel):
    hour_of_day: int
    day_of_week: int
    baseline_kwh: float

class PeerStatus(BaseModel):
    meter_id: str
    lat: float
    lng: float
    status: Literal["normal", "elevated", "anomalous"]
```

#### Route Implementations

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import pandas as pd
import json
from datetime import datetime, timezone, timedelta

from api.database import get_db
from api.models import MeterReading, MeterRegistryCache

router = APIRouter()

DATA_DIR = "data/processed"


@router.get("/meters/{meter_id}/readings", response_model=List[ReadingPoint])
async def get_meter_readings(meter_id: str, db: AsyncSession = Depends(get_db)):
    """Return last 14 days of readings for the specified meter."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    stmt = (
        select(MeterReading)
        .where(and_(MeterReading.meter_id == meter_id, MeterReading.timestamp >= cutoff))
        .order_by(MeterReading.timestamp.asc())
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No readings found for meter {meter_id}")
    return [ReadingPoint(timestamp=r.timestamp, kwh=r.kwh, voltage=r.voltage, power_factor=r.power_factor) for r in rows]


@router.get("/meters/{meter_id}/baseline", response_model=List[BaselinePoint])
async def get_meter_baseline(meter_id: str):
    """Read baseline_lookup.parquet and return all slots for this meter."""
    baseline_path = f"{DATA_DIR}/baseline_lookup.parquet"
    try:
        df = pd.read_parquet(baseline_path)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Baseline data not yet computed. Run pipeline first.")
    
    meter_df = df[df["meter_id"] == meter_id]
    if meter_df.empty:
        raise HTTPException(status_code=404, detail=f"No baseline found for meter {meter_id}")
    
    return [
        BaselinePoint(hour_of_day=int(r.hour_of_day), day_of_week=int(r.day_of_week), baseline_kwh=float(r.baseline_kwh))
        for r in meter_df.itertuples()
    ]


@router.get("/meters/{meter_id}/peers", response_model=List[PeerStatus])
async def get_meter_peers(meter_id: str, db: AsyncSession = Depends(get_db)):
    """
    Return peer meters with their current consumption status.
    Status classification: compare latest reading to their baseline.
    - normal: within ±20% of baseline
    - elevated: >20% above baseline
    - anomalous: >20% below baseline
    """
    # Load peer graph
    try:
        with open(f"{DATA_DIR}/peer_graph.json") as f:
            peer_graph = json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Peer graph not yet computed. Run pipeline first.")
    
    neighbor_ids = peer_graph.get(meter_id, [])
    if not neighbor_ids:
        return []
    
    # Load baseline for classification
    baseline_df = pd.read_parquet(f"{DATA_DIR}/baseline_lookup.parquet")
    
    result = []
    for neighbor_id in neighbor_ids:
        # Get registry entry for lat/lng
        reg_result = await db.execute(
            select(MeterRegistryCache).where(MeterRegistryCache.meter_id == neighbor_id)
        )
        reg = reg_result.scalar_one_or_none()
        if not reg:
            continue
        
        # Get latest reading
        reading_result = await db.execute(
            select(MeterReading)
            .where(MeterReading.meter_id == neighbor_id)
            .order_by(MeterReading.timestamp.desc())
            .limit(1)
        )
        reading = reading_result.scalar_one_or_none()
        if not reading:
            continue
        
        # Classify status
        ts = datetime.fromisoformat(reading.timestamp)
        baseline_row = baseline_df[
            (baseline_df["meter_id"] == neighbor_id) &
            (baseline_df["hour_of_day"] == ts.hour) &
            (baseline_df["day_of_week"] == ts.weekday())
        ]
        
        if baseline_row.empty or reading.kwh is None:
            status = "normal"
        else:
            baseline_kwh = baseline_row["baseline_kwh"].iloc[0]
            if baseline_kwh == 0:
                status = "normal"
            else:
                pct_dev = (reading.kwh - baseline_kwh) / baseline_kwh * 100
                if pct_dev < -20:
                    status = "anomalous"
                elif pct_dev > 20:
                    status = "elevated"
                else:
                    status = "normal"
        
        result.append(PeerStatus(meter_id=neighbor_id, lat=reg.lat, lng=reg.lng, status=status))
    
    return result
```

---

### 6e. `api/routers/feeders.py`

#### Pydantic Schemas

```python
class ForecastPoint(BaseModel):
    timestamp: str
    predicted_utilization_pct: float

class FeederStatusResponse(BaseModel):
    feeder_id: str
    current_utilization_pct: float
    stress_level: str   # GREEN | AMBER | RED
    transformer_rated_kva: Optional[float]
    forecast_24h: List[ForecastPoint]
```

#### Route Implementation

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import json

from api.database import get_db
from api.models import FeederStatus

router = APIRouter()


@router.get("/feeders", response_model=List[FeederStatusResponse])
async def list_feeders(db: AsyncSession = Depends(get_db)):
    """
    Return all feeders with current utilization, stress level, and 24h forecast.
    stress_level is computed from current_utilization_pct:
      <= 70%  → GREEN
      70-90%  → AMBER
      > 90%   → RED
    """
    result = await db.execute(select(FeederStatus))
    feeders = result.scalars().all()
    
    response = []
    for f in feeders:
        # Recompute stress_level at serve time for accuracy
        pct = f.current_utilization_pct or 0.0
        if pct <= 70.0:
            stress = "GREEN"
        elif pct <= 90.0:
            stress = "AMBER"
        else:
            stress = "RED"
        
        forecast = json.loads(f.forecast_24h or "[]")
        
        response.append(FeederStatusResponse(
            feeder_id=f.feeder_id,
            current_utilization_pct=pct,
            stress_level=stress,
            transformer_rated_kva=f.transformer_rated_kva,
            forecast_24h=[ForecastPoint(**pt) for pt in forecast],
        ))
    
    return response
```

---

## 7. React Frontend Design

### 7a. Component Tree

```
App (React 18, QueryClientProvider wrapping all)
└── Dashboard (src/pages/Dashboard.tsx)
    │   Layout: CSS Grid, 3 columns: 25% | 35% | 40%
    │   Height: 100vh, overflow hidden per panel
    │
    ├── FeederStressMap (src/components/FeederStressMap.tsx)  [left panel, 25%]
    │   └── react-leaflet MapContainer
    │       ├── TileLayer (OpenStreetMap)
    │       └── CircleMarker per feeder
    │           ├── color: GREEN=#22c55e, AMBER=#f59e0b, RED=#ef4444
    │           ├── radius: 18px
    │           ├── Popup: feeder_id + utilization % + stress_level
    │           └── onClick: filter AlertQueue to that feeder_id
    │
    ├── AlertQueue (src/components/AlertQueue.tsx)  [center panel, 35%]
    │   ├── Header: "Alert Queue" + count badge
    │   ├── Filter bar: state dropdown (ALL | NEW | WATCHING | UNDER_REVIEW)
    │   └── AlertRow[] (sorted by anomaly_confidence DESC)
    │       ├── Left: meter_id (bold) + feeder_id (muted)
    │       ├── Center: confidence % badge (color: ≥95% red, ≥90% amber, else gray)
    │       ├── pattern_type badge (SUSTAINED_DROP | RECURRING_DAILY_DIP | SPIKE)
    │       └── Right: state badge
    │           ├── NEW → blue
    │           ├── WATCHING → yellow
    │           ├── UNDER_REVIEW → purple
    │           ├── DISPATCHED → green
    │           └── DISMISSED → gray
    │
    └── AlertDetailPane (src/components/)  [right panel, 40%]
        │   Renders only when selectedAlertId is set in Zustand store
        │   Shows skeleton loader while useAlertDetail is loading
        │
        ├── AlertDetail (src/components/AlertDetail.tsx)
        │   ├── Header: meter_id + alert_type badge + triggered_at
        │   ├── Recharts LineChart (14-day consumption chart)
        │   │   ├── X-axis: timestamps (15-min intervals, last 14 days)
        │   │   ├── Y-axis: kWh
        │   │   ├── Line "Actual" — blue solid (#3b82f6)
        │   │   ├── Line "Baseline" — gray dashed (#9ca3af)
        │   │   ├── Line "Peer Median" — orange dashed (#f97316)
        │   │   └── ReferenceArea: triggered_at ± 24h, fill="#fef08a" opacity=0.3
        │   ├── DeviationMetricsCard
        │   │   ├── Row 1: "vs Baseline" → pct_deviation_from_baseline %
        │   │   ├── Row 2: "vs Peer Median" → pct_deviation_from_peer_median %
        │   │   └── Row 3: "vs Cluster Norm" → pct_deviation_from_cluster_norm %
        │   │       Each row: label + value (red if negative, green if positive)
        │   ├── ShapExplanationCard
        │   │   ├── Title: "Why this alert?"
        │   │   └── 3 rows: feature name + plain_english string
        │   │       Each row has a small bar showing relative SHAP magnitude
        │   └── NeighborhoodMiniMap
        │       └── react-leaflet MapContainer (small, 200px height)
        │           ├── CircleMarker for the alerted meter (red, larger)
        │           └── CircleMarker per peer (color by status: normal=green, elevated=amber, anomalous=red)
        │
        └── DispatchPanel (src/components/DispatchPanel.tsx)
            ├── ActionButtons row
            │   ├── "DISPATCH LINEMAN" button (red variant)
            │   ├── "LOAD BALANCE" button (amber variant)
            │   └── "DISMISS" button (gray variant)
            ├── ReasonCodeDropdown (shadcn/ui Select)
            │   Visible only when pendingAction === "DISMISS"
            │   Options: VACATION | PLANNED_OUTAGE | FALSE_POSITIVE | OTHER
            ├── Submit button
            │   Disabled when: no action selected, OR action=DISMISS and no reason_code
            │   On click: openConfirmModal()
            ├── ConfirmationModal (src/components/ConfirmationModal.tsx)
            │   shadcn/ui Dialog
            │   Shows: "Confirm: {action} for meter {meter_id}?"
            │   If DISMISS: shows reason_code
            │   Buttons: "Confirm" (calls PATCH API) | "Cancel" (closeConfirmModal)
            └── ResolutionDisplay
                Shown after successful action submission
                Shows: action taken + resolved_at timestamp + resolver_id
```

---

### 7b. Zustand Store (`src/stores/alertStore.ts`)

```typescript
import { create } from "zustand";

type ActionType = "DISPATCH_LINEMAN" | "LOAD_BALANCE" | "DISMISS";
type ReasonCode = "VACATION" | "PLANNED_OUTAGE" | "FALSE_POSITIVE" | "OTHER";

interface AlertStore {
  // Selection state
  selectedAlertId: string | null;
  feederFilter: string | null;

  // Dispatch workflow state
  pendingAction: ActionType | null;
  pendingReasonCode: ReasonCode | null;
  showConfirmModal: boolean;

  // Actions
  selectAlert: (id: string) => void;
  clearSelection: () => void;
  setFeederFilter: (feederId: string | null) => void;
  setPendingAction: (action: ActionType | null) => void;
  setPendingReasonCode: (code: ReasonCode | null) => void;
  openConfirmModal: () => void;
  closeConfirmModal: () => void;
  resetDispatch: () => void;
}

export const useAlertStore = create<AlertStore>((set) => ({
  selectedAlertId: null,
  feederFilter: null,
  pendingAction: null,
  pendingReasonCode: null,
  showConfirmModal: false,

  selectAlert: (id) =>
    set({
      selectedAlertId: id,
      pendingAction: null,
      pendingReasonCode: null,
      showConfirmModal: false,
    }),

  clearSelection: () =>
    set({
      selectedAlertId: null,
      pendingAction: null,
      pendingReasonCode: null,
      showConfirmModal: false,
    }),

  setFeederFilter: (feederId) => set({ feederFilter: feederId }),

  setPendingAction: (action) =>
    set({
      pendingAction: action,
      // Clear reason code when action changes
      pendingReasonCode: action === "DISMISS" ? null : null,
    }),

  setPendingReasonCode: (code) => set({ pendingReasonCode: code }),

  openConfirmModal: () => set({ showConfirmModal: true }),

  closeConfirmModal: () => set({ showConfirmModal: false }),

  resetDispatch: () =>
    set({
      pendingAction: null,
      pendingReasonCode: null,
      showConfirmModal: false,
    }),
}));
```

**State transition rules enforced in store and components:**

| From State | Trigger | To State |
|---|---|---|
| (any) | `selectAlert(id)` called | `UNDER_REVIEW` (API PATCH called by AlertDetail on mount) |
| `UNDER_REVIEW` | DISPATCH_LINEMAN or LOAD_BALANCE confirmed | `DISPATCHED` |
| `UNDER_REVIEW` | DISMISS + reason_code confirmed | `DISMISSED` |
| `DISPATCHED` | (any) | Terminal — action buttons disabled |
| `DISMISSED` | (any) | Terminal — action buttons disabled |

Note: The `UNDER_REVIEW` state transition is triggered by the frontend calling `PATCH /api/v1/alerts/{id}/action` with `action="UNDER_REVIEW"` is NOT a valid API action — instead, the frontend simply reflects the state returned by the API after the dispatcher opens the detail view. The API does not auto-transition to UNDER_REVIEW; this is a display-only state in the frontend until a real action is taken.

---

### 7c. TanStack Query Hooks (`src/hooks/`)

#### `useAlertQueue.ts`

```typescript
import { useQuery } from "@tanstack/react-query";
import { fetchAlerts } from "../lib/api";
import { useAlertStore } from "../stores/alertStore";

export function useAlertQueue() {
  const feederFilter = useAlertStore((s) => s.feederFilter);

  return useQuery({
    queryKey: ["alerts", feederFilter],
    queryFn: () => fetchAlerts({ feeder_id: feederFilter ?? undefined }),
    refetchInterval: 5 * 60 * 1000,  // 5 minutes
    staleTime: 60 * 1000,             // 1 minute
  });
}
```

#### `useAlertDetail.ts`

```typescript
import { useQuery } from "@tanstack/react-query";
import { fetchAlertDetail } from "../lib/api";

export function useAlertDetail(alertId: string | null) {
  return useQuery({
    queryKey: ["alert", alertId],
    queryFn: () => fetchAlertDetail(alertId!),
    enabled: !!alertId,
    staleTime: 30 * 1000,  // 30 seconds
  });
}
```

#### `useFeederStatus.ts`

```typescript
import { useQuery } from "@tanstack/react-query";
import { fetchFeeders } from "../lib/api";

export function useFeederStatus() {
  return useQuery({
    queryKey: ["feeders"],
    queryFn: fetchFeeders,
    refetchInterval: 5 * 60 * 1000,
    staleTime: 60 * 1000,
  });
}
```

#### `useMeterReadings.ts`

```typescript
import { useQuery } from "@tanstack/react-query";
import { fetchMeterReadings, fetchMeterBaseline, fetchMeterPeers } from "../lib/api";

export function useMeterReadings(meterId: string | null) {
  return useQuery({
    queryKey: ["readings", meterId],
    queryFn: () => fetchMeterReadings(meterId!),
    enabled: !!meterId,
    staleTime: 60 * 1000,
  });
}

export function useMeterBaseline(meterId: string | null) {
  return useQuery({
    queryKey: ["baseline", meterId],
    queryFn: () => fetchMeterBaseline(meterId!),
    enabled: !!meterId,
    staleTime: 5 * 60 * 1000,  // Baseline changes slowly
  });
}

export function useMeterPeers(meterId: string | null) {
  return useQuery({
    queryKey: ["peers", meterId],
    queryFn: () => fetchMeterPeers(meterId!),
    enabled: !!meterId,
    staleTime: 60 * 1000,
  });
}
```

---

### 7d. `src/lib/api.ts` — Typed API Client

```typescript
const API_BASE = "/api/v1";

// --- Types ---

export interface AlertSummary {
  alert_id: string;
  meter_id: string;
  alert_type: "THEFT_SUSPECT" | "LOAD_STRESS" | "HARDWARE_ISSUE";
  state: "NEW" | "UNDER_REVIEW" | "WATCHING" | "DISPATCHED" | "DISMISSED";
  anomaly_confidence: number;
  pattern_type: "SUSTAINED_DROP" | "RECURRING_DAILY_DIP" | "SPIKE" | null;
  triggered_at: string;
  feeder_id: string | null;
}

export interface ShapEntry {
  feature: string;
  value: number;
  plain_english: string;
}

export interface AlertDetail extends AlertSummary {
  pct_deviation_from_baseline: number | null;
  pct_deviation_from_peer_median: number | null;
  pct_deviation_from_cluster_norm: number | null;
  z_score: number | null;
  shap_top3: ShapEntry[];
  peer_status_summary: { normal: number; elevated: number; anomalous: number };
  repeat_days_count: number;
  dispatch_action: string | null;
  dismiss_reason: string | null;
  resolved_at: string | null;
  resolver_id: string | null;
}

export interface ReadingPoint {
  timestamp: string;
  kwh: number | null;
  voltage: number | null;
  power_factor: number | null;
}

export interface BaselinePoint {
  hour_of_day: number;
  day_of_week: number;
  baseline_kwh: number;
}

export interface PeerStatus {
  meter_id: string;
  lat: number;
  lng: number;
  status: "normal" | "elevated" | "anomalous";
}

export interface FeederStatusResponse {
  feeder_id: string;
  current_utilization_pct: number;
  stress_level: "GREEN" | "AMBER" | "RED";
  transformer_rated_kva: number | null;
  forecast_24h: Array<{ timestamp: string; predicted_utilization_pct: number }>;
}

export interface ActionRequest {
  action: "DISPATCH_LINEMAN" | "LOAD_BALANCE" | "DISMISS";
  reason_code?: "VACATION" | "PLANNED_OUTAGE" | "FALSE_POSITIVE" | "OTHER";
  resolver_id: string;
}

// --- Fetch helpers ---

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const fetchAlerts = (params?: { state?: string; alert_type?: string; feeder_id?: string }) => {
  const qs = new URLSearchParams();
  if (params?.state) qs.set("state", params.state);
  if (params?.alert_type) qs.set("alert_type", params.alert_type);
  if (params?.feeder_id) qs.set("feeder_id", params.feeder_id);
  const query = qs.toString() ? `?${qs}` : "";
  return apiFetch<AlertSummary[]>(`/alerts${query}`);
};

export const fetchAlertDetail = (alertId: string) =>
  apiFetch<AlertDetail>(`/alerts/${alertId}`);

export const submitAlertAction = (alertId: string, body: ActionRequest) =>
  apiFetch<AlertDetail>(`/alerts/${alertId}/action`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const fetchMeterReadings = (meterId: string) =>
  apiFetch<ReadingPoint[]>(`/meters/${meterId}/readings`);

export const fetchMeterBaseline = (meterId: string) =>
  apiFetch<BaselinePoint[]>(`/meters/${meterId}/baseline`);

export const fetchMeterPeers = (meterId: string) =>
  apiFetch<PeerStatus[]>(`/meters/${meterId}/peers`);

export const fetchFeeders = () =>
  apiFetch<FeederStatusResponse[]>(`/feeders`);
```

---

### 7e. DispatchPanel Logic (`src/components/DispatchPanel.tsx`)

```typescript
import { useAlertStore } from "../stores/alertStore";
import { useQueryClient, useMutation } from "@tanstack/react-query";
import { submitAlertAction } from "../lib/api";

export function DispatchPanel({ alertId, alertState }: { alertId: string; alertState: string }) {
  const {
    pendingAction, pendingReasonCode, showConfirmModal,
    setPendingAction, setPendingReasonCode,
    openConfirmModal, closeConfirmModal, resetDispatch,
  } = useAlertStore();
  const queryClient = useQueryClient();

  const isTerminal = alertState === "DISPATCHED" || alertState === "DISMISSED";

  const mutation = useMutation({
    mutationFn: () =>
      submitAlertAction(alertId, {
        action: pendingAction!,
        reason_code: pendingReasonCode ?? undefined,
        resolver_id: "dispatcher-001",  // Hardcoded for prototype (no auth)
      }),
    onSuccess: () => {
      resetDispatch();
      // Invalidate both the list and the detail cache
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
      queryClient.invalidateQueries({ queryKey: ["alert", alertId] });
    },
  });

  // Submit button disabled conditions:
  const submitDisabled =
    !pendingAction ||
    (pendingAction === "DISMISS" && !pendingReasonCode) ||
    mutation.isPending;

  return (
    <div className="flex flex-col gap-3 p-4 border-t">
      {/* Action buttons */}
      <div className="flex gap-2">
        <button
          disabled={isTerminal}
          onClick={() => setPendingAction("DISPATCH_LINEMAN")}
          className={`flex-1 px-3 py-2 rounded text-sm font-medium
            ${pendingAction === "DISPATCH_LINEMAN" ? "bg-red-600 text-white" : "bg-red-100 text-red-700"}
            disabled:opacity-40 disabled:cursor-not-allowed`}
        >
          Dispatch Lineman
        </button>
        <button
          disabled={isTerminal}
          onClick={() => setPendingAction("LOAD_BALANCE")}
          className={`flex-1 px-3 py-2 rounded text-sm font-medium
            ${pendingAction === "LOAD_BALANCE" ? "bg-amber-600 text-white" : "bg-amber-100 text-amber-700"}
            disabled:opacity-40 disabled:cursor-not-allowed`}
        >
          Load Balance
        </button>
        <button
          disabled={isTerminal}
          onClick={() => setPendingAction("DISMISS")}
          className={`flex-1 px-3 py-2 rounded text-sm font-medium
            ${pendingAction === "DISMISS" ? "bg-gray-600 text-white" : "bg-gray-100 text-gray-700"}
            disabled:opacity-40 disabled:cursor-not-allowed`}
        >
          Dismiss
        </button>
      </div>

      {/* Reason code dropdown — only shown when DISMISS selected */}
      {pendingAction === "DISMISS" && (
        <select
          value={pendingReasonCode ?? ""}
          onChange={(e) => setPendingReasonCode(e.target.value as any || null)}
          className="w-full border rounded px-3 py-2 text-sm"
        >
          <option value="">Select reason code...</option>
          <option value="VACATION">Vacation</option>
          <option value="PLANNED_OUTAGE">Planned Outage</option>
          <option value="FALSE_POSITIVE">False Positive</option>
          <option value="OTHER">Other</option>
        </select>
      )}

      {/* Submit button */}
      {!isTerminal && (
        <button
          disabled={submitDisabled}
          onClick={openConfirmModal}
          className="w-full bg-blue-600 text-white py-2 rounded font-medium
            disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {mutation.isPending ? "Submitting..." : "Submit Action"}
        </button>
      )}

      {/* Confirmation modal */}
      {showConfirmModal && (
        <ConfirmationModal
          action={pendingAction!}
          reasonCode={pendingReasonCode}
          meterId={alertId}
          onConfirm={() => {
            closeConfirmModal();
            mutation.mutate();
          }}
          onCancel={closeConfirmModal}
        />
      )}

      {/* Terminal state display */}
      {isTerminal && (
        <div className="text-sm text-gray-500 text-center py-2">
          Alert {alertState.toLowerCase()}. No further actions available.
        </div>
      )}
    </div>
  );
}
```

---

### 7f. AlertDetail Chart Data (`src/components/AlertDetail.tsx`)

The 14-day consumption chart uses Recharts `ComposedChart` with three data series merged on timestamp:

```typescript
// Merge readings + baseline + peer median into a single array for Recharts
function buildChartData(
  readings: ReadingPoint[],
  baseline: BaselinePoint[],
  peers: PeerStatus[],
  peerReadings: Record<string, ReadingPoint[]>,
): ChartDataPoint[] {
  // Build baseline lookup: {hour_of_day: {day_of_week: baseline_kwh}}
  const baselineLookup: Record<number, Record<number, number>> = {};
  for (const b of baseline) {
    if (!baselineLookup[b.hour_of_day]) baselineLookup[b.hour_of_day] = {};
    baselineLookup[b.hour_of_day][b.day_of_week] = b.baseline_kwh;
  }

  return readings.map((r) => {
    const ts = new Date(r.timestamp);
    const hour = ts.getHours();
    const dow = ts.getDay();
    const baselineKwh = baselineLookup[hour]?.[dow] ?? null;

    // Compute peer median at this timestamp
    const peerKwhValues = peers
      .map((p) => {
        const peerReading = peerReadings[p.meter_id]?.find(
          (pr) => pr.timestamp === r.timestamp
        );
        return peerReading?.kwh ?? null;
      })
      .filter((v): v is number => v !== null);

    const peerMedian =
      peerKwhValues.length > 0
        ? peerKwhValues.sort((a, b) => a - b)[Math.floor(peerKwhValues.length / 2)]
        : null;

    return {
      timestamp: r.timestamp,
      actual: r.kwh,
      baseline: baselineKwh,
      peerMedian,
    };
  });
}
```

**Recharts configuration:**
```typescript
<ComposedChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
  <XAxis
    dataKey="timestamp"
    tickFormatter={(ts) => format(new Date(ts), "MMM d")}
    interval={96}  // Show one tick per day (96 × 15min = 24h)
  />
  <YAxis unit=" kWh" />
  <Tooltip
    labelFormatter={(ts) => format(new Date(ts as string), "MMM d, HH:mm")}
  />
  <Legend />

  {/* Anomalous time range highlight */}
  <ReferenceArea
    x1={anomalyStart}
    x2={anomalyEnd}
    fill="#fef08a"
    fillOpacity={0.3}
    label="Anomaly"
  />

  <Line
    type="monotone"
    dataKey="actual"
    stroke="#3b82f6"
    strokeWidth={2}
    dot={false}
    name="Actual"
  />
  <Line
    type="monotone"
    dataKey="baseline"
    stroke="#9ca3af"
    strokeWidth={1.5}
    strokeDasharray="5 5"
    dot={false}
    name="Baseline"
  />
  <Line
    type="monotone"
    dataKey="peerMedian"
    stroke="#f97316"
    strokeWidth={1.5}
    strokeDasharray="3 3"
    dot={false}
    name="Peer Median"
  />
</ComposedChart>
```

---

## 8. `run_pipeline.py` Orchestration Design

### 8.1 Full Stage Sequence

```python
#!/usr/bin/env python3
"""
run_pipeline.py — Synapse-Grid full pipeline CLI trigger.

Usage:
    python run_pipeline.py [--data-dir data/raw] [--force]

Options:
    --data-dir PATH   Directory containing sample_readings.csv and sample_registry.csv.
                      Defaults to data/raw/
    --force           Force re-run of all stages even if outputs are up to date.
"""

import argparse
import sys
from datetime import datetime

from scripts.generate_synthetic_data import check_or_generate_synthetic_data
from pipeline.ingest.meter_reader import ingest_readings
from pipeline.ingest.validator import validate_readings
from pipeline.impute.gap_handler import impute_gaps
from pipeline.peer_graph.builder import build_peer_graph
from pipeline.features.baseline import compute_baseline
from pipeline.features.deviations import compute_deviations
from pipeline.features.temporal_lags import compute_lag_features
from pipeline.features.pattern_fingerprint import compute_pattern_fingerprints
from pipeline.features.zone_profiles import compute_zone_profiles
from pipeline.clustering.seasonal import fit_seasonal_clusters
from pipeline.features.build_matrix import build_feature_matrix
from models.load_forecast.train import train_load_forecast_models
from models.truth_engine.train import train_truth_engine
from models.inference_runner import run_inference_and_write_alerts
from models.eval.evaluate import evaluate_models


def parse_args():
    parser = argparse.ArgumentParser(description="Synapse-Grid pipeline runner")
    parser.add_argument(
        "--data-dir",
        default="data/raw",
        help="Directory containing input CSV files (default: data/raw)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-run all stages, ignoring cached outputs",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    stages = [
        ("Synthetic Data Check",   check_or_generate_synthetic_data),
        ("Ingest Readings",        ingest_readings),
        ("Validate",               validate_readings),
        ("Impute Gaps",            impute_gaps),
        ("Build Peer Graph",       build_peer_graph),
        ("Compute Baseline",       compute_baseline),
        ("Compute Deviations",     compute_deviations),
        ("Compute Lag Features",   compute_lag_features),
        ("Pattern Fingerprints",   compute_pattern_fingerprints),
        ("Zone Profiles",          compute_zone_profiles),
        ("Seasonal Clustering",    fit_seasonal_clusters),
        ("Build Feature Matrix",   build_feature_matrix),
        ("Train Load Forecast",    train_load_forecast_models),
        ("Train Truth Engine",     train_truth_engine),
        ("Run Inference",          run_inference_and_write_alerts),
        ("Evaluate Models",        evaluate_models),
    ]

    print(f"\n{'='*60}")
    print(f"  Synapse-Grid Pipeline  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Data dir: {args.data_dir}  |  Force: {args.force}")
    print(f"{'='*60}\n")

    for stage_name, stage_fn in stages:
        try:
            print(f"[{stage_name}] Starting...")
            result = stage_fn(data_dir=args.data_dir, force=args.force)
            if result and result.get("skipped"):
                print(f"[{stage_name}] Skipped (output up to date).")
            else:
                print(f"[{stage_name}] Done.")
        except Exception as e:
            print(f"\n[{stage_name}] FAILED: {e}", file=sys.stderr)
            print("Pipeline aborted. Fix the error above and re-run.", file=sys.stderr)
            sys.exit(1)

    # Print final summary
    from models.inference_runner import get_pipeline_summary
    summary = get_pipeline_summary()

    print(f"\n{'='*60}")
    print(f"  === Pipeline Complete ===")
    print(f"  Meters processed:          {summary['meters_processed']}")
    print(f"  Alerts written (NEW):      {summary['alerts_new']}")
    print(f"  Alerts written (WATCHING): {summary['alerts_watching']}")
    print(f"  Shadow queue records:      {summary['shadow_records']}")
    print(f"  Eval report:               models/eval/eval_report.json")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
```

---

### 8.2 Idempotency Strategy

Each stage function accepts a `force: bool` parameter and implements the following check at the top:

```python
def compute_baseline(data_dir: str = "data/raw", force: bool = False) -> dict:
    OUTPUT_PATH = "data/processed/baseline_lookup.parquet"
    INPUT_PATH = "data/processed/feature_matrix_imputed.parquet"  # or equivalent input

    if not force and _is_output_fresh(OUTPUT_PATH, INPUT_PATH):
        return {"skipped": True}

    # ... actual computation ...
    return {"skipped": False}


def _is_output_fresh(output_path: str, *input_paths: str) -> bool:
    """
    Returns True if output_path exists AND its mtime is newer than
    all input_paths. Returns False if output is missing or stale.
    """
    import os
    if not os.path.exists(output_path):
        return False
    output_mtime = os.path.getmtime(output_path)
    for inp in input_paths:
        if os.path.exists(inp) and os.path.getmtime(inp) > output_mtime:
            return False
    return True
```

**Idempotency guarantees per stage:**

| Stage | Output file(s) | Freshness check input |
|---|---|---|
| Synthetic Data Check | `data/raw/sample_readings.csv`, `sample_registry.csv` | File existence only |
| Ingest Readings | In-memory only | N/A |
| Validate | `data_quality_log.db` | `sample_readings.csv` mtime |
| Impute Gaps | In-memory + `hardware_issue_flags.csv` | `sample_readings.csv` mtime |
| Build Peer Graph | `peer_graph.json` | `sample_registry.csv` mtime |
| Compute Baseline | `baseline_lookup.parquet` | `sample_readings.csv` mtime |
| Compute Deviations | In-memory (merged into matrix) | `baseline_lookup.parquet` mtime |
| Compute Lag Features | In-memory | N/A |
| Pattern Fingerprints | In-memory | N/A |
| Zone Profiles | `zone_profiles.parquet` | `sample_readings.csv` mtime |
| Seasonal Clustering | `cluster_assignments.csv`, `seasonal_profiles.json` | `sample_readings.csv` mtime |
| Build Feature Matrix | `feature_matrix.parquet` | All upstream parquet mtimes |
| Train Load Forecast | `xgb_{cluster_id}_v1_{date}.joblib` | `feature_matrix.parquet` mtime |
| Train Truth Engine | `lgbm_v1_{date}.joblib` | `feature_matrix.parquet` mtime |
| Run Inference | `alert_events` table rows | `lgbm_v1_*.joblib` mtime |
| Evaluate Models | `eval_report.json`, `confusion_matrix.png` | Model file mtimes |

**Alert deduplication in `run_inference_and_write_alerts`:**
Before writing an alert, check if an alert for the same `(meter_id, DATE(triggered_at))` already exists in `alert_events`. If it does, update the existing record rather than inserting a duplicate. This ensures re-running the pipeline on the same data does not create duplicate alert rows.

---

### 8.3 Final Summary Output

```
============================================================
  Synapse-Grid Pipeline  —  2024-11-15 14:32:07
  Data dir: data/raw  |  Force: False
============================================================

[Synthetic Data Check] Starting...
[Synthetic Data Check] Skipped (output up to date).
[Ingest Readings] Starting...
[Ingest Readings] Done.
[Validate] Starting...
[Validate] Done.
[Impute Gaps] Starting...
[Impute Gaps] Done.
[Build Peer Graph] Starting...
[Build Peer Graph] Skipped (output up to date).
[Compute Baseline] Starting...
[Compute Baseline] Skipped (output up to date).
[Compute Deviations] Starting...
[Compute Deviations] Done.
[Compute Lag Features] Starting...
[Compute Lag Features] Done.
[Pattern Fingerprints] Starting...
[Pattern Fingerprints] Done.
[Zone Profiles] Starting...
[Zone Profiles] Done.
[Seasonal Clustering] Starting...
[Seasonal Clustering] Skipped (output up to date).
[Build Feature Matrix] Starting...
[Build Feature Matrix] Done.
[Train Load Forecast] Starting...
[Train Load Forecast] Done.
[Train Truth Engine] Starting...
[Train Truth Engine] Done.
[Run Inference] Starting...
[Run Inference] Done.
[Evaluate Models] Starting...
[Evaluate Models] Done.

============================================================
  === Pipeline Complete ===
  Meters processed:          50
  Alerts written (NEW):      8
  Alerts written (WATCHING): 3
  Shadow queue records:      41
  Eval report:               models/eval/eval_report.json
============================================================
```

---

## 9. Synthetic Data Generation Design (`scripts/generate_synthetic_data.py`)

### 9.1 Overview

The script generates three files:
- `data/raw/sample_readings.csv` — 432,000 rows of 15-minute interval meter readings
- `data/raw/sample_registry.csv` — 50 meter registry entries
- `data/raw/injected_events.json` — ground-truth labels for all injected patterns

Run via: `python scripts/generate_synthetic_data.py` or automatically by `run_pipeline.py` if files are absent.

---

### 9.2 Registry Generation

```python
import numpy as np
import pandas as pd
from datetime import date

RANDOM_SEED = 42
rng = np.random.default_rng(RANDOM_SEED)

# 5 geographic cluster centers in Bangalore area
CLUSTER_CENTERS = [
    (12.9716, 77.5946),  # Cluster 0 — Bangalore Central
    (12.9850, 77.6101),  # Cluster 1 — Indiranagar
    (12.9580, 77.6410),  # Cluster 2 — Koramangala
    (12.9352, 77.6245),  # Cluster 3 — BTM Layout
    (13.0100, 77.5500),  # Cluster 4 — Yeshwanthpur
]

FEEDERS = ["F001", "F002", "F003", "F004", "F005"]
TRANSFORMERS = {
    "F001": "T001", "F002": "T001",
    "F003": "T002", "F004": "T002", "F005": "T002",
}
TRANSFORMER_KVA = {"T001": 500.0, "T002": 750.0}
SANCTIONED_KVA_OPTIONS = [25.0, 50.0, 63.0, 100.0]
CONSUMER_CATEGORIES = ["RESIDENTIAL", "COMMERCIAL", "INDUSTRIAL"]

rows = []
for cluster_idx, (center_lat, center_lng) in enumerate(CLUSTER_CENTERS):
    feeder_id = FEEDERS[cluster_idx]
    transformer_id = TRANSFORMERS[feeder_id]
    for meter_num in range(10):
        meter_id = f"METER_{cluster_idx:02d}{meter_num:02d}"
        # Scatter meters within ~100m of cluster center
        lat = center_lat + rng.normal(0, 0.0005)   # ~55m per 0.0005 degrees
        lng = center_lng + rng.normal(0, 0.0005)
        rows.append({
            "meter_id": meter_id,
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "feeder_id": feeder_id,
            "transformer_id": transformer_id,
            "zone": f"ZONE_{cluster_idx + 1}",
            "consumer_category": rng.choice(CONSUMER_CATEGORIES, p=[0.7, 0.2, 0.1]),
            "sanctioned_kva": rng.choice(SANCTIONED_KVA_OPTIONS),
            "connection_date": date(2018 + rng.integers(0, 5), rng.integers(1, 13), rng.integers(1, 28)),
        })

# Override specific meter IDs for injected patterns
TAMPER_METERS = ["METER_T001", "METER_T002", "METER_T003", "METER_T004", "METER_T005"]
VACATION_METERS = ["METER_V001", "METER_V002"]
SHORT_GAP_METERS = ["METER_G001", "METER_G002", "METER_G003"]
EXTENDED_GAP_METERS = ["METER_G004", "METER_G005"]

# Assign special meter IDs to the first 12 meters of cluster 0
special_ids = TAMPER_METERS + VACATION_METERS + SHORT_GAP_METERS + EXTENDED_GAP_METERS
for i, special_id in enumerate(special_ids):
    rows[i]["meter_id"] = special_id

registry_df = pd.DataFrame(rows)
registry_df.to_csv("data/raw/sample_registry.csv", index=False)
```

---

### 9.3 Base Load Generation

```python
N_METERS = 50
N_DAYS = 90
FREQ = "15min"
START_DATE = "2024-01-01"

timestamps = pd.date_range(start=START_DATE, periods=N_DAYS * 96, freq=FREQ, tz="UTC")
# 90 days × 96 slots/day = 8,640 timestamps per meter
# 50 meters × 8,640 = 432,000 total rows

def hour_of_day_curve(hour: int) -> float:
    """Normalized load curve: peak 18-21h, trough 2-5h."""
    # Approximate residential load shape
    curve = [
        0.35, 0.30, 0.28, 0.27, 0.28, 0.32,  # 00-05
        0.45, 0.65, 0.75, 0.72, 0.68, 0.65,  # 06-11
        0.62, 0.60, 0.58, 0.60, 0.65, 0.75,  # 12-17
        0.90, 1.00, 0.98, 0.88, 0.70, 0.50,  # 18-23
    ]
    return curve[hour]

def day_of_week_factor(dow: int) -> float:
    """Weekday=1.0, weekend=0.8."""
    return 0.8 if dow >= 5 else 1.0

def seasonal_factor(month: int) -> float:
    """Summer +20%, winter -10%, spring/autumn baseline."""
    factors = {1: 0.90, 2: 0.90, 3: 1.00, 4: 1.00, 5: 1.10,
               6: 1.20, 7: 1.20, 8: 1.20, 9: 1.10, 10: 1.00,
               11: 0.95, 12: 0.90}
    return factors[month]

all_rows = []
for meter_row in registry_df.itertuples():
    meter_id = meter_row.meter_id
    base_mean = rng.normal(2.5, 0.3)  # kWh per 15-min slot, meter-specific mean
    base_mean = max(0.5, base_mean)   # Clamp to positive

    for ts in timestamps:
        hour = ts.hour
        dow = ts.dayofweek
        month = ts.month

        # Compute expected load
        expected = (
            base_mean
            * hour_of_day_curve(hour)
            * day_of_week_factor(dow)
            * seasonal_factor(month)
        )

        # Add noise (±15%)
        kwh = expected * rng.normal(1.0, 0.08)
        kwh = max(0.0, kwh)

        # Voltage: normal(230, 5), clamped 200-250V
        voltage = float(np.clip(rng.normal(230, 5), 200, 250))

        # Power factor: normal(0.92, 0.03), clamped 0.85-1.0
        pf = float(np.clip(rng.normal(0.92, 0.03), 0.85, 1.0))

        # Reactive power: derived from apparent power and power factor
        # apparent_power = kwh / pf; reactive = sqrt(apparent^2 - real^2)
        apparent = kwh / pf if pf > 0 else 0
        reactive = float(np.sqrt(max(0, apparent**2 - kwh**2)))

        all_rows.append({
            "meter_id": meter_id,
            "timestamp": ts.isoformat(),
            "kwh": round(kwh, 4),
            "voltage": round(voltage, 2),
            "power_factor": round(pf, 4),
            "reactive_power": round(reactive, 4),
        })

readings_df = pd.DataFrame(all_rows)
```

---

### 9.4 Injected Patterns

After generating the base load, apply the following modifications in-place:

#### Pattern 1: Tamper/Theft Meters (METER_T001–METER_T005)

```python
TAMPER_CONFIG = [
    {"meter_id": "METER_T001", "start_day": 60, "end_day": 90, "drop_pct": 0.88},
    {"meter_id": "METER_T002", "start_day": 63, "end_day": 90, "drop_pct": 0.85},
    {"meter_id": "METER_T003", "start_day": 65, "end_day": 90, "drop_pct": 0.90},
    {"meter_id": "METER_T004", "start_day": 67, "end_day": 90, "drop_pct": 0.87},
    {"meter_id": "METER_T005", "start_day": 70, "end_day": 90, "drop_pct": 0.86},
]

for cfg in TAMPER_CONFIG:
    mask = (
        (readings_df["meter_id"] == cfg["meter_id"]) &
        (readings_df["timestamp"] >= start_date + timedelta(days=cfg["start_day"])) &
        (readings_df["timestamp"] < start_date + timedelta(days=cfg["end_day"]))
    )
    # Daytime: drop to 5-15% of normal (≥80% drop)
    day_mask = mask & readings_df["timestamp"].dt.hour.between(5, 21)
    readings_df.loc[day_mask, "kwh"] *= (1 - cfg["drop_pct"])

    # Night: preserve 60-80% of normal (bypass pattern — meter bypassed but
    # some consumption still occurs at night, distinguishing from vacation)
    night_mask = mask & ~readings_df["timestamp"].dt.hour.between(5, 21)
    readings_df.loc[night_mask, "kwh"] *= rng.uniform(0.60, 0.80)

# confirmed_tamper label: 1 for tamper meters during tamper period
readings_df["confirmed_tamper"] = 0
for cfg in TAMPER_CONFIG:
    mask = (
        (readings_df["meter_id"] == cfg["meter_id"]) &
        (readings_df["timestamp"] >= start_date + timedelta(days=cfg["start_day"]))
    )
    readings_df.loc[mask, "confirmed_tamper"] = 1
```

#### Pattern 2: Grid Stress Events (Feeders F001, F002)

```python
STRESS_CONFIG = [
    {"feeder_id": "F001", "start_day": 75, "end_day": 77, "hours": (14, 20), "spike_factor": 1.5},
    {"feeder_id": "F002", "start_day": 82, "end_day": 84, "hours": (11, 17), "spike_factor": 1.4},
]

for cfg in STRESS_CONFIG:
    feeder_meters = registry_df[registry_df["feeder_id"] == cfg["feeder_id"]]["meter_id"].tolist()
    mask = (
        readings_df["meter_id"].isin(feeder_meters) &
        (readings_df["timestamp"] >= start_date + timedelta(days=cfg["start_day"])) &
        (readings_df["timestamp"] < start_date + timedelta(days=cfg["end_day"])) &
        readings_df["timestamp"].dt.hour.between(*cfg["hours"])
    )
    readings_df.loc[mask, "kwh"] *= cfg["spike_factor"]
    # Clamp to physically reasonable maximum
    readings_df.loc[mask, "kwh"] = readings_df.loc[mask, "kwh"].clip(upper=20.0)
```

#### Pattern 3: Vacation Meters (METER_V001, METER_V002)

```python
VACATION_CONFIG = [
    {"meter_id": "METER_V001", "start_day": 45, "end_day": 55},
    {"meter_id": "METER_V002", "start_day": 48, "end_day": 58},
]

for cfg in VACATION_CONFIG:
    mask = (
        (readings_df["meter_id"] == cfg["meter_id"]) &
        (readings_df["timestamp"] >= start_date + timedelta(days=cfg["start_day"])) &
        (readings_df["timestamp"] < start_date + timedelta(days=cfg["end_day"]))
    )
    # All hours drop to 5-10% of normal (including night — distinguishes from tamper)
    readings_df.loc[mask, "kwh"] *= rng.uniform(0.05, 0.10)
# confirmed_tamper = 0 for vacation meters (already default)
```

#### Pattern 4: Short-Gap Meters (METER_G001–METER_G003)

```python
SHORT_GAP_CONFIG = ["METER_G001", "METER_G002", "METER_G003"]

for meter_id in SHORT_GAP_CONFIG:
    # Pick a random timestamp in days 20-70 and null out 1-3 consecutive slots
    meter_mask = readings_df["meter_id"] == meter_id
    meter_indices = readings_df[meter_mask].index.tolist()
    gap_start_idx = rng.integers(len(meter_indices) // 4, len(meter_indices) // 2)
    gap_length = rng.integers(1, 4)  # 1, 2, or 3 slots
    gap_indices = meter_indices[gap_start_idx:gap_start_idx + gap_length]
    readings_df.loc[gap_indices, "kwh"] = np.nan
```

#### Pattern 5: Extended-Gap Meters (METER_G004–METER_G005)

```python
EXTENDED_GAP_CONFIG = ["METER_G004", "METER_G005"]

for meter_id in EXTENDED_GAP_CONFIG:
    meter_mask = readings_df["meter_id"] == meter_id
    meter_indices = readings_df[meter_mask].index.tolist()
    gap_start_idx = rng.integers(len(meter_indices) // 3, len(meter_indices) // 2)
    gap_length = rng.integers(4, 9)  # 4-8 slots
    gap_indices = meter_indices[gap_start_idx:gap_start_idx + gap_length]
    readings_df.loc[gap_indices, "kwh"] = np.nan
```

---

### 9.5 Output Files

```python
# Write readings CSV (drop confirmed_tamper — it's only used internally for training labels)
readings_df.drop(columns=["confirmed_tamper"]).to_csv(
    "data/raw/sample_readings.csv", index=False
)

# Write injected_events.json for ground truth
import json

injected_events = {
    "tamper_meters": [
        {"meter_id": cfg["meter_id"], "start_day": cfg["start_day"],
         "end_day": cfg["end_day"], "drop_pct": cfg["drop_pct"]}
        for cfg in TAMPER_CONFIG
    ],
    "stress_events": [
        {"feeder_id": cfg["feeder_id"], "start_day": cfg["start_day"],
         "end_day": cfg["end_day"], "hours": list(cfg["hours"]),
         "spike_factor": cfg["spike_factor"]}
        for cfg in STRESS_CONFIG
    ],
    "vacation_meters": [
        {"meter_id": cfg["meter_id"], "start_day": cfg["start_day"],
         "end_day": cfg["end_day"]}
        for cfg in VACATION_CONFIG
    ],
    "short_gap_meters": SHORT_GAP_CONFIG,
    "extended_gap_meters": EXTENDED_GAP_CONFIG,
}

with open("data/raw/injected_events.json", "w") as f:
    json.dump(injected_events, f, indent=2)

print(f"Generated {len(readings_df):,} reading rows across {N_METERS} meters.")
print(f"Tamper meters: {[c['meter_id'] for c in TAMPER_CONFIG]}")
print(f"Vacation meters: {[c['meter_id'] for c in VACATION_CONFIG]}")
print(f"Stress feeders: {[c['feeder_id'] for c in STRESS_CONFIG]}")
```

---

### 9.6 Expected Dataset Statistics

| Metric | Value |
|---|---|
| Total rows | 432,000 |
| Meters | 50 |
| Days | 90 |
| Slots per day per meter | 96 (15-min intervals) |
| Tamper meters (confirmed_tamper=1) | 5 |
| Vacation meters (drop, no tamper) | 2 |
| Stress feeder events | 2 (F001, F002) |
| Short-gap meters | 3 (1–3 NaN slots each) |
| Extended-gap meters | 2 (4–8 NaN slots each) |
| Expected alerts (NEW) | ~8 (tamper meters after repeat gate) |
| Expected alerts (WATCHING) | ~3 (tamper meters before repeat gate) |
| Expected shadow records | ~41 (sub-threshold candidates) |
| Geographic spread | ~1 km² in Bangalore area |
| Feeder utilization peak | >90% during stress events on F001, F002 |

---

### 9.7 `injected_events.json` Full Structure

```json
{
  "tamper_meters": [
    {"meter_id": "METER_T001", "start_day": 60, "end_day": 90, "drop_pct": 0.88},
    {"meter_id": "METER_T002", "start_day": 63, "end_day": 90, "drop_pct": 0.85},
    {"meter_id": "METER_T003", "start_day": 65, "end_day": 90, "drop_pct": 0.90},
    {"meter_id": "METER_T004", "start_day": 67, "end_day": 90, "drop_pct": 0.87},
    {"meter_id": "METER_T005", "start_day": 70, "end_day": 90, "drop_pct": 0.86}
  ],
  "stress_events": [
    {
      "feeder_id": "F001",
      "start_day": 75,
      "end_day": 77,
      "hours": [14, 20],
      "spike_factor": 1.5
    },
    {
      "feeder_id": "F002",
      "start_day": 82,
      "end_day": 84,
      "hours": [11, 17],
      "spike_factor": 1.4
    }
  ],
  "vacation_meters": [
    {"meter_id": "METER_V001", "start_day": 45, "end_day": 55},
    {"meter_id": "METER_V002", "start_day": 48, "end_day": 58}
  ],
  "short_gap_meters": ["METER_G001", "METER_G002", "METER_G003"],
  "extended_gap_meters": ["METER_G004", "METER_G005"]
}
```

---

## Appendix: `requirements.txt`

```
# Data pipeline
pandas==2.2.2
pyarrow==16.1.0
numpy==1.26.4
geopandas==0.14.4
shapely==2.0.4

# ML
scikit-learn==1.5.0
xgboost==2.0.3
lightgbm==4.3.0
shap==0.45.1
imbalanced-learn==0.12.3
joblib==1.4.2

# API
fastapi==0.111.0
uvicorn[standard]==0.30.1
sqlalchemy==2.0.30
aiosqlite==0.20.0
pydantic==2.7.1

# Testing
pytest==8.2.2
pytest-asyncio==0.23.7
httpx==0.27.0
```

## Appendix: `frontend/package.json` (key dependencies)

```json
{
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-leaflet": "^4.2.1",
    "leaflet": "^1.9.4",
    "recharts": "^2.12.7",
    "@tanstack/react-query": "^5.40.0",
    "zustand": "^4.5.2",
    "date-fns": "^3.6.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@types/leaflet": "^1.9.12",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.4.5",
    "vite": "^5.3.1",
    "tailwindcss": "^3.4.4",
    "autoprefixer": "^10.4.19",
    "postcss": "^8.4.38"
  }
}
```

## Appendix: `frontend/vite.config.ts`

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
```
