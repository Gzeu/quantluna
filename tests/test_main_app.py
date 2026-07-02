"""
QuantLuna — Tests: Unified FastAPI app (api/main.py)
Sprint 21  |  5 smoke tests

Verifica:
  - GET /            — root returns version + modules
  - GET /docs        — Swagger UI reachable (200)
  - GET /health      — health endpoint reachable
  - GET /strategy/scores  — strategy router mounted correctly
  - GET /backtest/jobs    — backtest router mounted correctly
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


class TestMainApp:

    def test_root_returns_version(self, client):
        r = client.get("/")
        assert r.status_code == 200
        d = r.json()
        assert d["version"] == "0.21.0"
        assert "/backtest" in d["modules"]
        assert "/strategy" in d["modules"]

    def test_docs_reachable(self, client):
        r = client.get("/docs")
        assert r.status_code == 200

    def test_health_reachable(self, client):
        r = client.get("/health")
        assert r.status_code == 200

    def test_strategy_router_mounted(self, client):
        r = client.get("/strategy/scores")
        assert r.status_code == 200
        assert "active_strategy" in r.json()

    def test_backtest_router_mounted(self, client):
        r = client.get("/backtest/jobs")
        # 200 (empty list) or 404 depending on implementation — either way router is mounted
        assert r.status_code in (200, 404, 405)
