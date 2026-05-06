# Requirements Document

## Introduction

Synapse-Grid is a hackathon prototype of a proactive intelligence platform for BESCOM (Bangalore Electricity Supply Company) grid dispatchers. It ingests 15-minute interval smart meter AMI data from CSV files, detects load stress zones and anomalous consumption patterns (including theft/tamper), and presents actionable, explainable alerts to human dispatchers via a React dashboard. All data is processed locally; no external hosted models are used. No authentication, no background scheduling, and no database migrations are required — the prototype is operated via three CLI commands and a manual pipeline trigger.

The platform answers three operational questions:
1. **Where is the stress zone?** — 24-hour ahead load forecasting per feeder.
2. **Which meter looks off?** — Anomaly and theft detection with peer-context validation.
3. **What is the right action?** — Structured dispatch workflow with mandatory human confirmation.

---

## Glossary

- **AMI**: Advanced Metering Infrastructure — the smart meter network.
- **Alert_Queue**: The SQLite database table holding actionable alerts (confidence ≥ 90% + repeat gate met).
- **Shadow_Queue**: The SQLite database table holding sub-threshold alerts for model improvement only.
- **Anomaly_Confidence**: A float in [0, 1] produced by the Truth_Engine representing the probability that a meter's consumption pattern is anomalous.
- **Baseline**: The rolling 28-day median consumption for a given meter at a specific hour-of-day × day-of-week slot.
- **BESCOM**: Bangalore Electricity Supply Company — the operator and end user.
- **Cluster**: A group of meters sharing similar seasonal load shape, assigned by KMeans clustering.
- **Confidence_Gate**: The rule that only alerts with Anomaly_Confidence ≥ 0.90 are written to Alert_Queue.
- **Dispatcher**: A BESCOM grid dispatcher or circle-level officer — the primary user of the frontend. No login is required; whoever has the app open is the dispatcher.
- **Extended_Gap**: More than 3 consecutive missing 15-minute readings for a single meter.
- **Feature_Matrix**: The assembled per-meter per-timestamp table of all engineered features, persisted as a Parquet file.
- **Feeder**: A distribution line segment serving a group of meters, identified by feeder_id.
- **Glass_Box_View**: The mandatory alert detail view showing historical baseline chart, peer median, and SHAP top-3 plain-English explanations.
- **Hardware_Issue_Flag**: A record written when an Extended_Gap is detected, indicating a likely meter hardware fault.
- **Inference_Runner**: The batch process (`run_pipeline.py`) that executes the full ingestion → feature engineering → model inference pipeline end-to-end on the synthetic dataset when triggered manually.
- **KVA**: Kilovolt-ampere — unit of apparent power used for transformer capacity.
- **Load_Forecast_Model**: One XGBoost binary classifier per Cluster predicting is_high_stress_zone in the next 24 hours.
- **Meter_Registry**: The reference table of all meters with geographic, electrical, and administrative metadata.
- **Night_Activity_Score**: Mean consumption between 22:00–05:00 normalized to the meter's overall mean.
- **Peer_Deviation**: A condition where a meter's consumption drops ≥ 80% while ≥ 6 of its 8 nearest geographic neighbors are stable or rising.
- **Peer_Graph**: The adjacency dictionary mapping each meter_id to its list of neighbor meter_ids within 200 m radius.
- **Repeat_Pattern_Gate**: The rule requiring ≥ 2 consecutive anomaly days OR ≥ 3 of the last 5 days before an alert is promoted from WATCHING to actionable.
- **SHAP**: SHapley Additive exPlanations — the method used to attribute model predictions to individual features.
- **Short_Gap**: 1–3 consecutive missing 15-minute readings for a single meter.
- **Transformer**: Electrical transformer serving a group of feeders, identified by transformer_id.
- **Truth_Engine**: The LightGBM classifier that produces Anomaly_Confidence scores.
- **Zone**: An administrative grouping of feeders within BESCOM's service territory.

---

## Requirements

### Requirement 1: Smart Meter Data Ingestion

**User Story:** As a BESCOM data engineer, I want to ingest 15-minute interval smart meter readings from CSV files, so that the pipeline has validated, structured data to process.

#### Acceptance Criteria

