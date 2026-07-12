"""
tests/test_metrics.py — S36
Teste pentru GET /metrics endpoint Prometheus (api/metrics.py)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch


# ─ helpers ──────────────────────────────────────────────────────────────────

def _make_app(sizing_engine=None, watchdog=None, decision_engine=None):
    """Construieste o mini-app FastAPI cu metrics_router injectat."""
    from fastapi import FastAPI
    from api.metrics import metrics_router, set_metrics_state

    app = FastAPI()
    app.include_router(metrics_router)
    set_metrics_state({
        "sizing_engine":   sizing_engine,
        "watchdog":        watchdog,
        "decision_engine": decision_engine,
    })
    return app


def _mock_sizing():
    se = MagicMock()
    se.get_status.return_value = {
        "capital_usd": 10_000.0,
        "n_reduced_pairs": 2,
        "pair_factors": {"BTCUSDT": 0.8, "ETHUSDT": 0.5},
    }
    return se


def _mock_watchdog():
    wd = MagicMock()
    wd.enabled = True
    wd.get_status.return_value = {
        "enabled":      True,
        "alerts_total": 5,
        "halted_pairs": ["SOLUSDT"],
    }
    return wd


def _mock_decision():
    de = MagicMock()
    de.get_status.return_value = {
        "in_position":    True,
        "current_streak": -2,
        "current_drawdown": 0.03,
    }
    return de


# ─ teste content-type ────────────────────────────────────────────────────────

class TestMetricsContentType:
    def test_content_type_prometheus(self):
        """Media type trebuie sa fie text/plain Prometheus."""
        client = TestClient(_make_app())
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]

    def test_content_type_version_04(self):
        """Version 0.0.4 trebuie sa fie prezent in content-type."""
        client = TestClient(_make_app())
        r = client.get("/metrics")
        assert "0.0.4" in r.headers["content-type"]


# ─ teste metrici prezente ────────────────────────────────────────────────────

class TestMetricsKeys:
    def setup_method(self):
        self.client = TestClient(
            _make_app(
                sizing_engine=_mock_sizing(),
                watchdog=_mock_watchdog(),
                decision_engine=_mock_decision(),
            )
        )

    def test_sizing_capital_present(self):
        r = self.client.get("/metrics")
        assert "sizing_capital_usd" in r.text

    def test_sizing_n_reduced_pairs(self):
        r = self.client.get("/metrics")
        assert "sizing_n_reduced_pairs" in r.text

    def test_pair_factor_label(self):
        """pair_factor cu label pair= trebuie sa apara."""
        r = self.client.get("/metrics")
        assert "pair_factor" in r.text
        assert "BTCUSDT" in r.text

    def test_watchdog_enabled(self):
        r = self.client.get("/metrics")
        assert "watchdog_enabled" in r.text

    def test_watchdog_alerts_total(self):
        r = self.client.get("/metrics")
        assert "watchdog_alerts_total" in r.text

    def test_decision_in_position(self):
        r = self.client.get("/metrics")
        assert "decision_in_position" in r.text

    def test_decision_streak(self):
        r = self.client.get("/metrics")
        assert "decision_streak" in r.text


# ─ teste graceful fallback (engine None) ─────────────────────────────────────

class TestMetricsFallback:
    def test_no_engines_returns_200(self):
        """Fara engine injectat trebuie sa returneze 200, nu 500."""
        client = TestClient(_make_app())
        r = client.get("/metrics")
        assert r.status_code == 200

    def test_only_sizing_injected(self):
        client = TestClient(_make_app(sizing_engine=_mock_sizing()))
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "sizing_capital_usd" in r.text

    def test_only_decision_injected(self):
        client = TestClient(_make_app(decision_engine=_mock_decision()))
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "decision_in_position" in r.text
