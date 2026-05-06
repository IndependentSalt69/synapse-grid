# Synapse-Grid

**Proactive intelligence platform for BESCOM power grid dispatchers.**

Synapse-Grid sits between raw AMI smart meter data and field dispatchers. It detects load stress zones and anomalous consumption patterns (including theft/tamper), and presents actionable, explainable alerts via a React dashboard.

---

## What It Does

1. **Where is the stress zone?** — 24-hour ahead load forecasting per feeder (XGBoost, one model per cluster)
2. **Which meter looks off?** — Anomaly/theft detection with peer-context validation (LightGBM Truth Engine)
3. **What is the right action?** — Structured dispatch workflow: DISPATCH LINEMAN | LOAD BALANCE | DISMISS

Every alert includes a Glass-Box view: 14-day consumption chart, deviation metrics, SHAP top-3 plain-English explanations, and a neighborhood mini-map.

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- pip

---

## Setup

### 1. Clone and install Python dependencies

```bash
git clone <repo-url>
cd synapse_grid
pip install -r requirements.txt
```

### 2. Install frontend dependencies

```bash
cd frontend
npm install
cd ..
```

---

## Three One-Command Entry Points

### Step 1 — Run the full pipeline (loads data, trains models, writes alerts)

```bash
python run_pipeline.py
```

This single command:
- Generates synthetic data (50 meters, 90 days, 432,000 readings) if not already present
- Validates and imputes gaps
- Builds the peer graph and feature matrix
- Trains XGBoost load forecast models (one per cluster) and LightGBM Truth Engine
- Scores all meters and writes alerts to `data/synapse_grid.db`
- Computes SHAP explanations for each alert
- Outputs `models/eval/eval_report.json`

**Options:**
```bash
python run_pipeline.py --data-dir data/raw   # default
python run_pipeline.py --force               # force re-run all stages
```

Expected output:
```
=== Pipeline Complete ===
Meters processed:          50
Alerts written (NEW):      8
Alerts written (WATCHING): 3
Shadow queue records:      41
Eval report:               models/eval/eval_report.json
```

### Step 2 — Start the API

```bash
uvicorn api.main:app --reload
```

API available at: http://localhost:8000  
Interactive docs: http://localhost:8000/docs

### Step 3 — Start the frontend

```bash
cd frontend
npm run dev
```

Dashboard available at: http://localhost:5173

---

## Injected Demo Scenarios

The synthetic dataset includes pre-injected patterns for live demos:

| Pattern | Meters | Days |
|---|---|---|
| Tamper/theft (bypass) | METER_T001–T005 | 60–90 |
| Grid stress events | Feeders F001, F002 | 75–77, 82–84 |
| Vacation (false positive) | METER_V001, V002 | 45–55, 48–58 |
| Short gaps (1–3 slots) | METER_G001–G003 | random |
| Extended gaps (4–8 slots) | METER_G004–G005 | random |

See `data/raw/injected_events.json` for exact timestamps.

---

## Running Tests

```bash
python -m pytest tests/ -v
```

All 69 unit tests cover:
- Phase 2: meter reader, registry, validator, gap handler, peer graph
- Phase 3: pattern fingerprint (vacation vs bypass disambiguation)
- Phase 4: alert confidence gating (0.89 → shadow, 0.91+repeat → NEW, 0.91 no repeat → WATCHING)

---

## Project Structure

```
synapse_grid/
├── run_pipeline.py          # Single CLI entry point
├── requirements.txt
├── data/
│   ├── raw/                 # Synthetic CSV inputs + injected_events.json
│   └── processed/           # Feature files, peer graph, cluster assignments
├── pipeline/                # Data ingestion, imputation, feature engineering
├── models/                  # ML training, inference, SHAP, evaluation
├── api/                     # FastAPI backend
├── frontend/                # React 18 + TypeScript dashboard
├── tests/                   # pytest unit tests
├── demo/                    # Demo walkthrough scripts
└── eval/                    # Ablation study notes
```

---

## Architecture

```
CSV Files → run_pipeline.py → SQLite (synapse_grid.db) → FastAPI → React Dashboard
```

- **Pipeline**: Python 3.11, pandas, XGBoost, LightGBM, SHAP, scikit-learn
- **API**: FastAPI + SQLAlchemy 2.0 async + SQLite
- **Frontend**: React 18 + TypeScript + Vite + Tailwind + Recharts + react-leaflet + TanStack Query + Zustand

---

## Key Design Decisions

- **90% confidence threshold** is hardcoded — not a configurable slider
- **Repeat-pattern gate**: alert fires only after ≥2 consecutive days OR ≥3 of last 5 days
- **Dispatch action is final**: DISPATCHED/DISMISSED alerts cannot be re-opened
- **SHAP values computed at write time**, stored in DB, served statically — never recomputed on request
- **No authentication**: hackathon prototype — dispatcher is whoever has the app open
- **No background scheduler**: run `python run_pipeline.py` manually to refresh alerts