1. THE Meter_Reader SHALL parse CSV files containing the columns: meter_id, timestamp, kwh, voltage, power_factor, reactive_power.
2. WHEN a CSV file is provided to the Meter_Reader, THE Meter_Reader SHALL validate that all six required columns are present before processing any rows.
3. IF a required column is absent from the input CSV, THEN THE Meter_Reader SHALL raise a descriptive schema validation error identifying the missing column name.
4. WHEN a CSV file passes schema validation, THE Meter_Reader SHALL parse each row into a structured reading record with typed fields (meter_id as string, timestamp as datetime, kwh/voltage/power_factor/reactive_power as float).
5. THE Meter_Reader SHALL support reading multiple CSV files in a single invocation, concatenating results into a single DataFrame.

---

### Requirement 2: Meter Registry Loading

**User Story:** As a BESCOM data engineer, I want to load and serve the meter registry, so that all pipeline stages have access to meter metadata including geographic coordinates and electrical topology.

#### Acceptance Criteria

1. THE Meter_Registry SHALL load a CSV file containing the columns: meter_id, lat, lng, feeder_id, transformer_id, zone, consumer_category, sanctioned_kva, connection_date.
2. WHEN the registry CSV is loaded, THE Meter_Registry SHALL validate that all nine required columns are present.
3. IF a required registry column is absent, THEN THE Meter_Registry SHALL raise a descriptive schema validation error.
4. THE Meter_Registry SHALL expose a lookup interface that returns the full metadata record for a given meter_id in O(1) time.
5. WHEN the registry is loaded, THE Meter_Registry SHALL parse sanctioned_kva as a positive float and connection_date as a date.

---

### Requirement 3: Geographic Peer Graph Construction

**User Story:** As a data engineer, I want to build a peer graph of geographically proximate meters, so that anomaly detection can compare a meter's consumption against its neighbors.

#### Acceptance Criteria

1. WHEN the Peer_Graph_Builder is invoked with the Meter_Registry, THE Peer_Graph_Builder SHALL compute, for each meter, all other meters whose geographic distance is ≤ 200 meters using the Haversine formula on lat/lng coordinates.
2. THE Peer_Graph_Builder SHALL store the result as an adjacency dictionary with structure `{meter_id: [neighbor_meter_id, ...]}` where the neighbor list contains only meters within the 200 m radius.
3. WHEN the peer graph is computed, THE Peer_Graph_Builder SHALL persist it to `data/processed/peer_graph.json`.
4. THE Peer_Graph_Builder SHALL be idempotent: re-running on the same registry input SHALL overwrite `peer_graph.json` with an identical result.
5. WHERE a meter has no neighbors within 200 m, THE Peer_Graph_Builder SHALL store an empty list for that meter_id in the adjacency dictionary.

---

### Requirement 4: Data Gap Imputation and Hardware Issue Flagging

**User Story:** As a data engineer, I want missing meter readings to be handled systematically, so that feature engineering always operates on complete, gap-free data.

#### Acceptance Criteria

1. WHEN the Gap_Handler detects 1–3 consecutive missing readings for a meter at a given time slot, THE Gap_Handler SHALL impute each missing value using the rolling 7-day same-slot median for that meter (same hour-of-day and day-of-week).
2. WHEN the Gap_Handler detects more than 3 consecutive missing readings for a meter, THE Gap_Handler SHALL flag the affected meter and time range as HARDWARE_ISSUE and SHALL NOT impute any values in that range.
3. WHEN a HARDWARE_ISSUE is flagged, THE Gap_Handler SHALL write a record to `data/processed/hardware_issue_flags.csv` containing: meter_id, gap_start, gap_end, gap_length_slots.
4. WHEN a meter's reading series has no gaps, THE Gap_Handler SHALL leave the series unchanged.
5. THE Gap_Handler SHALL be idempotent: re-running on the same input SHALL produce identical output files.
6. WHEN the 7-day same-slot history contains fewer than 3 valid values for a Short_Gap slot, THE Gap_Handler SHALL use the available values' median rather than skipping imputation.

---

### Requirement 5: Data Quality Validation

**User Story:** As a data engineer, I want all ingested readings to be validated against domain rules, so that downstream models are not trained or scored on physically impossible values.

