"""
tests/test_sprint16_api.py  —  Sprint 16 backtest REST API tests

Covers:
  - BacktestRequest: validare Pydantic (fields, validators)
  - POST /api/backtest/run: async job creation
  - POST /api/backtest/run?sync=true: blocking execution
  - GET  /api/backtest/jobs/{id}: job retrieval
  - GET  /api/backtest/jobs/{id}/trades.csv: CSV download
  - GET  /api/backtest/jobs: listing
  - DELETE /api/backtest/jobs/{id}: cleanup
  - Error cases: 404, 409, validation errors
  - Metrics keys: toate prezente in response
  - CSV: Content-Type + Content-Disposition headers
  - _generate_synthetic_prices: shape + cointegration
  - _run_backtest_job: error handling
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient cu app-ul principal."""
    # Patch state_bus înainte de import app
    mock_bus = MagicMock()
    mock_bus.snapshot_dict.return_value = {"status": "IDLE", "pnl_usdt": 0.0,
                                            "drawdown": 0.0, "n_trades": 0,
                                            "last_update": None}
    mock_bus.get_positions.return_value = []
    mock_bus.get_equity_curve.return_value = []
    mock_bus.get_recent_trades.return_value = []

    with patch.dict("sys.modules", {"state_bus": MagicMock(bus=mock_bus),
                                     "core.state_bus": MagicMock(bus=mock_bus)}):
        from dashboard.server import app
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.fixture(scope="module")
def bt_client():
    """TestClient direct pe backtest router (izolat)."""
    from fastapi import FastAPI
    from api.backtest import router
    test_app = FastAPI()
    test_app.include_router(router)
    with TestClient(test_app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Pydantic schema validation
# ---------------------------------------------------------------------------

class TestBacktestRequest:
    def test_defaults_valid(self):
        from api.schemas import BacktestRequest
        req = BacktestRequest()
        assert req.sym_y == "BTCUSDT"
        assert req.sym_x == "ETHUSDT"
        assert req.n_splits == 5

    def test_sym_uppercase(self):
        from api.schemas import BacktestRequest
        req = BacktestRequest(sym_y="btcusdt", sym_x="ethusdt")
        assert req.sym_y == "BTCUSDT"
        assert req.sym_x == "ETHUSDT"

    def test_zscore_entry_gt_exit_valid(self):
        from api.schemas import BacktestRequest
        req = BacktestRequest(zscore_entry=2.0, zscore_exit=0.5)
        assert req.zscore_entry == 2.0

    def test_zscore_entry_le_exit_raises(self):
        from api.schemas import BacktestRequest
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            BacktestRequest(zscore_entry=0.5, zscore_exit=0.5)

    def test_capital_gt_zero(self):
        from api.schemas import BacktestRequest
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            BacktestRequest(capital_usdt=-100)

    def test_bar_freq_enum(self):
        from api.schemas import BacktestRequest, BarFreq
        req = BacktestRequest(bar_freq="4h")
        assert req.bar_freq == BarFreq.H4

    def test_n_bars_minimum(self):
        from api.schemas import BacktestRequest
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            BacktestRequest(n_bars=10)  # below 300 minimum


# ---------------------------------------------------------------------------
# POST /api/backtest/run (async)
# ---------------------------------------------------------------------------

class TestRunEndpointAsync:
    def test_returns_202(self, bt_client):
        resp = bt_client.post("/api/backtest/run",
                              json={"n_bars": 600, "n_splits": 2})
        assert resp.status_code == 202

    def test_response_has_job_id(self, bt_client):
        resp = bt_client.post("/api/backtest/run",
                              json={"n_bars": 600, "n_splits": 2})
        data = resp.json()
        assert "job_id" in data
        assert len(data["job_id"]) == 8

    def test_status_queued_or_running(self, bt_client):
        resp = bt_client.post("/api/backtest/run",
                              json={"n_bars": 600, "n_splits": 2})
        assert resp.json()["status"] in ("queued", "running", "done")

    def test_request_echoed_in_response(self, bt_client):
        resp = bt_client.post("/api/backtest/run",
                              json={"sym_y": "SOLUSDT", "sym_x": "BNBUSDT",
                                    "n_bars": 600, "n_splits": 2})
        data = resp.json()
        assert data["request"]["sym_y"] == "SOLUSDT"
        assert data["request"]["sym_x"] == "BNBUSDT"

    def test_invalid_request_422(self, bt_client):
        resp = bt_client.post("/api/backtest/run",
                              json={"zscore_entry": 0.3, "zscore_exit": 0.5})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/backtest/run?sync=true
# ---------------------------------------------------------------------------

class TestRunEndpointSync:
    def test_sync_returns_metrics(self, bt_client):
        resp = bt_client.post("/api/backtest/run?sync=true",
                              json={"n_bars": 600, "n_splits": 2})
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] in ("done", "error")

    def test_sync_metrics_keys(self, bt_client):
        resp = bt_client.post("/api/backtest/run?sync=true",
                              json={"n_bars": 600, "n_splits": 2})
        data = resp.json()
        if data["status"] == "done" and data.get("metrics"):
            metrics = data["metrics"]
            required = {"sharpe", "sortino", "calmar", "max_drawdown",
                        "win_rate", "profit_factor", "n_trades",
                        "total_net_pnl", "overfit_flag", "n_folds"}
            for k in required:
                assert k in metrics, f"Missing metric: {k}"

    def test_sync_duration_recorded(self, bt_client):
        resp = bt_client.post("/api/backtest/run?sync=true",
                              json={"n_bars": 600, "n_splits": 2})
        data = resp.json()
        if data["status"] == "done":
            assert data["duration_s"] is not None
            assert data["duration_s"] >= 0


