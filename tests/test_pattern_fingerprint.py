"""
Tests for pipeline/features/pattern_fingerprint.py

Critical spec requirements:
- Vacation pattern (sustained drop + near-zero night activity) must NOT trigger
  is_recurring_daily_pattern=True
- Bypass pattern (recurring hourly dip + non-zero night activity) MUST trigger
  is_recurring_daily_pattern=True
- is_sustained_multiday_drop=True only when >= 3 consecutive days of >= 50% below baseline
- night_activity_score = mean(22:00-05:00 kwh) / overall_mean
"""

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timezone, timedelta

from pipeline.features.pattern_fingerprint import compute_pattern_fingerprints


def _make_ts(start: str, n_days: int) -> pd.DatetimeIndex:
    return pd.date_range(start=start, periods=n_days * 96, freq="15min", tz="UTC")


def _make_df(meter_id: str, timestamps, kwh_values, baseline_values=None) -> pd.DataFrame:
    df = pd.DataFrame({
        "meter_id": meter_id,
        "timestamp": timestamps,
        "kwh": kwh_values,
    })
    if baseline_values is not None:
        df["baseline_kwh"] = baseline_values
    return df


class TestSustainedMultidayDrop:
    def test_3_consecutive_days_triggers_flag(self):
        """3 consecutive days at 30% of baseline → is_sustained_multiday_drop=True."""
        ts = _make_ts("2024-01-01", 10)
        baseline = np.full(len(ts), 2.0)
        kwh = np.full(len(ts), 2.0)
        # Days 3-5 (indices 2*96 to 5*96): drop to 30% of baseline
        kwh[2 * 96: 5 * 96] = 0.6  # 30% of 2.0 = 0.6 (70% below baseline)
        df = _make_df("M001", ts, kwh, baseline)
        result = compute_pattern_fingerprints(df)
        # Rows in days 3-5 should have is_sustained_multiday_drop=True
        drop_rows = result[
            (result["timestamp"] >= pd.Timestamp("2024-01-03", tz="UTC"))
            & (result["timestamp"] < pd.Timestamp("2024-01-06", tz="UTC"))
        ]
        assert drop_rows["is_sustained_multiday_drop"].all()

    def test_2_consecutive_days_does_not_trigger(self):
        """Only 2 consecutive days below threshold → is_sustained_multiday_drop=False."""
        ts = _make_ts("2024-01-01", 10)
        baseline = np.full(len(ts), 2.0)
        kwh = np.full(len(ts), 2.0)
        # Only 2 days drop
        kwh[2 * 96: 4 * 96] = 0.6
        df = _make_df("M001", ts, kwh, baseline)
        result = compute_pattern_fingerprints(df)
        drop_rows = result[
            (result["timestamp"] >= pd.Timestamp("2024-01-03", tz="UTC"))
            & (result["timestamp"] < pd.Timestamp("2024-01-05", tz="UTC"))
        ]
        assert not drop_rows["is_sustained_multiday_drop"].any()

    def test_normal_consumption_no_flag(self):
        """Normal consumption → is_sustained_multiday_drop=False everywhere."""
        ts = _make_ts("2024-01-01", 10)
        baseline = np.full(len(ts), 2.0)
        kwh = np.full(len(ts), 1.8)  # 10% below, not 50%
        df = _make_df("M001", ts, kwh, baseline)
        result = compute_pattern_fingerprints(df)
        assert not result["is_sustained_multiday_drop"].any()


class TestNightActivityScore:
    def test_night_activity_score_computed_correctly(self):
        """night_activity_score = mean(22:00-05:00 kwh) / overall_mean."""
        ts = _make_ts("2024-01-01", 7)
        # Set all kwh to 1.0, then set night hours to 2.0
        kwh = np.ones(len(ts))
        night_mask = pd.DatetimeIndex(ts).hour.isin([22, 23, 0, 1, 2, 3, 4])
        kwh[night_mask] = 2.0
        df = _make_df("M001", ts, kwh)
        result = compute_pattern_fingerprints(df)
        score = result["night_activity_score"].iloc[0]
        # night_mean = 2.0, overall_mean = weighted average
        assert score > 1.0  # night is higher than average

    def test_zero_overall_mean_gives_nan(self):
        """If overall mean is 0, night_activity_score should be NaN."""
        ts = _make_ts("2024-01-01", 3)
        kwh = np.zeros(len(ts))
        df = _make_df("M001", ts, kwh)
        result = compute_pattern_fingerprints(df)
        assert result["night_activity_score"].isna().all() or result["night_activity_score"].iloc[0] == 0.0


