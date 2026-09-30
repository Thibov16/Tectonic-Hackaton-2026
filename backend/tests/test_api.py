from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(ingested, monkeypatch_session=None):
    # Point service at ingested db
    from app.config import AppConfig
    from app import pipeline
    from app.main import app

    svc = pipeline.SearchService(ingested)
    svc.ensure_ready()
    pipeline._SERVICE = svc
    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_search_empty_rejected(client):
    r = client.post("/api/search", json={"details": ""})
    assert r.status_code in {400, 422}


def test_search_janssens(client):
    r = client.post(
        "/api/search",
        json={"details": "What allowance applies to a Janssens Logistics employee?", "today": "2026-09-30"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "verdict" in data
    assert data["sources"]
    assert all("reasons" in s for s in data["sources"])
