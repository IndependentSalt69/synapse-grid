#!/usr/bin/env python3
"""
run_pipeline.py — Synapse-Grid full pipeline CLI trigger.

Runs the complete pipeline end-to-end on the synthetic dataset:
  ingest → validate → impute → peer graph → features → cluster →
  train models → inference → evaluate

Usage:
    python run_pipeline.py [--data-dir data/raw] [--force]

Options:
    --data-dir PATH   Input CSV directory (default: data/raw)
    --force           Force re-run all stages even if outputs are up to date
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Idempotency helper
# ---------------------------------------------------------------------------

def _is_output_fresh(output_path: str, *input_paths: str) -> bool:
    """
    Return True if output_path exists AND is newer than all input_paths.
    Returns False if output is missing or any input is newer.
    """
    if not Path(output_path).exists():
        return False
    output_mtime = os.path.getmtime(output_path)
    for inp in input_paths:
        if Path(inp).exists() and os.path.getmtime(inp) > output_mtime:
            return False
    return True


# ---------------------------------------------------------------------------
# Stage functions
# ---------------------------------------------------------------------------

def stage_check_synthetic_data(data_dir: str, force: bool) -> dict:
    """Ensure synthetic data files exist; generate if missing."""
    readings_path = f"{data_dir}/sample_readings.csv"
    registry_path = f"{data_dir}/sample_registry.csv"
    events_path = f"{data_dir}/injected_events.json"

    if not force and all(Path(p).exists() for p in [readings_path, registry_path, events_path]):
        return {"skipped": True}

    from scripts.generate_synthetic_data import main as gen_main
    gen_main()
    return {"skipped": False}


def stage_ingest(data_dir: str, force: bool) -> dict:
    """Load readings and registry CSVs into memory."""
    from pipeline.ingest.meter_reader import load_readings
    from pipeline.ingest.meter_registry import load_registry

    readings_path = f"{data_dir}/sample_readings.csv"
    registry_path = f"{data_dir}/sample_registry.csv"

    readings_df = load_readings(readings_path)
    registry_df, registry_dict = load_registry(registry_path)

    # Cache in module-level state for downstream stages
    _state["readings_df"] = readings_df
    _state["registry_df"] = registry_df
    _state["registry_dict"] = registry_dict
    return {"skipped": False, "n_meters": readings_df["meter_id"].nunique()}


def stage_validate(data_dir: str, force: bool) -> dict:
    """Validate readings and log violations."""
    from pipeline.ingest.validator import validate_readings
    db_path = "data/processed/data_quality_log.db"
    Path("data/processed").mkdir(parents=True, exist_ok=True)
    _state["readings_df"] = validate_readings(_state["readings_df"], db_path=db_path)
    return {"skipped": False}


def stage_impute(data_dir: str, force: bool) -> dict:
    """Impute short gaps; flag extended gaps as hardware issues."""
    flags_path = "data/processed/hardware_issue_flags.csv"
    if not force and _is_output_fresh(flags_path, f"{data_dir}/sample_readings.csv"):
        return {"skipped": True}

    from pipeline.impute.gap_handler import handle_gaps
    _state["readings_df"] = handle_gaps(_state["readings_df"], flags_path=flags_path)
    return {"skipped": False}


def stage_write_to_db(data_dir: str, force: bool) -> dict:
    """
    Write imputed readings and registry to the SQLite database.

    Populates:
    - meter_readings table  (used by GET /api/v1/meters/{id}/readings)
    - meter_registry_cache  (used by GET /api/v1/meters/{id}/peers)

    Uses a synchronous SQLAlchemy engine so the pipeline stays free of
    async/event-loop complexity. The DB file is the same one the API uses.
    """
    import sqlite3
    import pandas as pd
    from pathlib import Path

    db_path = "data/synapse_grid.db"
    Path("data").mkdir(parents=True, exist_ok=True)

    readings_df: pd.DataFrame = _state["readings_df"]
    registry_df: pd.DataFrame = _state["registry_df"]

    conn = sqlite3.connect(db_path)

    # ------------------------------------------------------------------ #
    # Ensure tables exist (mirrors the SQLAlchemy ORM schema)             #
    # ------------------------------------------------------------------ #
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meter_readings (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            meter_id       TEXT    NOT NULL,
            timestamp      TEXT    NOT NULL,
            kwh            REAL,
            voltage        REAL,
            power_factor   REAL,
            reactive_power REAL
        );
        CREATE INDEX IF NOT EXISTS idx_meter_readings_meter_ts
            ON meter_readings (meter_id, timestamp);

        CREATE TABLE IF NOT EXISTS meter_registry_cache (
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
    """)

    # ------------------------------------------------------------------ #
    # Write meter_readings (replace existing rows for idempotency)        #
    # ------------------------------------------------------------------ #
    # Normalise timestamp to ISO8601 string
    readings_out = readings_df[
        ["meter_id", "timestamp", "kwh", "voltage", "power_factor", "reactive_power"]
    ].copy()
    readings_out["timestamp"] = readings_out["timestamp"].astype(str)

    # Delete existing rows then bulk-insert (idempotent re-run)
    conn.execute("DELETE FROM meter_readings")
    readings_out.to_sql(
        "meter_readings",
        conn,
        if_exists="append",
        index=False,
        chunksize=10_000,
    )
    n_readings = len(readings_out)

    # ------------------------------------------------------------------ #
    # Write meter_registry_cache                                          #
    # ------------------------------------------------------------------ #
    registry_out = registry_df[
        ["meter_id", "lat", "lng", "feeder_id", "transformer_id",
         "zone", "consumer_category", "sanctioned_kva", "connection_date"]
    ].copy()
    registry_out["connection_date"] = registry_out["connection_date"].astype(str)

    registry_out.to_sql(
        "meter_registry_cache",
        conn,
        if_exists="replace",
        index=False,
    )

    conn.commit()
    conn.close()

    print(
        f"  → Wrote {n_readings:,} readings and "
        f"{len(registry_out)} registry entries to {db_path}"
    )
    return {"skipped": False, "n_readings": n_readings}