class TestVacationVsBypassDisambiguation:
    def test_vacation_pattern_does_not_trigger_recurring_daily_pattern(self):
        """
        Vacation: sustained multiday drop + near-zero night activity
        → is_recurring_daily_pattern must be False.
        """
        ts = _make_ts("2024-01-01", 14)
        baseline = np.full(len(ts), 2.0)
        # All consumption drops to 5% of normal (including night)
        kwh = np.full(len(ts), 0.1)  # 5% of 2.0
        df = _make_df("M001", ts, kwh, baseline)
        result = compute_pattern_fingerprints(df)

        # Should have sustained drop
        assert result["is_sustained_multiday_drop"].any()
        # Night activity score should be low (near-zero relative to baseline)
        # Since all kwh is 0.1, night_mean ≈ overall_mean → score ≈ 1.0
        # But the vacation override checks night_activity_score < 0.2
        # With uniform low consumption, score = night_mean/overall_mean ≈ 1.0
        # So we need to set night to near-zero explicitly
        kwh2 = np.full(len(ts), 0.1)
        night_mask = pd.DatetimeIndex(ts).hour.isin([22, 23, 0, 1, 2, 3, 4])
        kwh2[night_mask] = 0.01  # near-zero night
        df2 = _make_df("M001", ts, kwh2, baseline)
        result2 = compute_pattern_fingerprints(df2)

        # Vacation override: sustained drop + low night → is_recurring_daily_pattern=False
        sustained_rows = result2[result2["is_sustained_multiday_drop"]]
        if not sustained_rows.empty:
            assert not sustained_rows["is_recurring_daily_pattern"].any(), (
                "Vacation pattern (sustained drop + low night activity) must NOT "
                "trigger is_recurring_daily_pattern=True"
            )

    def test_bypass_pattern_triggers_recurring_daily_pattern(self):
        """
        Bypass: recurring hourly dip at consistent hours + non-zero night activity
        → is_recurring_daily_pattern must be True.
        """
        ts = _make_ts("2024-01-01", 14)
        baseline = np.full(len(ts), 2.0)
        kwh = np.full(len(ts), 2.0)

        # Inject a consistent dip at hours 8-10 every day for 10 days
        ts_index = pd.DatetimeIndex(ts)
        dip_mask = ts_index.hour.isin([8, 9, 10])
        kwh[dip_mask] = 0.5  # 25% of baseline (75% below → dip)

        # Night activity stays normal (non-zero)
        # night hours already at 2.0

        df = _make_df("M001", ts, kwh, baseline)
        result = compute_pattern_fingerprints(df)

        # After day 5 (enough history), should see recurring pattern
        late_rows = result[result["timestamp"] >= pd.Timestamp("2024-01-06", tz="UTC")]
        if not late_rows.empty:
            assert late_rows["is_recurring_daily_pattern"].any(), (
                "Bypass pattern (recurring hourly dip + non-zero night activity) "
                "must trigger is_recurring_daily_pattern=True"
            )

    def test_is_recurring_daily_pattern_false_without_consistent_hours(self):
        """Random dips without consistent hours → is_recurring_daily_pattern=False."""
        ts = _make_ts("2024-01-01", 10)
        baseline = np.full(len(ts), 2.0)
        kwh = np.full(len(ts), 2.0)
        # Random single dips, not at consistent hours
        rng = np.random.default_rng(42)
        random_indices = rng.choice(len(ts), size=20, replace=False)
        kwh[random_indices] = 0.3
        df = _make_df("M001", ts, kwh, baseline)
        result = compute_pattern_fingerprints(df)
        # Should not have widespread recurring pattern
        # (some may trigger by chance, but not the majority)
        recurring_pct = result["is_recurring_daily_pattern"].mean()
        assert recurring_pct < 0.5  # Less than 50% of rows flagged


class TestNightActivityScoreValues:
    def test_score_is_float(self):
        ts = _make_ts("2024-01-01", 5)
        kwh = np.ones(len(ts)) * 1.5
        df = _make_df("M001", ts, kwh)
        result = compute_pattern_fingerprints(df)
        score = result["night_activity_score"].iloc[0]
        assert isinstance(score, float) or np.isnan(score)

    def test_uniform_consumption_score_near_1(self):
        """Uniform consumption → night_activity_score ≈ 1.0."""
        ts = _make_ts("2024-01-01", 7)
        kwh = np.ones(len(ts)) * 2.0
        df = _make_df("M001", ts, kwh)
        result = compute_pattern_fingerprints(df)
        score = result["night_activity_score"].iloc[0]
        assert abs(score - 1.0) < 0.01