#### Acceptance Criteria

1. WHEN the Validator processes a reading, THE Validator SHALL flag any record where kwh < 0 as a quality violation.
2. WHEN the Validator processes readings for a meter, THE Validator SHALL flag any record where the timestamp is not strictly monotonically increasing relative to the previous reading for that meter.
3. WHEN the Validator processes a reading, THE Validator SHALL flag any record where voltage < 180 V or voltage > 260 V as a quality violation, but SHALL NOT drop the record.
4. WHEN the Validator processes a reading, THE Validator SHALL flag any record where power_factor < 0 or power_factor > 1 as a quality violation.
5. WHEN a quality violation is detected, THE Validator SHALL log the violation to `data/processed/data_quality_log.db` with fields: meter_id, timestamp, violation_type, field_name, observed_value.
6. THE Validator SHALL process all records and log all violations in a single pass without halting on the first violation.

---

### Requirement 6: Consumption Baseline Computation

**User Story:** As a data scientist, I want a rolling 28-day median baseline for each meter at each hour-of-day × day-of-week slot, so that deviations can be measured against a meter's own historical normal.

#### Acceptance Criteria

1. THE Baseline_Computer SHALL compute, for each meter, the rolling 28-day median consumption for each of the 168 slots defined by the combination of hour-of-day (0–23) and day-of-week (0–6).
2. WHEN the baseline is computed, THE Baseline_Computer SHALL persist the result to `data/processed/baseline_lookup.parquet` with columns: meter_id, hour_of_day, day_of_week, baseline_kwh.
3. THE Baseline_Computer SHALL use only data from the 28 days preceding each target timestamp to prevent data leakage.
4. THE Baseline_Computer SHALL be idempotent: re-running on the same input SHALL produce an identical Parquet file.

---

### Requirement 7: Deviation Feature Computation

**User Story:** As a data scientist, I want deviation metrics computed for each meter reading, so that the Truth_Engine has quantitative signals of abnormal consumption.

#### Acceptance Criteria

1. THE Deviation_Computer SHALL compute pct_deviation_from_baseline as `(actual_kwh - baseline_kwh) / baseline_kwh × 100` for each meter reading.
2. THE Deviation_Computer SHALL compute z_score as `(actual_kwh - rolling_28d_mean) / rolling_28d_std` for each meter reading.
3. THE Deviation_Computer SHALL compute peer_deviation_score as the deviation of a meter's consumption from the median consumption of its Peer_Graph neighbors at the same timestamp.
4. WHEN a meter's consumption drops ≥ 80% below its baseline AND ≥ 6 of its 8 nearest Peer_Graph neighbors are stable or rising at the same timestamp, THE Deviation_Computer SHALL set the peer_deviation flag to True for that reading.
5. WHERE a meter has fewer than 8 Peer_Graph neighbors, THE Deviation_Computer SHALL apply the peer_deviation flag rule using all available neighbors, requiring ≥ 75% of available neighbors to be stable or rising.

---

### Requirement 8: Temporal Lag Feature Engineering

**User Story:** As a data scientist, I want lag and rolling statistical features, so that the models can detect patterns that persist or evolve over time.

#### Acceptance Criteria

1. THE Lag_Feature_Builder SHALL compute lag features for each meter reading: T-1h (4 slots back), T-24h (96 slots back), T-48h (192 slots back), T-7d (672 slots back).
2. THE Lag_Feature_Builder SHALL compute a 7-day rolling mean and 7-day rolling standard deviation for each meter.
3. THE Lag_Feature_Builder SHALL compute a 3-day trend slope using linear regression over the 3-day window of same-slot readings.
4. WHEN insufficient history exists for a lag feature, THE Lag_Feature_Builder SHALL populate the field with NaN rather than raising an error.

---

### Requirement 9: Pattern Fingerprint Features

**User Story:** As a data scientist, I want vacation/bypass disambiguation features computed during feature engineering, so that the Truth_Engine can distinguish legitimate consumption drops from theft-related bypasses.

#### Acceptance Criteria

