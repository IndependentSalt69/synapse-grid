"""
api/database.py

SQLite async database setup for the Synapse-Grid API.
Uses SQLAlchemy 2.0 async with aiosqlite.
Schema is created via create_all() on startup — no Alembic migrations.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

# SQLite only for this prototype. Configurable via DATABASE_URL env var.
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./data/synapse_grid.db",
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
    """
    Create all tables if they do not already exist.
    Called on FastAPI startup. No Alembic migrations required.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency: yields an AsyncSession per request.
    Ensures the session is closed after the request completes.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