def stage_peer_graph(data_dir: str, force: bool) -> dict:
    """Build geographic peer graph."""
    output_path = "data/processed/peer_graph.json"
    if not force and _is_output_fresh(output_path, f"{data_dir}/sample_registry.csv"):
        return {"skipped": True}

    from pipeline.peer_graph.builder import build_peer_graph
    _state["peer_graph"] = build_peer_graph(_state["registry_df"], output_path=output_path)
    return {"skipped": False}
    

def stage_baseline(data_dir: str, force: bool) -> dict:
    """Compute rolling 28-day median baseline."""
    output_path = "data/processed/baseline_lookup.parquet"
    if not force and _is_output_fresh(output_path, f"{data_dir}/sample_readings.csv"):
        import pandas as pd
        _state["baseline_df"] = pd.read_parquet(output_path)
        return {"skipped": True}

    from pipeline.features.baseline import compute_baseline
    result = compute_baseline(_state["readings_df"], output_path=output_path, force=True)
    import pandas as pd
    _state["baseline_df"] = pd.read_parquet(output_path)
    return {"skipped": False}


def stage_cluster(data_dir: str, force: bool) -> dict:
    """Fit seasonal KMeans clusters."""
    assignments_path = "data/processed/cluster_assignments.csv"
    profiles_path = "data/processed/seasonal_profiles.json"
    if not force and _is_output_fresh(assignments_path, f"{data_dir}/sample_readings.csv"):
        import pandas as pd
        _state["cluster_assignments"] = pd.read_csv(assignments_path)
        return {"skipped": True}

    from pipeline.clustering.seasonal import fit_seasonal_clusters
    result = fit_seasonal_clusters(
        _state["readings_df"],
        assignments_path=assignments_path,
        profiles_path=profiles_path,
        force=True,
    )
    import pandas as pd
    _state["cluster_assignments"] = pd.read_csv(assignments_path)
    return {"skipped": False}


def stage_zone_profiles(data_dir: str, force: bool) -> dict:
    """Compute per-feeder load profiles and stress metrics."""
    output_path = "data/processed/zone_profiles.parquet"
    if not force and _is_output_fresh(output_path, f"{data_dir}/sample_readings.csv"):
        return {"skipped": True}

    from pipeline.features.zone_profiles import compute_zone_profiles
    compute_zone_profiles(
        _state["readings_df"],
        _state["registry_df"],
        cluster_assignments=_state.get("cluster_assignments"),
        output_path=output_path,
        force=True,
    )
    return {"skipped": False}