1. THE Pattern_Fingerprint_Computer SHALL compute is_sustained_multiday_drop as True when a meter's consumption is ≥ 50% below its baseline for ≥ 3 consecutive days.
2. THE Pattern_Fingerprint_Computer SHALL compute is_recurring_daily_pattern as True when a consumption dip repeats at consistent hours on ≥ 3 of the last 5 days for a given meter.
3. THE Pattern_Fingerprint_Computer SHALL compute night_activity_score as the mean consumption between 22:00–05:00 normalized by the meter's overall mean consumption.
4. WHEN a meter shows a vacation pattern (sustained multiday drop with low night activity), THE Pattern_Fingerprint_Computer SHALL set is_recurring_daily_pattern to False for that period.
5. WHEN a meter shows a bypass pattern (recurring dip at consistent hours with non-zero night activity), THE Pattern_Fingerprint_Computer SHALL set is_recurring_daily_pattern to True.

---

### Requirement 10: Seasonal Clustering

**User Story:** As a data scientist, I want meters grouped into seasonal load shape clusters, so that the Load_Forecast_Model can be trained on homogeneous consumption profiles.

#### Acceptance Criteria

1. THE Seasonal_Clusterer SHALL fit a KMeans model with k=8 on month × hour load shape vectors derived from meter consumption data.
2. WHEN clustering is complete, THE Seasonal_Clusterer SHALL assign a cluster_id (integer 0–7) to each meter.
3. THE Seasonal_Clusterer SHALL persist cluster assignments to `data/processed/cluster_assignments.csv` with columns: meter_id, cluster_id.
4. THE Seasonal_Clusterer SHALL persist the cluster load shape profiles to `data/processed/seasonal_profiles.json`.
5. THE Seasonal_Clusterer SHALL be idempotent: re-running on the same input SHALL produce identical output files.

---

### Requirement 11: Zone and Feeder Profile Aggregation

**User Story:** As a data scientist, I want per-feeder hourly load profiles and stress metrics, so that the Load_Forecast_Model has zone-level context features.

#### Acceptance Criteria

1. THE Zone_Profile_Aggregator SHALL compute per-feeder hourly load profiles by summing meter loads grouped by feeder_id and timestamp.
2. THE Zone_Profile_Aggregator SHALL compute feeder_stress as `sum(meter_loads_at_timestamp) / transformer_rated_kva` for each feeder at each timestamp.
3. WHEN feeder_stress > 0.90, THE Zone_Profile_Aggregator SHALL set is_high_stress_zone to True for that feeder and timestamp.
4. THE Zone_Profile_Aggregator SHALL persist zone statistics to `data/processed/zone_profiles.parquet`.

---

### Requirement 12: Feature Matrix Assembly

**User Story:** As a data scientist, I want all features joined into a single matrix per meter per timestamp, so that model training and inference have a single, consistent input source.

#### Acceptance Criteria

1. THE Feature_Matrix_Builder SHALL join baseline features, deviation features, lag features, pattern fingerprint features, cluster assignments, and zone profile features on (meter_id, timestamp).
2. WHEN the feature matrix is assembled, THE Feature_Matrix_Builder SHALL persist it to `data/processed/feature_matrix.parquet`.
3. THE Feature_Matrix_Builder SHALL be idempotent: re-running on the same inputs SHALL produce an identical Parquet file.
4. IF any required feature source file is missing, THEN THE Feature_Matrix_Builder SHALL raise a descriptive error identifying the missing file.

---

### Requirement 13: Load Forecast Model Training

**User Story:** As a data scientist, I want one XGBoost classifier trained per cluster, so that load stress predictions are calibrated to each cluster's consumption profile.

#### Acceptance Criteria

1. THE Load_Forecast_Trainer SHALL train one XGBoost binary classifier per cluster_id on the Feature_Matrix, with target label is_high_stress_zone (feeder utilization > 90% in the next 24 hours).
2. THE Load_Forecast_Trainer SHALL use TimeSeriesSplit for cross-validation and SHALL NOT shuffle temporal data.
3. THE Load_Forecast_Trainer SHALL set scale_pos_weight to address class imbalance in the training split.
4. WHEN training is complete, THE Load_Forecast_Trainer SHALL save each model to `models/load_forecast/xgb_{cluster_id}_v1_{YYYYMMDD}.joblib`.
5. THE Load_Forecast_Trainer SHALL apply SMOTE only on the training split, never on the validation or test split.

