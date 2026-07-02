"""
QuantLuna — Tests: /strategy API endpoints
Sprint 20  |  6 integration tests
"""
from __future__ import annotations
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import strategy as strategy_module


@pytest.fixture(autouse=True)
def reset_selectors():
    strategy_module._SELECTORS.clear()
    yield
    strategy_module._SELECTORS.clear()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(strategy_module.router)
    return TestClient(app)


class TestStrategyScoresEndpoint:
    def test_scores_default_live(self, client):
        r = client.get("/strategy/scores")
        assert r.status_code == 200
        d = r.json()
        assert all(k in d for k in ("active_strategy", "scores", "recent_win_rate", "total_bars"))
        assert d["selector_id"] == "live"

    def test_scores_custom_selector_id(self, client):
        r = client.get("/strategy/scores?selector_id=job_42")
        assert r.status_code == 200
        assert r.json()["selector_id"] == "job_42"


class TestStrategyListEndpoint:
    def test_list_returns_all_strategies(self, client):
        r = client.get("/strategy/list")
        assert r.status_code == 200
        d = r.json()
        assert d["total"] >= 3
        names = [s["name"] for s in d["strategies"]]
        assert all(n in names for n in ("BollingerBandsMeanReversion", "ZScoreMomentum", "FundingRateArbitrage"))

    def test_list_includes_version(self, client):
        for s in client.get("/strategy/list").json()["strategies"]:
            assert s["version"] != ""


class TestStrategySwitchEndpoint:
    def test_switch_valid_strategy(self, client):
        r = client.post("/strategy/switch", json={"strategy_name": "ZScoreMomentum", "selector_id": "live"})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True and d["switched_to"] == "ZScoreMomentum"

    def test_switch_invalid_strategy_404(self, client):
        r = client.post("/strategy/switch", json={"strategy_name": "GhostStrategy", "selector_id": "live"})
        assert r.status_code == 404
        assert "not found" in r.json()["detail"].lower()


class TestStrategyContextEndpoint:
    def test_context_unknown_job(self, client):
        r = client.get("/strategy/context/nonexistent_job_999")
        assert r.status_code == 200
        d = r.json()
        assert d["context"] == {} and "note" in d