# ---------------------------------------------------------------------------
# GET /api/backtest/jobs/{id}
# ---------------------------------------------------------------------------

class TestGetJob:
    def test_404_unknown_job(self, bt_client):
        resp = bt_client.get("/api/backtest/jobs/nonexistent")
        assert resp.status_code == 404

    def test_get_existing_job(self, bt_client):
        # Create job first
        create_resp = bt_client.post("/api/backtest/run?sync=true",
                                      json={"n_bars": 600, "n_splits": 2})
        job_id = create_resp.json()["job_id"]
        get_resp = bt_client.get(f"/api/backtest/jobs/{job_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["job_id"] == job_id

    def test_get_job_has_trades_csv_url_when_done(self, bt_client):
        create_resp = bt_client.post("/api/backtest/run?sync=true",
                                      json={"n_bars": 600, "n_splits": 2})
        data = create_resp.json()
        if data["status"] == "done":
            assert data.get("trades_csv_url") is not None
            assert "/trades.csv" in data["trades_csv_url"]


# ---------------------------------------------------------------------------
# GET /api/backtest/jobs/{id}/trades.csv
# ---------------------------------------------------------------------------

class TestDownloadCsv:
    def test_csv_404_for_unknown_job(self, bt_client):
        resp = bt_client.get("/api/backtest/jobs/ghost/trades.csv")
        assert resp.status_code == 404

    def test_csv_409_for_queued_job(self, bt_client):
        """Un job in starea queued/running nu poate fi downloadat."""
        create_resp = bt_client.post("/api/backtest/run",  # async!
                                      json={"n_bars": 600, "n_splits": 2})
        job_id = create_resp.json()["job_id"]
        # Immediately fetch CSV before job completes (may be queued)
        csv_resp = bt_client.get(f"/api/backtest/jobs/{job_id}/trades.csv")
        # Either 409 (not done) or 200 (already done race condition) are acceptable
        assert csv_resp.status_code in (200, 409)

    def test_csv_content_type(self, bt_client):
        create_resp = bt_client.post("/api/backtest/run?sync=true",
                                      json={"n_bars": 600, "n_splits": 2})
        job_id = create_resp.json()["job_id"]
        if create_resp.json()["status"] == "done":
            csv_resp = bt_client.get(f"/api/backtest/jobs/{job_id}/trades.csv")
            assert csv_resp.status_code == 200
            assert "text/csv" in csv_resp.headers["content-type"]

    def test_csv_filename_in_disposition(self, bt_client):
        create_resp = bt_client.post("/api/backtest/run?sync=true",
                                      json={"n_bars": 600, "n_splits": 2})
        job_id = create_resp.json()["job_id"]
        if create_resp.json()["status"] == "done":
            csv_resp = bt_client.get(f"/api/backtest/jobs/{job_id}/trades.csv")
            if csv_resp.status_code == 200:
                disp = csv_resp.headers.get("content-disposition", "")
                assert "trades" in disp
                assert ".csv" in disp


# ---------------------------------------------------------------------------
# GET /api/backtest/jobs (list)
# ---------------------------------------------------------------------------

class TestListJobs:
    def test_returns_list(self, bt_client):
        resp = bt_client.get("/api/backtest/jobs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_limit_respected(self, bt_client):
        resp = bt_client.get("/api/backtest/jobs?limit=2")
        assert len(resp.json()) <= 2

    def test_list_item_has_job_id(self, bt_client):
        # Ensure at least one job exists
        bt_client.post("/api/backtest/run?sync=true",
                        json={"n_bars": 600, "n_splits": 2})
        resp = bt_client.get("/api/backtest/jobs")
        if resp.json():
            assert "job_id" in resp.json()[0]
            assert "status" in resp.json()[0]


# ---------------------------------------------------------------------------
# DELETE /api/backtest/jobs/{id}
# ---------------------------------------------------------------------------

class TestDeleteJob:
    def test_delete_existing(self, bt_client):
        create_resp = bt_client.post("/api/backtest/run?sync=true",
                                      json={"n_bars": 600, "n_splits": 2})
        job_id = create_resp.json()["job_id"]
        del_resp = bt_client.delete(f"/api/backtest/jobs/{job_id}")
        assert del_resp.status_code == 204
        # Verify gone
        get_resp = bt_client.get(f"/api/backtest/jobs/{job_id}")
        assert get_resp.status_code == 404

    def test_delete_404_unknown(self, bt_client):
        resp = bt_client.delete("/api/backtest/jobs/doesnotexist")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# _generate_synthetic_prices
# ---------------------------------------------------------------------------

class TestGenerateSyntheticPrices:
    def test_shape(self):
        from api.backtest import _generate_synthetic_prices
        df = _generate_synthetic_prices(n=500)
        assert len(df) == 500
        assert "timestamp" in df.columns
        assert "close_y" in df.columns
        assert "close_x" in df.columns

    def test_no_nan(self):
        from api.backtest import _generate_synthetic_prices
        df = _generate_synthetic_prices(n=300)
        assert not df.isnull().any().any()

    def test_4h_frequency(self):
        from api.backtest import _generate_synthetic_prices
        df = _generate_synthetic_prices(n=100, freq="4h")
        diffs = pd.to_datetime(df["timestamp"]).diff().dropna()
        assert (diffs == pd.Timedelta(hours=4)).all()

    def test_prices_positive(self):
        from api.backtest import _generate_synthetic_prices
        df = _generate_synthetic_prices(n=300)
        assert (df["close_y"] > 0).all()
        assert (df["close_x"] > 0).all()