---

### Requirement 14: Truth Engine Model Training

**User Story:** As a data scientist, I want a LightGBM classifier trained to produce anomaly confidence scores, so that the system can rank meters by theft/tamper likelihood.

#### Acceptance Criteria

1. THE Truth_Engine_Trainer SHALL train a LightGBM classifier with target label confirmed_tamper using the following features: z_score, peer_deviation_score, is_sustained_multiday_drop, is_recurring_daily_pattern, night_activity_score, pct_deviation_from_baseline, pct_deviation_from_peer_median, pct_deviation_from_cluster_norm, and all lag features.
2. THE Truth_Engine_Trainer SHALL use TimeSeriesSplit and SHALL NOT shuffle temporal data.
3. WHEN training is complete, THE Truth_Engine_Trainer SHALL save the model to `models/truth_engine/lgbm_v1_{YYYYMMDD}.joblib`.

---

### Requirement 15: Alert Confidence Gating and Queue Writing

**User Story:** As a BESCOM dispatcher, I want only high-confidence, repeat-pattern anomalies surfaced as actionable alerts, so that I am not overwhelmed by false positives.

#### Acceptance Criteria

1. WHEN the Truth_Engine_Scorer produces an Anomaly_Confidence ≥ 0.90 AND the Repeat_Pattern_Gate is met (≥ 2 consecutive anomaly days OR ≥ 3 of the last 5 days), THE Truth_Engine_Scorer SHALL write the alert to `alert_queue.db` with state=NEW.
2. WHEN the Truth_Engine_Scorer produces an Anomaly_Confidence ≥ 0.90 AND the Repeat_Pattern_Gate is NOT met, THE Truth_Engine_Scorer SHALL write the alert to `alert_queue.db` with state=WATCHING.
3. WHEN the Truth_Engine_Scorer produces an Anomaly_Confidence < 0.90, THE Truth_Engine_Scorer SHALL write the alert to `shadow_queue.db` only and SHALL NOT write to `alert_queue.db`.
4. THE Truth_Engine_Scorer SHALL enforce the confidence gate and repeat-pattern gate in the alert writer, not in the frontend or API layer.
5. WHEN an alert is written to `alert_queue.db`, THE Truth_Engine_Scorer SHALL include all fields defined in the canonical Alert Object Schema.

---

### Requirement 16: SHAP Explainability

**User Story:** As a BESCOM dispatcher, I want every alert to include a plain-English explanation of the top contributing factors, so that I can make an informed dispatch decision without being a data scientist.

#### Acceptance Criteria

1. WHEN an alert is written to `alert_queue.db`, THE SHAP_Explainer SHALL compute TreeExplainer SHAP values for that alert's feature vector using the Truth_Engine model.
2. THE SHAP_Explainer SHALL extract the top-3 features by absolute SHAP value for each alert.
3. THE SHAP_Explainer SHALL generate a plain_english string for each top feature describing the deviation in human-readable terms (e.g., "Consumption is 74% below this meter's 28-day Monday evening average").
4. THE SHAP_Explainer SHALL write the shap_top3 array to the alert record in `alert_queue.db` at alert write time.
5. THE SHAP_Explainer SHALL NOT recompute SHAP values on frontend request; values are served statically from the database.

---

### Requirement 17: Model Evaluation

**User Story:** As a data scientist, I want machine-readable evaluation metrics for both models, so that I can track model performance and detect degradation.

#### Acceptance Criteria

1. THE Model_Evaluator SHALL compute precision, recall, F1, and AUC for both the Load_Forecast_Model and the Truth_Engine against a held-out temporal test split.
2. WHEN evaluation is complete, THE Model_Evaluator SHALL write results to `models/eval/eval_report.json` with per-model metrics.
3. THE Model_Evaluator SHALL save a confusion matrix plot to `models/eval/confusion_matrix.png`.
4. THE Model_Evaluator SHALL use a temporal test split (no shuffling) that is strictly later in time than the training data.

---

### Requirement 18: Manual Pipeline CLI Trigger

