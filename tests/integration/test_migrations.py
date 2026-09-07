"""Exercises the real Alembic migration (not Base.metadata.create_all).

Runs alembic in a subprocess so there is no asyncio event-loop clash with
pytest-asyncio.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _alembic(args: list[str], db_url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": db_url, "APP_ENV": "test"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def sqlite_url(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'mig.db'}"


def test_upgrade_then_downgrade(sqlite_url):
    up = _alembic(["upgrade", "head"], sqlite_url)
    assert up.returncode == 0, up.stderr

    down = _alembic(["downgrade", "base"], sqlite_url)
    assert down.returncode == 0, down.stderr

    up2 = _alembic(["upgrade", "head"], sqlite_url)
    assert up2.returncode == 0, up2.stderr


def test_migrated_schema_matches_models(sqlite_url):
    import asyncio

    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import create_async_engine

    from app import models  # noqa: F401
    from app.db.base import Base

    assert _alembic(["upgrade", "head"], sqlite_url).returncode == 0

    async def _tables() -> set[str]:
        engine = create_async_engine(sqlite_url)
        try:
            async with engine.connect() as conn:
                names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        finally:
            await engine.dispose()
        return names

    db_tables = asyncio.run(_tables())
    model_tables = set(Base.metadata.tables) | {"alembic_version"}
    assert model_tables == db_tables
