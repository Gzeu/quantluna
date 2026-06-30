"""
tests/test_dashboard_api.py  —  Dashboard FastAPI endpoint tests

Tests:
  - GET /api/status
  - GET /api/positions
  - GET /api/performance
  - GET /api/health
  - GET /api/optimize/results  (Sprint 13)
  - GET /api/optimize/results?study_name=X (cu filtru)
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def client():
    """FastAPI TestClient cu state_bus mock."""
    from fastapi.testclient import TestClient
    from core.state_bus import StateBus  # updated import path

    # Patch StateBus to return predictable data
    mock_bus = MagicMock()
    mock_bus.snapshot_dict.return_value = {
        "status": "RUNNING",
        "pair": "BTCUSDT/ETHUSDT",
        "beta": 1.52,
        "zscore": 0.34,
        "pnl_usdt": 123.45,
        "drawdown": -0.023,
        "n_trades": 7,
        "last_update": "2026-07-01T00:00:00Z",
    }
    mock_bus.get_positions.return_value = []
    mock_bus.get_recent_trades.return_value = []
    mock_bus.get_equity_curve.return_value = []

    with patch("dashboard.server.bus", mock_bus):
        from dashboard.server import app
        with TestClient(app) as c:
            yield c


class TestDashboardEndpoints:
    def test_status_endpoint(self, client):
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data

    def test_health_endpoint(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert data["status"] in ("ok", "degraded", "error")

    def test_optimize_results_no_storage_returns_empty(self, client):
        """When no Optuna DB exists, endpoint returns empty list gracefully."""
        r = client.get("/api/optimize/results?storage=sqlite:////nonexistent/path.db")
        assert r.status_code == 200
        data = r.json()
        assert "trials" in data
        assert isinstance(data["trials"], list)

    def test_optimize_results_with_mock_study(self, client, tmp_path):
        """Verify /api/optimize/results with a real (empty) Optuna study."""
        try:
            import optuna
        except ImportError:
            pytest.skip("optuna not installed")

        db_path = str(tmp_path / "test_optuna.db")
        storage = f"sqlite:///{db_path}"
        study = optuna.create_study(storage=storage, study_name="test_study")
        # Add one trial
        study.enqueue_trial({"delta": 1e-4, "R": 1e-2, "zscore_entry": 2.0,
                             "zscore_exit": 0.5, "kelly_fraction": 0.25,
                             "vol_target": 0.01, "half_life_min_h": 12.0,
                             "half_life_max_h": 168.0, "min_warmup_bars": 30})

        r = client.get(f"/api/optimize/results?storage={storage}&study_name=test_study")
        assert r.status_code == 200
        data = r.json()
        assert "study_name" in data
        assert isinstance(data["trials"], list)