**User Story:** As a hackathon demonstrator, I want a single CLI command that runs the full pipeline end-to-end on the synthetic dataset, so that the demo can be set up with one command and judges can see the system working immediately.

#### Acceptance Criteria

1. THE `run_pipeline.py` script SHALL execute the full pipeline in sequence when invoked: load synthetic CSV data → validate → impute gaps → build peer graph → compute features → train models → run inference → write alerts to `alert_queue.db`.
2. WHEN `run_pipeline.py` completes successfully, THE script SHALL print a summary to stdout: number of meters processed, number of alerts written to alert_queue, number of alerts in WATCHING state, and number of records written to shadow_queue.
3. THE `run_pipeline.py` script SHALL be idempotent: re-running on the same synthetic dataset SHALL produce identical alert records without duplicating existing alerts.
4. IF any pipeline stage fails, THEN THE `run_pipeline.py` script SHALL print a descriptive error message identifying the failing stage and exit with a non-zero status code without writing partial results.
5. THE `run_pipeline.py` script SHALL accept an optional `--data-dir` argument defaulting to `data/raw/` to specify the input CSV directory.

---

### Requirement 19: Alerts API

**User Story:** As a frontend developer, I want a REST API for alerts, so that the dashboard can display and act on the current alert queue.

#### Acceptance Criteria

1. THE Alerts_API SHALL expose GET /api/v1/alerts returning all alerts from `alert_queue.db`, filterable by state, alert_type, and feeder_id, sorted by anomaly_confidence descending.
2. THE Alerts_API SHALL expose GET /api/v1/alerts/{alert_id} returning the full alert detail including shap_top3 and peer_status_summary.
3. THE Alerts_API SHALL expose PATCH /api/v1/alerts/{alert_id}/action accepting action values DISPATCH_LINEMAN, LOAD_BALANCE, or DISMISS.
4. WHEN action=DISMISS is submitted, THE Alerts_API SHALL require a non-empty reason_code field; IF reason_code is absent, THEN THE Alerts_API SHALL return HTTP 422.
5. WHEN a dispatch action is accepted, THE Alerts_API SHALL write the action to `dispatch_audit_log` with fields: alert_id, action, reason_code, resolver_id, resolved_at.
6. WHEN an alert has been DISPATCHED or DISMISSED, THE Alerts_API SHALL reject further action requests for that alert with HTTP 409.

---

### Requirement 20: Meters API

**User Story:** As a frontend developer, I want a REST API for meter data, so that the Glass_Box_View can display historical readings, baselines, and peer context.

#### Acceptance Criteria

1. THE Meters_API SHALL expose GET /api/v1/meters/{meter_id}/readings returning the last 14 days of readings for the specified meter.
2. THE Meters_API SHALL expose GET /api/v1/meters/{meter_id}/baseline returning the 28-day rolling baseline for the specified meter.
3. THE Meters_API SHALL expose GET /api/v1/meters/{meter_id}/peers returning the peer meter IDs and their current consumption status (normal/elevated/anomalous).

---

### Requirement 21: Feeders API

**User Story:** As a frontend developer, I want a REST API for feeder status, so that the Feeder Stress Map can display real-time utilization and 24-hour forecasts.

#### Acceptance Criteria

1. THE Feeders_API SHALL expose GET /api/v1/feeders returning all feeders with: feeder_id, current utilization percentage, stress_level (GREEN/AMBER/RED), and 24-hour load forecast.
2. WHEN feeder utilization ≤ 70%, THE Feeders_API SHALL set stress_level=GREEN.
3. WHEN feeder utilization > 70% and ≤ 90%, THE Feeders_API SHALL set stress_level=AMBER.
4. WHEN feeder utilization > 90%, THE Feeders_API SHALL set stress_level=RED.

---

### Requirement 22: Database Initialization

**User Story:** As a developer, I want the SQLite database schema created automatically on API startup, so that there is no migration setup step and the API is ready to serve immediately after `uvicorn api.main:app` is run.

#### Acceptance Criteria

