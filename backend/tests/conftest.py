"""Shared pytest fixtures for SD Worx trust-search tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure backend/ is on sys.path
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("TODAY", "2026-09-30")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def data_dir(repo_root: Path) -> Path:
    candidate = repo_root / "data" / "sdworx_testset"
    legacy = repo_root / "testset"
    path = candidate if candidate.exists() else legacy
    if not path.exists():
        pytest.skip(f"Dataset not found at {candidate} or {legacy}")
    return path


@pytest.fixture(scope="session")
def app_config(data_dir: Path, tmp_path_factory: pytest.TempPathFactory):
    from app.config import AppConfig
    from datetime import date

    db_path = tmp_path_factory.mktemp("db") / "trust_test.db"
    return AppConfig(
        today=date.fromisoformat("2026-09-30"),
        project_root=REPO_ROOT,
        data_dir=data_dir,
        db_path=db_path,
    )


@pytest.fixture(scope="session")
def ingested(app_config):
    """Rebuild SQLite from the synthetic dataset once per session."""
    from app.ingest.loader import ingest

    stats = ingest(app_config, rebuild=True)
    assert stats["documents"] > 0
    return app_config


@pytest.fixture(scope="session")
def db_conn(ingested):
    from app.ingest import store

    conn = store.connect(ingested.db_path)
    yield conn
    conn.close()
