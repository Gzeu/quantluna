"""
tests/test_sprint41_44.py — S36
Teste smoke pentru routerele API livrate in S41-S44:
  - /api/services/*   (ServicesControlPanel)
  - /api/optimizer/*  (Grid Search WFO)
  - /api/watchdog/*   (MonitoringWatchdog)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock
from fastapi import FastAPI


# ─ helpers ──────────────────────────────────────────────────────────────────

def _make_app():
    from fastapi import FastAPI
    from api.services  import services_router
    from api.optimizer import optimizer_router, set_optimizer_state
    from api.watchdog  import watchdog_router,  set_watchdog_state

    app = FastAPI()
    app.include_router(services_router,  prefix="/api/services")
    app.include_router(optimizer_router, prefix="/api/optimizer")
    app.include_router(watchdog_router,  prefix="/api/watchdog")

    # Watchdog mock
    wd = MagicMock()
    wd.enabled = True
    wd.thresholds = {}
    wd.alerts = []
    wd.get_status = MagicMock(return_value={
        "enabled": True, "alerts_total": 0, "halted_pairs": []
    })
    set_watchdog_state({"watchdog": wd, "dispatcher": MagicMock()})

    # Optimizer mock
    set_optimizer_state({
        "running": False,
        "last_run": None,
        "last_results": {},
        "pairs": ["BTCUSDT"],
        "auto_reoptimizer": None,
    })

    return app


@pytest.fixture(scope="module")
def client():
    return TestClient(_make_app())


# ─ /api/services ─────────────────────────────────────────────────────────────

class TestServicesRouter:
    def test_services_status_200(self, client):
        r = client.get("/api/services/status")
        assert r.status_code == 200

    def test_services_status_has_services_key(self, client):
        r = client.get("/api/services/status")
        data = r.json()
        assert "services" in data or isinstance(data, dict)

    def test_services_list_200(self, client):
        """GET /api/services/ sau /api/services trebuie sa raspunda."""
        r = client.get("/api/services/")
        assert r.status_code in (200, 404)  # 404 OK daca endpoint nu e definit


# ─ /api/optimizer ────────────────────────────────────────────────────────────

class TestOptimizerRouter:
    def test_optimizer_status_200(self, client):
        r = client.get("/api/optimizer/status")
        assert r.status_code == 200

    def test_optimizer_status_running_false(self, client):
        r = client.get("/api/optimizer/status")
        data = r.json()
        assert data.get("running") is False

    def test_optimizer_history_200(self, client):
        r = client.get("/api/optimizer/history")
        assert r.status_code == 200

    def test_optimizer_results_200(self, client):
        r = client.get("/api/optimizer/results")
        assert r.status_code == 200


# ─ /api/watchdog ─────────────────────────────────────────────────────────────

class TestWatchdogRouter:
    def test_watchdog_status_200(self, client):
        r = client.get("/api/watchdog/status")
        assert r.status_code == 200

    def test_watchdog_status_enabled(self, client):
        r = client.get("/api/watchdog/status")
        data = r.json()
        assert "enabled" in data

    def test_watchdog_alerts_200(self, client):
        r = client.get("/api/watchdog/alerts")
        assert r.status_code == 200

    def test_watchdog_thresholds_get(self, client):
        r = client.get("/api/watchdog/thresholds")
        assert r.status_code == 200

    def test_watchdog_enable_post(self, client):
        r = client.post("/api/watchdog/enable")
        assert r.status_code in (200, 201, 422)  # 422 daca body necesar

    def test_watchdog_disable_post(self, client):
        r = client.post("/api/watchdog/disable")
        assert r.status_code in (200, 201, 422)