1. WHEN the FastAPI application starts, THE Database_Initializer SHALL call SQLAlchemy `create_all()` to create all required tables in the SQLite database if they do not already exist.
2. THE Database_Initializer SHALL use SQLite as the only supported database for the prototype; no PostgreSQL connection string or Alembic migration history is required.
3. WHEN `create_all()` is called on a database that already has the correct schema, THE Database_Initializer SHALL leave existing data and tables unchanged.
4. THE database file path SHALL default to `data/synapse_grid.db` and SHALL be configurable via a `DATABASE_URL` environment variable.

---

### Requirement 23: Feeder Stress Map

**User Story:** As a BESCOM dispatcher, I want a geographic choropleth map of feeder stress levels, so that I can immediately identify which zones require attention.

#### Acceptance Criteria

1. THE Feeder_Stress_Map SHALL render a react-leaflet choropleth map coloring each feeder zone by stress_level (GREEN/AMBER/RED).
2. WHEN a feeder zone is clicked, THE Feeder_Stress_Map SHALL highlight the corresponding alerts in the Alert Queue panel.
3. THE Feeder_Stress_Map SHALL refresh feeder data every 5 minutes via the useFeederStatus hook.

---

### Requirement 24: Alert Queue Panel

**User Story:** As a BESCOM dispatcher, I want a sorted list of actionable alerts, so that I can triage the highest-confidence anomalies first.

#### Acceptance Criteria

1. THE Alert_Queue_Panel SHALL display all alerts from `alert_queue.db` sorted by anomaly_confidence descending.
2. THE Alert_Queue_Panel SHALL refresh alert data every 5 minutes via the useAlertQueue hook.
3. WHEN an alert is selected, THE Alert_Queue_Panel SHALL load the full alert detail in the Alert Detail panel.
4. THE Alert_Queue_Panel SHALL visually distinguish alert states: NEW, UNDER_REVIEW, WATCHING, DISPATCHED, DISMISSED.

---

### Requirement 25: Glass-Box Alert Detail View

**User Story:** As a BESCOM dispatcher, I want a mandatory Glass-Box view for every alert showing historical context and explanations, so that I can make an informed dispatch decision.

#### Acceptance Criteria

1. THE Alert_Detail_View SHALL display a 14-day Recharts LineChart showing actual consumption, baseline, and peer median for the alerted meter.
2. THE Alert_Detail_View SHALL display a deviation metrics card showing pct_deviation_from_baseline, pct_deviation_from_peer_median, z_score, and anomaly_confidence as percentages or plain numbers.
3. THE Alert_Detail_View SHALL display a SHAP explanation card showing the top-3 contributing features in plain English.
4. THE Alert_Detail_View SHALL display a neighborhood mini-map showing the meter's location and the status of its Peer_Graph neighbors.
5. THE Alert_Detail_View SHALL be shown for every alert without exception; there is no path to the Dispatch_Action_Panel that bypasses the Glass_Box_View.

---

### Requirement 26: Dispatch Action Panel

**User Story:** As a BESCOM dispatcher, I want a structured action panel requiring explicit confirmation, so that no alert is silently auto-closed and every dispatch decision is auditable.

#### Acceptance Criteria

1. THE Dispatch_Action_Panel SHALL present exactly three action buttons: DISPATCH LINEMAN, LOAD BALANCE, and DISMISS.
2. WHEN DISMISS is selected, THE Dispatch_Action_Panel SHALL require the dispatcher to select a reason code from: VACATION, PLANNED_OUTAGE, FALSE_POSITIVE, OTHER before the action can be submitted; the submit button SHALL remain disabled until a reason code is selected.
3. WHEN any action is selected, THE Dispatch_Action_Panel SHALL display a confirmation modal before submitting the action to the API.
4. WHEN an action is successfully submitted, THE Dispatch_Action_Panel SHALL display the resolution timestamp and resolver identity.
5. WHEN an alert is in DISPATCHED or DISMISSED state, THE Dispatch_Action_Panel SHALL disable all action buttons and display the final resolution details.
6. THE Dispatch_Action_Panel SHALL NOT allow any action to be submitted without an explicit dispatcher selection; there is no auto-close or default action.

---

### Requirement 27: Alert State Machine

**User Story:** As a BESCOM dispatcher, I want alert states to transition correctly through the workflow, so that the system accurately reflects the operational status of each alert.

#### Acceptance Criteria

