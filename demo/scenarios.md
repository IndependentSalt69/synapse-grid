# Synapse-Grid — Demo Scenarios

Three walkthrough scripts for hackathon judges. Each scenario takes 3–5 minutes.

**Setup:** Run `python run_pipeline.py` once before the demo. Then start the API and frontend.

---

## Scenario 1 — Meter Theft Detection

**Story:** A meter in Zone 1 has been bypassed. Daytime consumption dropped 88% while night-time activity remains normal — a classic bypass signature. The system detected it, scored it above 90% confidence, and flagged it after 2 consecutive anomalous days.

### Walkthrough

1. **Open the dashboard** at http://localhost:5173

2. **Look at the Alert Queue** (center panel). The top alert should be for **METER_T001** with:
   - Confidence badge: ~92–96% (red)
   - Pattern badge: **Sustained Drop** or **Daily Dip**
   - State badge: **NEW**

3. **Click the METER_T001 alert row.** The right panel loads the Glass-Box view.

4. **Read the 14-day chart.** You'll see:
   - Blue line (Actual): drops sharply around day 60 and stays low
   - Gray dashed line (Baseline): continues at normal level
   - Yellow highlight: the anomalous period

5. **Read the Deviation Metrics card:**
   - vs Personal Baseline: approximately **−88%**
   - vs Peer Median: approximately **−80%** (neighbors are normal)

6. **Read the SHAP Explanation card ("Why this alert?"):**
   - "Consumption is 88% below this meter's 28-day [weekday] [hour] average."
   - "Consumption is 80% below the median of 9 neighboring meters at the same time."
   - "Night-time usage (10 PM–5 AM) is 0.7x the meter's normal level." ← bypass signature

7. **Look at the Neighborhood Mini-Map.** METER_T001's neighbors are all green (normal). Only METER_T001 is anomalous.

8. **Click "Dispatch Lineman"** in the Dispatch Action Panel.

9. **Confirmation modal appears.** Click **"Confirm Dispatch Lineman"**.

10. **Alert moves to DISPATCHED state.** The state badge turns green. Action buttons are disabled.

**Key talking point:** The system didn't just flag a number — it showed *why* the alert fired, in plain English, with visual context. The dispatcher made an informed decision in under 30 seconds.

---

## Scenario 2 — Vacation vs Bypass Disambiguation

**Story:** Two meters show similar consumption drops. One is a genuine vacation (family away, near-zero night activity). The other is a bypass (daytime tampered, but night activity preserved). The system correctly distinguishes them using the pattern fingerprint features.

### Walkthrough

1. **In the Alert Queue**, look for **METER_V001** (vacation meter). It may appear in WATCHING state or not appear at all (if confidence < 90% — vacation patterns score lower because night activity is near zero, which is *not* the bypass signature).

2. **Now find METER_T002** (tamper meter, started day 63). Click it.

3. **Read the SHAP Explanation:**
   - "Night-time usage (10 PM–5 AM) is 0.7x the meter's normal level." ← non-zero night activity
   - "A consistent daily dip pattern has repeated on 3 of the last 5 days."
   - Pattern type badge: **Daily Dip** or **Sustained Drop**

4. **Now navigate to METER_V001** (if it appears in the queue, or explain from the data):
   - Night activity score ≈ 0.05 (near-zero — family is away, no appliances running at night)
   - `is_recurring_daily_pattern = False` (vacation override applied)
   - Pattern type: **Sustained Drop**
   - If it appears in the queue, click **Dismiss** → select reason code **VACATION** → Confirm.

5. **Side-by-side comparison:**

   | Feature | METER_T002 (Bypass) | METER_V001 (Vacation) |
   |---|---|---|
   | Daytime drop | ~85% | ~95% |
   | Night activity score | ~0.7 | ~0.05 |
   | is_recurring_daily_pattern | True | False |
   | Confidence | ~92% | ~45% |
   | Queue state | NEW | Shadow (not shown) |

**Key talking point:** The vacation/bypass disambiguation is a *feature set*, not a post-hoc rule. The model learned to distinguish them from the training data. The dispatcher sees the night activity score and pattern type — one sentence explains the difference.

---

## Scenario 3 — 24-Hour Load Spike Prediction

**Story:** Feeder F001 is approaching transformer capacity. The XGBoost load forecast model predicts it will exceed 90% utilization in the next 24 hours. The dispatcher can proactively load-balance before the spike causes a fault.

### Walkthrough

1. **Look at the Feeder Stress Map** (left panel). Feeder **F001** should appear as a **RED** circle (>90% utilization) or **AMBER** (approaching threshold), depending on the current synthetic data timestamp.

2. **Click the F001 circle on the map.** The Alert Queue filters to show only F001 alerts.

3. **In the filtered Alert Queue**, find the **LOAD_STRESS** alert for a meter on F001. Click it.

4. **Read the Glass-Box view:**
   - The 14-day chart shows a spike pattern — consumption rising sharply during hours 14–20
   - Deviation Metrics: vs Cluster Norm shows a large positive deviation (+40–50%)
   - SHAP Explanation: "Consumption has been rising steadily over the past 3 days." / "The 7-day rolling average is elevated."

5. **Click "Load Balance"** in the Dispatch Action Panel.

6. **Confirmation modal:** "Confirm: Load Balance for meter [ID]?" → Click **Confirm Load Balance**.

7. **Alert moves to DISPATCHED.** The feeder stress map will update on the next 5-minute poll.

**Key talking point:** The system answered all three questions in one workflow: *Where* (F001 on the map), *Which meter* (the highest-confidence LOAD_STRESS alert), *What action* (Load Balance). The dispatcher never had to leave the dashboard.

---

## Demo Reset

To reset the demo state (clear all dispatched alerts and re-run inference):

```bash
rm data/synapse_grid.db
python run_pipeline.py --force
```

This regenerates all alerts from scratch with the same injected patterns.
