"""
api/main.py

Synapse-Grid FastAPI application.
- Starts with SQLite schema creation (create_all) on startup
- No authentication (hackathon prototype)
- CORS enabled for Vite dev server (localhost:5173)
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.database import init_db

# Import routers (registered after app creation)
from api.routers.alerts import router as alerts_router
from api.routers.meters import router as meters_router
from api.routers.feeders import router as feeders_router

# Ensure data directory exists
Path("data").mkdir(exist_ok=True)

app = FastAPI(
    title="Synapse-Grid API",
    description=(
        "Proactive grid intelligence platform for BESCOM dispatchers. "
        "Provides anomaly alerts, meter readings, and feeder stress data."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Allow Vite dev server to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    """Create all database tables on startup. No migrations required."""
    await init_db()


# Register all routers under /api/v1
app.include_router(alerts_router, prefix="/api/v1", tags=["alerts"])
app.include_router(meters_router, prefix="/api/v1", tags=["meters"])
app.include_router(feeders_router, prefix="/api/v1", tags=["feeders"])


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "service": "synapse-grid-api"}