1. THE Alert_State_Machine SHALL support the following states: NEW, UNDER_REVIEW, WATCHING, DISPATCHED, DISMISSED.
2. WHEN a dispatcher opens an alert detail, THE Alert_State_Machine SHALL transition the alert from NEW to UNDER_REVIEW.
3. WHEN a dispatcher submits DISPATCH_LINEMAN or LOAD_BALANCE, THE Alert_State_Machine SHALL transition the alert to DISPATCHED.
4. WHEN a dispatcher submits DISMISS with a reason code, THE Alert_State_Machine SHALL transition the alert to DISMISSED.
5. WHEN an alert reaches DISPATCHED or DISMISSED state, THE Alert_State_Machine SHALL prevent any further state transitions for that alert.
6. THE Alert_State_Machine SHALL be implemented in alertStore.ts using Zustand and SHALL sync state transitions with the backend via React Query.

---

### Requirement 28: Synthetic Sample Data Generation

**User Story:** As a hackathon demonstrator, I want synthetic sample data with injected anomaly events pre-loaded in the repository, so that all three demo scenarios work end-to-end without any real BESCOM data.

#### Acceptance Criteria

1. THE Sample_Data_Generator SHALL produce `data/raw/sample_readings.csv` containing readings for 50 meters over 90 days at 15-minute intervals (50 × 90 × 96 = 432,000 rows).
2. THE Sample_Data_Generator SHALL produce `data/raw/sample_registry.csv` containing registry records for all 50 meters with valid lat/lng coordinates clustered in groups to ensure peer neighbors exist within 200 m, plus feeder_id, transformer_id, zone, consumer_category, sanctioned_kva, and connection_date.
3. WHEN sample readings are generated, THE Sample_Data_Generator SHALL include realistic variation: daily load curves, weekend vs weekday patterns, and seasonal amplitude variation.
4. THE Sample_Data_Generator SHALL inject exactly 5 known tamper/theft patterns into 5 distinct meters: each pattern SHALL show a sustained consumption drop of ≥ 80% persisting for ≥ 3 consecutive days with non-zero night_activity_score (bypass signature).
5. THE Sample_Data_Generator SHALL inject exactly 2 grid stress events into 2 distinct feeders: each event SHALL show feeder utilization exceeding 90% of transformer_rated_kva for ≥ 6 consecutive hours.
6. THE Sample_Data_Generator SHALL include at least 3 meters with Short_Gap patterns (1–3 consecutive missing slots) and at least 2 meters with Extended_Gap patterns (> 3 consecutive missing slots).
7. THE Sample_Data_Generator SHALL include at least 2 meters with vacation patterns (sustained multiday drop with near-zero night_activity_score) that are distinct from the 5 tamper meters, to enable vacation vs bypass disambiguation in the demo.
8. THE injected anomaly meter IDs and event timestamps SHALL be documented in `data/raw/injected_events.json` so demo scripts can reference them by name.

---

### Requirement 29: Documentation and Demo Scripts

**User Story:** As a hackathon demonstrator, I want a README and demo scripts that let a judge reproduce the full demo in under 5 minutes, so that the prototype can be evaluated live without setup friction.

#### Acceptance Criteria

1. THE README SHALL provide step-by-step setup instructions covering: Python environment setup, `pip install -r requirements.txt`, running `python run_pipeline.py` to load data and train models, starting the API with `uvicorn api.main:app --reload`, and starting the frontend with `npm run dev`.
2. THE README SHALL document the three one-command entry points: pipeline trigger, API start, frontend start.
3. THE Demo_Scenarios document (`demo/scenarios.md`) SHALL contain three complete walkthrough scripts: (1) meter theft detection — navigate to a tamper-flagged meter, read the SHAP explanation, dispatch a lineman; (2) vacation vs bypass disambiguation — compare two meters with similar drop patterns but different night_activity_scores and pattern_type badges; (3) 24-hour load spike prediction — identify a RED feeder on the stress map and trace it to the load forecast alert.
4. THE Ablation_Notes document (`eval/ablation_notes.md`) SHALL describe two ablation studies: (1) peer comparison vs baseline-alone — remove peer_deviation_score and measure precision/recall change; (2) per-cluster models vs global model — train a single global XGBoost model and compare AUC against the per-cluster ensemble.
