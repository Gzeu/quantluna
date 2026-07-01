"""
tests/test_sprint18.py  —  QuantLuna Sprint 18

Teste pentru:
  1. GET /api/backtest/compare  — multi-run comparison endpoint
  2. Compare helpers: _build_radar, _build_diff_matrix, _build_param_diff
  3. _parse_metrics_param validation
  4. GET /api/backtest/compare/trades.csv  — combined CSV export
  5. Edge cases: 1 job (error), 11 jobs (error), unknown metric, not-done job
  6. Radar normalization correctness (max=1, min=0, equal values → 0.5)
  7. Diff matrix diagonal is always 0, antisymmetry
  8. Param diff: all_equal correct, differing params first
  9. rank_by lower-is-better (max_drawdown_pct)
  10. Combined CSV column injection (job_id, sym_y, sym_x)

Teste: 52 total
  - TestParseMetrics          (6)
  - TestBuildRadar            (8)
  - TestBuildDiffMatrix       (7)
  - TestBuildParamDiff        (6)
  - TestCompareEndpoint       (13)
  - TestCompareCSVEndpoint    (8)
  - TestCompareRankBy         (4)

Rulare:
  pytest tests/test_sprint18.py -v
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

FIXED_NOW = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()


def _make_metrics(
    sharpe: float = 1.2,
    sortino: float = 1.8,
    calmar: float = 0.6,
    max_drawdown_pct: float = -8.5,
    win_rate: float = 0.54,
    profit_factor: float = 1.35,
    ann_return: float = 1200.0,
    n_trades: int = 45,
    total_net_pnl: float = 980.0,
) -> Dict[str, Any]:
    return dict(
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        max_drawdown_pct=max_drawdown_pct,
        win_rate=win_rate,
        profit_factor=profit_factor,
        ann_return=ann_return,
        n_trades=n_trades,
        total_net_pnl=total_net_pnl,
        ann_volatility=0.12,
        max_drawdown=-850.0,
        n_folds=5,
        overfit_flag=False,
    )


def _make_trades_df(n: int = 20, fold_range: int = 5) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append({
            "fold":         i % fold_range,
            "split":        "OOS" if i % 2 == 0 else "IS",
            "entry_ts":     "2023-01-01T00:00:00Z",
            "exit_ts":      "2023-01-02T00:00:00Z",
            "direction":    "LONG_SPREAD",
            "entry_zscore": 2.1,
            "exit_zscore":  0.3,
            "hedge_ratio":  1.5,
            "qty_y":        0.01,
            "qty_x":        0.015,
            "gross_pnl":    50.0,
            "fees":         1.1,
            "net_pnl":      48.9,
            "bars_held":    12,
            "exit_reason":  "zscore_exit",
        })
    return pd.DataFrame(rows)


def _inject_done_job(
    job_store: Dict,
    job_id: str | None = None,
    sym_y: str = "BTCUSDT",
    sym_x: str = "ETHUSDT",
    bar_freq: str = "1h",
    n_splits: int = 5,
    capital: float = 10000.0,
    zscore_entry: float = 2.0,
    zscore_exit: float = 0.5,
    delta: float = 0.0001,
    vol_target: float = 0.01,
    kelly: float = 0.25,
    metrics: Dict | None = None,
    n_bars: int = 2000,
    trades_df: pd.DataFrame | None = None,
) -> str:
    """
    Injectează direct un job 'done' în _JOBS store, bypassing FastAPI.
    Returnează job_id.
    """
    from api.schemas import BacktestRequest, JobStatus

    jid = job_id or str(uuid.uuid4())[:8]

    req = BacktestRequest(
        sym_y=sym_y,
        sym_x=sym_x,
        bar_freq=bar_freq,
        capital_usdt=capital,
        zscore_entry=zscore_entry,
        zscore_exit=zscore_exit,
        delta=delta,
        vol_target=vol_target,
        kelly_fraction=kelly,
        n_splits=n_splits,
        n_bars=n_bars,
    )
    job_store[jid] = {
        "job_id":       jid,
        "status":       JobStatus.DONE,
        "request":      req,
        "metrics":      metrics or _make_metrics(),
        "trades_df":    trades_df if trades_df is not None else _make_trades_df(),
        "error":        None,
        "duration_s":   1.23,
        "created_at":   FIXED_NOW,
        "completed_at": FIXED_NOW,
    }
    return jid


@pytest.fixture()
def client():
    """Fresh FastAPI app + TestClient with empty _JOBS store for each test."""
    from api import backtest as bt_module

    # Reset store
    bt_module._JOBS.clear()

    app = FastAPI()
    app.include_router(bt_module.router)
    return TestClient(app), bt_module._JOBS


# ===========================================================================
# 1. TestParseMetrics  (6 tests)
# ===========================================================================

class TestParseMetrics:
    def test_default_all_valid(self):
        from api.backtest import _parse_metrics_param, _COMPARE_METRICS_ALL
        result = _parse_metrics_param(",".join(_COMPARE_METRICS_ALL))
        assert result == _COMPARE_METRICS_ALL

    def test_subset_preserved_order(self):
        from api.backtest import _parse_metrics_param
        result = _parse_metrics_param("sortino,sharpe,calmar")
        assert result == ["sortino", "sharpe", "calmar"]

    def test_dedup(self):
        from api.backtest import _parse_metrics_param
        result = _parse_metrics_param("sharpe,sharpe,sortino")
        assert result == ["sharpe", "sortino"]

    def test_empty_returns_all(self):
        from api.backtest import _parse_metrics_param, _COMPARE_METRICS_ALL
        result = _parse_metrics_param("")
        assert result == _COMPARE_METRICS_ALL

    def test_invalid_metric_raises_422(self):
        from fastapi import HTTPException
        from api.backtest import _parse_metrics_param
        with pytest.raises(HTTPException) as exc_info:
            _parse_metrics_param("sharpe,nonexistent_metric")
        assert exc_info.value.status_code == 422
        assert "nonexistent_metric" in str(exc_info.value.detail)

    def test_case_normalized(self):
        from api.backtest import _parse_metrics_param
        result = _parse_metrics_param("SHARPE,Sortino")
        assert result == ["sharpe", "sortino"]


# ===========================================================================
# 2. TestBuildRadar  (8 tests)
# ===========================================================================

class TestBuildRadar:

    def _make_summaries(self, values_list: List[Dict]) -> List:
        """Build minimal JobSummary-like objects."""
        from api.backtest import JobSummary
        summaries = []
        for i, vals in enumerate(values_list):
            summaries.append(JobSummary(
                job_id=f"job{i}",
                sym_y="BTCUSDT",
                sym_x="ETHUSDT",
                bar_freq="1h",
                n_splits=5,
                capital_usdt=10000.0,
                zscore_entry=2.0,
                zscore_exit=0.5,
                delta=0.0001,
                vol_target=0.01,
                kelly_fraction=0.25,
                metrics=vals,
                duration_s=1.0,
                created_at=FIXED_NOW,
            ))
        return summaries

    def test_max_normalized_is_one(self):
        from api.backtest import _build_radar
        summaries = self._make_summaries([
            {"sharpe": 1.0, "sortino": 0.8},
            {"sharpe": 2.0, "sortino": 1.5},
        ])
        radar = _build_radar(summaries, ["sharpe", "sortino"])
        # Job 1 has max sharpe and sortino → normalized = 1.0
        s1 = next(s for s in radar.series if s.job_id == "job1")
        assert s1.values[0] == pytest.approx(1.0)
        assert s1.values[1] == pytest.approx(1.0)

    def test_min_normalized_is_zero(self):
        from api.backtest import _build_radar
        summaries = self._make_summaries([
            {"sharpe": 1.0},
            {"sharpe": 2.0},
        ])
        radar = _build_radar(summaries, ["sharpe"])
        s0 = next(s for s in radar.series if s.job_id == "job0")
        assert s0.values[0] == pytest.approx(0.0)

    def test_equal_values_yield_half(self):
        from api.backtest import _build_radar
        summaries = self._make_summaries([
            {"sharpe": 1.5},
            {"sharpe": 1.5},
        ])
        radar = _build_radar(summaries, ["sharpe"])
        for s in radar.series:
            assert s.values[0] == pytest.approx(0.5)

    def test_lower_is_better_inverted(self):
        """max_drawdown_pct: -20% is worse than -5%; normalized score for -5% should be > -20%."""
        from api.backtest import _build_radar
        summaries = self._make_summaries([
            {"max_drawdown_pct": -20.0},  # worse
            {"max_drawdown_pct": -5.0},   # better
        ])
        radar = _build_radar(summaries, ["max_drawdown_pct"])
        score_worse  = next(s for s in radar.series if s.job_id == "job0").values[0]
        score_better = next(s for s in radar.series if s.job_id == "job1").values[0]
        assert score_better > score_worse

    def test_radar_metrics_order(self):
        from api.backtest import _build_radar
        summaries = self._make_summaries([
            {"calmar": 0.5, "sharpe": 1.0},
            {"calmar": 1.0, "sharpe": 2.0},
        ])
        requested = ["calmar", "sharpe"]
        radar = _build_radar(summaries, requested)
        assert radar.metrics == requested

    def test_raw_min_max_stored(self):
        from api.backtest import _build_radar
        summaries = self._make_summaries([
            {"sharpe": 0.5},
            {"sharpe": 3.0},
        ])
        radar = _build_radar(summaries, ["sharpe"])
        assert radar.raw_min["sharpe"] == pytest.approx(0.5)
        assert radar.raw_max["sharpe"] == pytest.approx(3.0)

    def test_label_format(self):
        from api.backtest import _build_radar
        summaries = self._make_summaries([{"sharpe": 1.0}])
        summaries[0].sym_y = "SOLUSDT"
        summaries[0].sym_x = "BNBUSDT"
        summaries[0].n_splits = 3
        radar = _build_radar(summaries, ["sharpe"])
        assert radar.series[0].label == "SOLUSDT/BNBUSDT 3f"

    def test_three_jobs_values_in_01(self):
        from api.backtest import _build_radar
        summaries = self._make_summaries([
            {"win_rate": 0.40},
            {"win_rate": 0.50},
            {"win_rate": 0.60},
        ])
        radar = _build_radar(summaries, ["win_rate"])
        for s in radar.series:
            assert 0.0 <= s.values[0] <= 1.0


# ===========================================================================
# 3. TestBuildDiffMatrix  (7 tests)
# ===========================================================================

class TestBuildDiffMatrix:

    def _summaries(self):
        from api.backtest import JobSummary
        return [
            JobSummary(job_id="j0", sym_y="BTC", sym_x="ETH", bar_freq="1h",
                       n_splits=5, capital_usdt=10000, zscore_entry=2.0,
                       zscore_exit=0.5, delta=1e-4, vol_target=0.01,
                       kelly_fraction=0.25,
                       metrics={"sharpe": 1.5, "win_rate": 0.55},
                       duration_s=1.0, created_at=FIXED_NOW),
            JobSummary(job_id="j1", sym_y="SOL", sym_x="BNB", bar_freq="1h",
                       n_splits=5, capital_usdt=10000, zscore_entry=2.0,
                       zscore_exit=0.5, delta=1e-4, vol_target=0.01,
                       kelly_fraction=0.25,
                       metrics={"sharpe": 1.0, "win_rate": 0.48},
                       duration_s=1.0, created_at=FIXED_NOW),
        ]

    def test_diagonal_zero(self):
        from api.backtest import _build_diff_matrix
        dm = _build_diff_matrix(self._summaries(), ["sharpe", "win_rate"])
        for i in range(len(dm.job_ids)):
            for m in dm.metrics:
                assert dm.matrix[i][i][m] == pytest.approx(0.0)

    def test_antisymmetry(self):
        from api.backtest import _build_diff_matrix
        dm = _build_diff_matrix(self._summaries(), ["sharpe"])
        assert dm.matrix[0][1]["sharpe"] == pytest.approx(-dm.matrix[1][0]["sharpe"])

    def test_correct_diff_value(self):
        from api.backtest import _build_diff_matrix
        dm = _build_diff_matrix(self._summaries(), ["sharpe"])
        # j0.sharpe - j1.sharpe = 1.5 - 1.0 = 0.5
        assert dm.matrix[0][1]["sharpe"] == pytest.approx(0.5)

    def test_win_rate_diff(self):
        from api.backtest import _build_diff_matrix
        dm = _build_diff_matrix(self._summaries(), ["win_rate"])
        # j0.win_rate - j1.win_rate = 0.55 - 0.48 = 0.07
        assert dm.matrix[0][1]["win_rate"] == pytest.approx(0.07, abs=1e-5)

    def test_job_ids_order_preserved(self):
        from api.backtest import _build_diff_matrix
        dm = _build_diff_matrix(self._summaries(), ["sharpe"])
        assert dm.job_ids == ["j0", "j1"]

    def test_metrics_field_correct(self):
        from api.backtest import _build_diff_matrix
        dm = _build_diff_matrix(self._summaries(), ["sharpe", "win_rate"])
        assert dm.metrics == ["sharpe", "win_rate"]

    def test_matrix_size(self):
        from api.backtest import _build_diff_matrix
        dm = _build_diff_matrix(self._summaries(), ["sharpe"])
        n = len(dm.job_ids)
        assert len(dm.matrix) == n
        assert all(len(row) == n for row in dm.matrix)


# ===========================================================================
# 4. TestBuildParamDiff  (6 tests)
# ===========================================================================

class TestBuildParamDiff:

    def _make_summary(self, job_id, sym_y="BTC", zscore_entry=2.0, kelly=0.25, delta=1e-4):
        from api.backtest import JobSummary
        return JobSummary(
            job_id=job_id, sym_y=sym_y, sym_x="ETH", bar_freq="1h",
            n_splits=5, capital_usdt=10000, zscore_entry=zscore_entry,
            zscore_exit=0.5, delta=delta, vol_target=0.01,
            kelly_fraction=kelly,
            metrics={}, duration_s=1.0, created_at=FIXED_NOW,
        )

    def test_all_equal_when_identical(self):
        from api.backtest import _build_param_diff
        s1 = self._make_summary("j0")
        s2 = self._make_summary("j1")
        result = _build_param_diff([s1, s2])
        # sym_y both "BTC", zscore_entry both 2.0, etc. → all_equal for these
        sym_field = next(p for p in result if p.param == "sym_y")
        assert sym_field.all_equal is True

    def test_differing_sym_y_detected(self):
        from api.backtest import _build_param_diff
        s1 = self._make_summary("j0", sym_y="BTC")
        s2 = self._make_summary("j1", sym_y="SOL")
        result = _build_param_diff([s1, s2])
        sym_field = next(p for p in result if p.param == "sym_y")
        assert sym_field.all_equal is False

    def test_differing_params_first(self):
        from api.backtest import _build_param_diff
        s1 = self._make_summary("j0", zscore_entry=2.0, kelly=0.25)
        s2 = self._make_summary("j1", zscore_entry=2.5, kelly=0.30)
        result = _build_param_diff([s1, s2])
        # differing (all_equal=False) should come before equal ones
        diff_group = [p for p in result if not p.all_equal]
        equal_group = [p for p in result if p.all_equal]
        assert result.index(diff_group[0]) < result.index(equal_group[0])

    def test_values_dict_keyed_by_job_id(self):
        from api.backtest import _build_param_diff
        s1 = self._make_summary("jobA", zscore_entry=2.0)
        s2 = self._make_summary("jobB", zscore_entry=2.5)
        result = _build_param_diff([s1, s2])
        ze = next(p for p in result if p.param == "zscore_entry")
        assert "jobA" in ze.values
        assert "jobB" in ze.values
        assert ze.values["jobA"] == pytest.approx(2.0)
        assert ze.values["jobB"] == pytest.approx(2.5)

    def test_ten_param_fields_returned(self):
        from api.backtest import _build_param_diff
        s1 = self._make_summary("j0")
        s2 = self._make_summary("j1")
        result = _build_param_diff([s1, s2])
        assert len(result) == 10  # 10 PARAM_FIELDS defined

    def test_delta_difference_detected(self):
        from api.backtest import _build_param_diff
        s1 = self._make_summary("j0", delta=1e-4)
        s2 = self._make_summary("j1", delta=5e-4)
        result = _build_param_diff([s1, s2])
        delta_field = next(p for p in result if p.param == "delta")
        assert delta_field.all_equal is False


# ===========================================================================
# 5. TestCompareEndpoint  (13 tests)
# ===========================================================================

class TestCompareEndpoint:

    def test_two_jobs_200(self, client):
        tc, store = client
        j1 = _inject_done_job(store, metrics=_make_metrics(sharpe=1.5))
        j2 = _inject_done_job(store, metrics=_make_metrics(sharpe=0.8))
        resp = tc.get(f"/api/backtest/compare?job_ids={j1},{j2}")
        assert resp.status_code == 200

    def test_response_schema_fields(self, client):
        tc, store = client
        j1 = _inject_done_job(store)
        j2 = _inject_done_job(store)
        resp = tc.get(f"/api/backtest/compare?job_ids={j1},{j2}").json()
        for field in ["job_ids", "ranking", "summary", "radar",
                      "diff_matrix", "param_diff", "best_job_id",
                      "comparison_ts", "requested_metrics", "rank_by"]:
            assert field in resp, f"Missing field: {field}"

    def test_best_job_id_is_highest_sharpe(self, client):
        tc, store = client
        j1 = _inject_done_job(store, metrics=_make_metrics(sharpe=0.5))
        j2 = _inject_done_job(store, metrics=_make_metrics(sharpe=2.0))
        resp = tc.get(f"/api/backtest/compare?job_ids={j1},{j2}").json()
        assert resp["best_job_id"] == j2

    def test_ranking_order(self, client):
        tc, store = client
        j1 = _inject_done_job(store, metrics=_make_metrics(sharpe=0.3))
        j2 = _inject_done_job(store, metrics=_make_metrics(sharpe=1.8))
        j3 = _inject_done_job(store, metrics=_make_metrics(sharpe=1.1))
        resp = tc.get(f"/api/backtest/compare?job_ids={j1},{j2},{j3}").json()
        assert resp["ranking"] == [j2, j3, j1]

    def test_single_job_returns_422(self, client):
        tc, store = client
        j1 = _inject_done_job(store)
        resp = tc.get(f"/api/backtest/compare?job_ids={j1}")
        assert resp.status_code == 422

    def test_eleven_jobs_returns_422(self, client):
        tc, store = client
        ids = [_inject_done_job(store) for _ in range(11)]
        resp = tc.get(f"/api/backtest/compare?job_ids={','.join(ids)}")
        assert resp.status_code == 422

    def test_unknown_job_id_returns_404(self, client):
        tc, store = client
        j1 = _inject_done_job(store)
        resp = tc.get(f"/api/backtest/compare?job_ids={j1},nonexistent")
        assert resp.status_code == 404

    def test_not_done_job_returns_409(self, client):
        tc, store = client
        from api.schemas import JobStatus, BacktestRequest
        j1 = _inject_done_job(store)
        # Inject a queued job directly
        qjid = "queued1"
        req = BacktestRequest(sym_y="BTCUSDT", sym_x="ETHUSDT")
        store[qjid] = {
            "job_id": qjid, "status": JobStatus.QUEUED, "request": req,
            "metrics": None, "trades_df": None, "error": None,
            "duration_s": None, "created_at": FIXED_NOW, "completed_at": None,
        }
        resp = tc.get(f"/api/backtest/compare?job_ids={j1},{qjid}")
        assert resp.status_code == 409

    def test_invalid_metric_returns_422(self, client):
        tc, store = client
        j1 = _inject_done_job(store)
        j2 = _inject_done_job(store)
        resp = tc.get(
            f"/api/backtest/compare?job_ids={j1},{j2}&metrics=sharpe,fakemetric"
        )
        assert resp.status_code == 422

    def test_invalid_rank_by_returns_422(self, client):
        tc, store = client
        j1 = _inject_done_job(store)
        j2 = _inject_done_job(store)
        resp = tc.get(
            f"/api/backtest/compare?job_ids={j1},{j2}&rank_by=notametric"
        )
        assert resp.status_code == 422

    def test_radar_series_count(self, client):
        tc, store = client
        j1 = _inject_done_job(store)
        j2 = _inject_done_job(store)
        j3 = _inject_done_job(store)
        resp = tc.get(
            f"/api/backtest/compare?job_ids={j1},{j2},{j3}&metrics=sharpe,sortino"
        ).json()
        assert len(resp["radar"]["series"]) == 3

    def test_diff_matrix_dimension(self, client):
        tc, store = client
        j1 = _inject_done_job(store)
        j2 = _inject_done_job(store)
        resp = tc.get(
            f"/api/backtest/compare?job_ids={j1},{j2}&metrics=sharpe"
        ).json()
        dm = resp["diff_matrix"]
        assert len(dm["matrix"]) == 2
        assert len(dm["matrix"][0]) == 2
        # diagonal
        assert dm["matrix"][0][0]["sharpe"] == pytest.approx(0.0)
        assert dm["matrix"][1][1]["sharpe"] == pytest.approx(0.0)

    def test_rank_by_auto_added_to_metrics(self, client):
        tc, store = client
        j1 = _inject_done_job(store, metrics=_make_metrics(sortino=1.2))
        j2 = _inject_done_job(store, metrics=_make_metrics(sortino=2.5))
        # Request only 'calmar' but rank_by sortino — sortino should be auto-added
        resp = tc.get(
            f"/api/backtest/compare?job_ids={j1},{j2}&metrics=calmar&rank_by=sortino"
        ).json()
        assert "sortino" in resp["requested_metrics"]


# ===========================================================================
# 6. TestCompareCSVEndpoint  (8 tests)
# ===========================================================================

class TestCompareCSVEndpoint:

    def test_200_and_csv_content_type(self, client):
        tc, store = client
        j1 = _inject_done_job(store)
        j2 = _inject_done_job(store)
        resp = tc.get(f"/api/backtest/compare/trades.csv?job_ids={j1},{j2}")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    def test_content_disposition_attachment(self, client):
        tc, store = client
        j1 = _inject_done_job(store)
        j2 = _inject_done_job(store)
        resp = tc.get(f"/api/backtest/compare/trades.csv?job_ids={j1},{j2}")
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert ".csv" in cd

    def test_job_id_column_injected(self, client):
        tc, store = client
        j1 = _inject_done_job(store, trades_df=_make_trades_df(5))
        j2 = _inject_done_job(store, trades_df=_make_trades_df(5))
        resp = tc.get(f"/api/backtest/compare/trades.csv?job_ids={j1},{j2}")
        df = pd.read_csv(pd.io.common.StringIO(resp.text))
        assert "job_id" in df.columns
        assert set(df["job_id"].unique()) == {j1, j2}

    def test_sym_y_sym_x_columns_injected(self, client):
        tc, store = client
        j1 = _inject_done_job(store, sym_y="BTCUSDT", sym_x="ETHUSDT")
        j2 = _inject_done_job(store, sym_y="SOLUSDT", sym_x="BNBUSDT")
        resp = tc.get(f"/api/backtest/compare/trades.csv?job_ids={j1},{j2}")
        df = pd.read_csv(pd.io.common.StringIO(resp.text))
        assert "sym_y" in df.columns and "sym_x" in df.columns
        assert "BTCUSDT" in df["sym_y"].values
        assert "SOLUSDT" in df["sym_y"].values

    def test_split_filter_oos(self, client):
        tc, store = client
        j1 = _inject_done_job(store, trades_df=_make_trades_df(20))
        j2 = _inject_done_job(store, trades_df=_make_trades_df(20))
        resp = tc.get(
            f"/api/backtest/compare/trades.csv?job_ids={j1},{j2}&split=OOS"
        )
        df = pd.read_csv(pd.io.common.StringIO(resp.text))
        assert set(df["split"].unique()) == {"OOS"}

    def test_split_filter_is(self, client):
        tc, store = client
        j1 = _inject_done_job(store, trades_df=_make_trades_df(20))
        j2 = _inject_done_job(store, trades_df=_make_trades_df(20))
        resp = tc.get(
            f"/api/backtest/compare/trades.csv?job_ids={j1},{j2}&split=IS"
        )
        df = pd.read_csv(pd.io.common.StringIO(resp.text))
        assert set(df["split"].unique()) == {"IS"}

    def test_single_job_returns_422(self, client):
        tc, store = client
        j1 = _inject_done_job(store)
        resp = tc.get(f"/api/backtest/compare/trades.csv?job_ids={j1}")
        assert resp.status_code == 422

    def test_combined_row_count(self, client):
        tc, store = client
        j1 = _inject_done_job(store, trades_df=_make_trades_df(10))
        j2 = _inject_done_job(store, trades_df=_make_trades_df(15))
        resp = tc.get(f"/api/backtest/compare/trades.csv?job_ids={j1},{j2}")
        df = pd.read_csv(pd.io.common.StringIO(resp.text))
        assert len(df) == 25


# ===========================================================================
# 7. TestCompareRankBy  (4 tests)
# ===========================================================================

class TestCompareRankBy:

    def test_rank_by_sortino(self, client):
        tc, store = client
        j1 = _inject_done_job(store, metrics=_make_metrics(sortino=0.6))
        j2 = _inject_done_job(store, metrics=_make_metrics(sortino=2.1))
        resp = tc.get(
            f"/api/backtest/compare?job_ids={j1},{j2}&rank_by=sortino"
        ).json()
        assert resp["best_job_id"] == j2
        assert resp["ranking"][0] == j2

    def test_rank_by_max_drawdown_pct_lower_is_better(self, client):
        """max_drawdown_pct: -3% (less bad) should rank above -20%."""
        tc, store = client
        j1 = _inject_done_job(store, metrics=_make_metrics(max_drawdown_pct=-20.0))
        j2 = _inject_done_job(store, metrics=_make_metrics(max_drawdown_pct=-3.0))
        resp = tc.get(
            f"/api/backtest/compare?job_ids={j1},{j2}&rank_by=max_drawdown_pct"
        ).json()
        assert resp["best_job_id"] == j2

    def test_rank_by_win_rate(self, client):
        tc, store = client
        j1 = _inject_done_job(store, metrics=_make_metrics(win_rate=0.45))
        j2 = _inject_done_job(store, metrics=_make_metrics(win_rate=0.62))
        resp = tc.get(
            f"/api/backtest/compare?job_ids={j1},{j2}&rank_by=win_rate"
        ).json()
        assert resp["best_job_id"] == j2

    def test_rank_by_profit_factor(self, client):
        tc, store = client
        j1 = _inject_done_job(store, metrics=_make_metrics(profit_factor=0.9))
        j2 = _inject_done_job(store, metrics=_make_metrics(profit_factor=1.6))
        resp = tc.get(
            f"/api/backtest/compare?job_ids={j1},{j2}&rank_by=profit_factor"
        ).json()
        assert resp["best_job_id"] == j2