def stage_build_matrix(data_dir: str, force: bool) -> dict:
    """Assemble the full feature matrix."""
    output_path = "data/processed/feature_matrix.parquet"
    if not force and _is_output_fresh(output_path, "data/processed/baseline_lookup.parquet"):
        import pandas as pd
        _state["feature_matrix"] = pd.read_parquet(output_path)
        return {"skipped": True}

    from pipeline.features.build_matrix import build_feature_matrix
    result = build_feature_matrix(
        _state["readings_df"],
        _state["registry_df"],
        data_dir="data/processed",
        injected_events_path=f"{data_dir}/injected_events.json",
        output_path=output_path,
        force=True,
    )
    import pandas as pd
    _state["feature_matrix"] = pd.read_parquet(output_path)
    return {"skipped": False}


def stage_train_load_forecast(data_dir: str, force: bool) -> dict:
    """Train XGBoost load forecast models (one per cluster)."""
    from models.load_forecast.train import train_load_forecast_models
    model_paths = train_load_forecast_models(data_dir="data/processed", force=force)
    return {"skipped": False, "models": model_paths}


def stage_train_truth_engine(data_dir: str, force: bool) -> dict:
    """Train LightGBM Truth Engine."""
    from models.truth_engine.train import train_truth_engine
    model_path = train_truth_engine(data_dir="data/processed", force=force)
    return {"skipped": False, "model": model_path}


def stage_inference(data_dir: str, force: bool) -> dict:
    """Score all meters and write alerts."""
    from models.inference_runner import run_inference_and_write_alerts
    summary = run_inference_and_write_alerts(data_dir="data/processed", force=force)
    _state["inference_summary"] = summary
    return {"skipped": False, **summary}


def stage_evaluate(data_dir: str, force: bool) -> dict:
    """Evaluate models and write eval_report.json."""
    from models.eval.evaluate import evaluate_models
    report = evaluate_models(data_dir="data/processed", force=force)
    return {"skipped": False}


# ---------------------------------------------------------------------------
# Pipeline state (shared between stages)
# ---------------------------------------------------------------------------
_state: dict = {}

STAGES = [
    ("Synthetic Data Check",   stage_check_synthetic_data),
    ("Ingest Readings",        stage_ingest),
    ("Validate",               stage_validate),
    ("Impute Gaps",            stage_impute),
    ("Write to Database",      stage_write_to_db),
    ("Build Peer Graph",       stage_peer_graph),
    ("Compute Baseline",       stage_baseline),
    ("Seasonal Clustering",    stage_cluster),
    ("Zone Profiles",          stage_zone_profiles),
    ("Build Feature Matrix",   stage_build_matrix),
    ("Train Load Forecast",    stage_train_load_forecast),
    ("Train Truth Engine",     stage_train_truth_engine),
    ("Run Inference",          stage_inference),
    ("Evaluate Models",        stage_evaluate),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synapse-Grid pipeline runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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


def main() -> None:
    args = parse_args()

    print(f"\n{'=' * 60}")
    print(f"  Synapse-Grid Pipeline  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Data dir: {args.data_dir}  |  Force: {args.force}")
    print(f"{'=' * 60}\n")

    for stage_name, stage_fn in STAGES:
        try:
            print(f"[{stage_name}] Starting...")
            result = stage_fn(data_dir=args.data_dir, force=args.force)
            if isinstance(result, dict) and result.get("skipped"):
                print(f"[{stage_name}] Skipped (output up to date).")
            else:
                print(f"[{stage_name}] Done.")
        except Exception as e:
            print(f"\n[{stage_name}] FAILED: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            print("\nPipeline aborted. Fix the error above and re-run.", file=sys.stderr)
            sys.exit(1)

    # Final summary
    summary = _state.get("inference_summary", {})
    print(f"\n{'=' * 60}")
    print(f"  === Pipeline Complete ===")
    print(f"  Meters processed:          {summary.get('meters_processed', 'N/A')}")
    print(f"  Alerts written (NEW):      {summary.get('alerts_new', 0)}")
    print(f"  Alerts written (WATCHING): {summary.get('alerts_watching', 0)}")
    print(f"  Shadow queue records:      {summary.get('shadow_records', 0)}")
    print(f"  Eval report:               models/eval/eval_report.json")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
