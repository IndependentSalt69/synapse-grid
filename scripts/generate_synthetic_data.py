#!/usr/bin/env python3
"""
generate_synthetic_data.py — Synapse-Grid synthetic data generator.

Generates:
- data/raw/sample_readings.csv (432,000 rows: 50 meters × 90 days × 96 slots/day)
- data/raw/sample_registry.csv (50 meters with geographic and electrical metadata)
- data/raw/injected_events.json (ground truth labels for all injected patterns)

Injected patterns:
- 5 tamper/theft meters (METER_T001–METER_T005): ≥80% daytime drop, preserved night activity
- 2 grid stress events (feeders F001, F002): spike to >90% utilization
- 2 vacation meters (METER_V001, METER_V002): sustained drop including night
- 3 short-gap meters (METER_G001–G003): 1–3 consecutive NaN slots
- 2 extended-gap meters (METER_G004–G005): 4–8 consecutive NaN slots
"""

import numpy as np
import pandas as pd
import json
from datetime import date, datetime, timedelta
from pathlib import Path

RANDOM_SEED = 42
N_METERS = 50
N_DAYS = 90
FREQ = "15min"
START_DATE = "2024-01-01"

# Geographic cluster centers in Bangalore area
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

# Injected pattern configurations
TAMPER_METERS = ["METER_T001", "METER_T002", "METER_T003", "METER_T004", "METER_T005"]
VACATION_METERS = ["METER_V001", "METER_V002"]
SHORT_GAP_METERS = ["METER_G001", "METER_G002", "METER_G003"]
EXTENDED_GAP_METERS = ["METER_G004", "METER_G005"]

TAMPER_CONFIG = [
    {"meter_id": "METER_T001", "start_day": 60, "end_day": 90, "drop_pct": 0.88},
    {"meter_id": "METER_T002", "start_day": 63, "end_day": 90, "drop_pct": 0.85},
    {"meter_id": "METER_T003", "start_day": 65, "end_day": 90, "drop_pct": 0.90},
    {"meter_id": "METER_T004", "start_day": 67, "end_day": 90, "drop_pct": 0.87},
    {"meter_id": "METER_T005", "start_day": 70, "end_day": 90, "drop_pct": 0.86},
]

STRESS_CONFIG = [
    {"feeder_id": "F001", "start_day": 75, "end_day": 77, "hours": (14, 20), "spike_factor": 1.5},
    {"feeder_id": "F002", "start_day": 82, "end_day": 84, "hours": (11, 17), "spike_factor": 1.4},
]

VACATION_CONFIG = [
    {"meter_id": "METER_V001", "start_day": 45, "end_day": 55},
    {"meter_id": "METER_V002", "start_day": 48, "end_day": 58},
]


def hour_of_day_curve(hour: int) -> float:
    """Normalized residential load curve: peak 18-21h, trough 2-5h."""
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


def generate_registry(rng: np.random.Generator) -> pd.DataFrame:
    """Generate meter registry with 50 meters in 5 geographic clusters."""
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
    special_ids = TAMPER_METERS + VACATION_METERS + SHORT_GAP_METERS + EXTENDED_GAP_METERS
    for i, special_id in enumerate(special_ids):
        if i < len(rows):
            rows[i]["meter_id"] = special_id
    
    return pd.DataFrame(rows)


def generate_base_load(registry_df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Generate base load for all meters over 90 days at 15-min intervals."""
    timestamps = pd.date_range(start=START_DATE, periods=N_DAYS * 96, freq=FREQ, tz="UTC")
    all_rows = []
    
    for meter_row in registry_df.itertuples():
        meter_id = meter_row.meter_id
        base_mean = rng.normal(2.5, 0.3)  # kWh per 15-min slot
        base_mean = max(0.5, base_mean)
        
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
            
            # Add noise (±8%)
            kwh = expected * rng.normal(1.0, 0.08)
            kwh = max(0.0, kwh)
            
            # Voltage: normal(230, 5), clamped 200-250V
            voltage = float(np.clip(rng.normal(230, 5), 200, 250))
            
            # Power factor: normal(0.92, 0.03), clamped 0.85-1.0
            pf = float(np.clip(rng.normal(0.92, 0.03), 0.85, 1.0))
            
            # Reactive power: derived from apparent power
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
    
    return pd.DataFrame(all_rows)


def inject_tamper_patterns(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Inject 5 tamper/theft patterns: daytime drop ≥80%, night activity preserved."""
    start_date = pd.Timestamp(START_DATE, tz="UTC")
    
    for cfg in TAMPER_CONFIG:
        mask = (
            (df["meter_id"] == cfg["meter_id"]) &
            (pd.to_datetime(df["timestamp"]) >= start_date + timedelta(days=cfg["start_day"])) &
            (pd.to_datetime(df["timestamp"]) < start_date + timedelta(days=cfg["end_day"]))
        )
        
        # Daytime (5-21h): drop to 5-15% of normal
        day_mask = mask & pd.to_datetime(df["timestamp"]).dt.hour.between(5, 21)
        df.loc[day_mask, "kwh"] *= (1 - cfg["drop_pct"])
        
        # Night (22-4h): preserve 60-80% of normal (bypass signature)
        night_mask = mask & ~pd.to_datetime(df["timestamp"]).dt.hour.between(5, 21)
        df.loc[night_mask, "kwh"] *= rng.uniform(0.60, 0.80, size=night_mask.sum())
    
    return df


def inject_stress_events(df: pd.DataFrame, registry_df: pd.DataFrame) -> pd.DataFrame:
    """Inject 2 grid stress events: feeder utilization >90%."""
    start_date = pd.Timestamp(START_DATE, tz="UTC")
    
    for cfg in STRESS_CONFIG:
        feeder_meters = registry_df[registry_df["feeder_id"] == cfg["feeder_id"]]["meter_id"].tolist()
        mask = (
            df["meter_id"].isin(feeder_meters) &
            (pd.to_datetime(df["timestamp"]) >= start_date + timedelta(days=cfg["start_day"])) &
            (pd.to_datetime(df["timestamp"]) < start_date + timedelta(days=cfg["end_day"])) &
            pd.to_datetime(df["timestamp"]).dt.hour.between(*cfg["hours"])
        )
        df.loc[mask, "kwh"] *= cfg["spike_factor"]
        # Clamp to physically reasonable maximum
        df.loc[mask, "kwh"] = df.loc[mask, "kwh"].clip(upper=20.0)
    
    return df


def inject_vacation_patterns(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Inject 2 vacation patterns: sustained drop including night (distinguishes from tamper)."""
    start_date = pd.Timestamp(START_DATE, tz="UTC")
    
    for cfg in VACATION_CONFIG:
        mask = (
            (df["meter_id"] == cfg["meter_id"]) &
            (pd.to_datetime(df["timestamp"]) >= start_date + timedelta(days=cfg["start_day"])) &
            (pd.to_datetime(df["timestamp"]) < start_date + timedelta(days=cfg["end_day"]))
        )
        # All hours drop to 5-10% of normal (including night)
        df.loc[mask, "kwh"] *= rng.uniform(0.05, 0.10, size=mask.sum())
    
    return df


def inject_gap_patterns(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Inject short-gap (1-3 NaN) and extended-gap (4-8 NaN) patterns."""
    # Short gaps
    for meter_id in SHORT_GAP_METERS:
        meter_mask = df["meter_id"] == meter_id
        meter_indices = df[meter_mask].index.tolist()
        gap_start_idx = rng.integers(len(meter_indices) // 4, len(meter_indices) // 2)
        gap_length = rng.integers(1, 4)  # 1, 2, or 3 slots
        gap_indices = meter_indices[gap_start_idx:gap_start_idx + gap_length]
        df.loc[gap_indices, "kwh"] = np.nan
    
    # Extended gaps
    for meter_id in EXTENDED_GAP_METERS:
        meter_mask = df["meter_id"] == meter_id
        meter_indices = df[meter_mask].index.tolist()
        gap_start_idx = rng.integers(len(meter_indices) // 3, len(meter_indices) // 2)
        gap_length = rng.integers(4, 9)  # 4-8 slots
        gap_indices = meter_indices[gap_start_idx:gap_start_idx + gap_length]
        df.loc[gap_indices, "kwh"] = np.nan
    
    return df


def main():
    """Generate all synthetic data files."""
    rng = np.random.default_rng(RANDOM_SEED)
    
    print("Generating meter registry...")
    registry_df = generate_registry(rng)
    
    print(f"Generating base load ({N_METERS} meters × {N_DAYS} days × 96 slots = {N_METERS * N_DAYS * 96:,} rows)...")
    readings_df = generate_base_load(registry_df, rng)
    
    print("Injecting tamper patterns (5 meters)...")
    readings_df = inject_tamper_patterns(readings_df, rng)
    
    print("Injecting grid stress events (2 feeders)...")
    readings_df = inject_stress_events(readings_df, registry_df)
    
    print("Injecting vacation patterns (2 meters)...")
    readings_df = inject_vacation_patterns(readings_df, rng)
    
    print("Injecting gap patterns (3 short + 2 extended)...")
    readings_df = inject_gap_patterns(readings_df, rng)
    
    # Write output files
    Path("data/raw").mkdir(parents=True, exist_ok=True)
    
    print("Writing data/raw/sample_registry.csv...")
    registry_df.to_csv("data/raw/sample_registry.csv", index=False)
    
    print("Writing data/raw/sample_readings.csv...")
    readings_df.to_csv("data/raw/sample_readings.csv", index=False)
    
    # Write injected_events.json
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
        "short_gap_meters": SHORT_GAP_METERS,
        "extended_gap_meters": EXTENDED_GAP_METERS,
    }
    
    print("Writing data/raw/injected_events.json...")
    with open("data/raw/injected_events.json", "w") as f:
        json.dump(injected_events, f, indent=2)
    
    print(f"\n{'='*60}")
    print(f"  Synthetic data generation complete!")
    print(f"  Readings: {len(readings_df):,} rows")
    print(f"  Registry: {len(registry_df)} meters")
    print(f"  Tamper meters: {TAMPER_METERS}")
    print(f"  Vacation meters: {VACATION_METERS}")
    print(f"  Stress feeders: {[c['feeder_id'] for c in STRESS_CONFIG]}")
    print(f"  Gap meters: {SHORT_GAP_METERS + EXTENDED_GAP_METERS}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
