"""
Tests for backtest/walk_forward.py -- Sprint 2
Uses stub BacktestEngine to isolate WalkForwardValidator logic.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from backtest.walk_forward import WalkForwardValidator


# --- Stubs --------------------------------------------------------------------

def _make_series(n: int = 1500, seed: int = 42):
    rng = np.random.default_rng(seed)
    beta = 1.2
    x = pd.Series(30000 + np.cumsum(rng.normal(0, 50, n)))
    y = beta * x + 5000 + rng.normal(0, 300, n)
    return y, x


class _FakeTrade:
    def __init__(self, pnl: float):
        self.pnl_net = pnl


def _make_fake_result(n_trades: int = 20, sharpe: float = 1.2):
    rng = np.random.default_rng(0)
    trades = [_FakeTrade(float(p)) for p in rng.normal(10, 30, n_trades)]
    equity = list(np.cumsum(rng.normal(5, 20, 50)) + 10000)
    metrics = {
        "sharpe": sharpe, "max_drawdown": -0.05, "n_trades": n_trades,
        "sortino": 1.5, "calmar": 2.0, "win_rate": 0.55,
    }
    return {"metrics": metrics, "trades": trades, "equity": equity}


# --- _build_folds -------------------------------------------------------------

class TestBuildFolds:
    def test_rolling_correct_count(self):
        wf = WalkForwardValidator(train_periods=100, test_periods=50, embargo_bars=0)
        assert len(wf._build_folds(400)) == 6

    def test_anchored_train_expands(self):
        wf = WalkForwardValidator(train_periods=100, test_periods=50, anchored=True, embargo_bars=0)
        for train_idx, _ in wf._build_folds(300):
            assert train_idx.start == 0

    def test_rolling_train_slides(self):
        wf = WalkForwardValidator(train_periods=100, test_periods=50, anchored=False, embargo_bars=0)
        for i, (train_idx, _) in enumerate(wf._build_folds(300)):
            assert train_idx.start == i * 50

    def test_embargo_creates_gap(self):
        embargo = 15
        wf = WalkForwardValidator(train_periods=100, test_periods=50, embargo_bars=embargo)
        for train_idx, test_idx in wf._build_folds(300):
            assert test_idx.start == train_idx.stop + embargo

    def test_no_folds_when_insufficient_data(self):
        wf = WalkForwardValidator(train_periods=500, test_periods=100)
        assert wf._build_folds(400) == []

    def test_embargo_reduces_fold_count(self):
        wf_no = WalkForwardValidator(train_periods=100, test_periods=50, embargo_bars=0)
        wf_em = WalkForwardValidator(train_periods=100, test_periods=50, embargo_bars=10)
        assert len(wf_em._build_folds(500)) <= len(wf_no._build_folds(500))


# --- WalkForwardValidator.run -------------------------------------------------

class TestWalkForwardRun:
    def _run_patched(self, wf, y, x, is_sharpe=1.2, oos_sharpe=0.9):
        call = {"n": 0}

        def fake_run(self_eng, y_data, x_data, funding_rate=None, freq_hours=1.0):
            call["n"] += 1
            sharpe = is_sharpe if call["n"] % 2 == 1 else oos_sharpe
            return _make_fake_result(n_trades=10, sharpe=sharpe)

        with patch("backtest.walk_forward.BacktestEngine") as MockEng:
            instance = MockEng.return_value
            instance.run.side_effect = lambda *a, **kw: fake_run(instance, *a, **kw)
            return wf.run(y, x, freq_hours=1.0)

    def test_returns_expected_keys(self):
        y, x = _make_series(1500)
        wf = WalkForwardValidator(train_periods=300, test_periods=100, embargo_bars=0)
        result = self._run_patched(wf, y, x)
        for key in ["combined", "per_fold", "oos_trades", "overfit_flag", "median_is_sharpe"]:
            assert key in result

    def test_correct_n_folds(self):
        y, x = _make_series(1500)
        wf = WalkForwardValidator(train_periods=300, test_periods=100, embargo_bars=0)
        result = self._run_patched(wf, y, x)
        assert result["combined"]["n_folds"] == len(wf._build_folds(len(y)))

    def test_raises_on_insufficient_data(self):
        y, x = _make_series(100)
        wf = WalkForwardValidator(train_periods=500, test_periods=200)
        with pytest.raises(ValueError, match="Not enough bars"):
            wf.run(y, x)

    def test_overfit_flag_set(self):
        y, x = _make_series(1500)
        wf = WalkForwardValidator(train_periods=300, test_periods=100)
        result = self._run_patched(wf, y, x, is_sharpe=2.0, oos_sharpe=0.3)
        assert result["overfit_flag"] is True

    def test_overfit_flag_not_set(self):
        y, x = _make_series(1500)
        wf = WalkForwardValidator(train_periods=300, test_periods=100)
        result = self._run_patched(wf, y, x, is_sharpe=1.2, oos_sharpe=0.9)
        assert result["overfit_flag"] is False


# --- monte_carlo --------------------------------------------------------------

class TestMonteCarlo:
    def _trades(self, n=100, mean=10.0, std=30.0, seed=42):
        rng = np.random.default_rng(seed)
        return [_FakeTrade(float(p)) for p in rng.normal(mean, std, n)]

    def test_returns_expected_keys(self):
        wf = WalkForwardValidator()
        result = wf.monte_carlo(self._trades(100), n_simulations=200, seed=0)
        for k in ["n_simulations", "n_trades", "prob_profit", "median_final_equity",
                  "median_max_dd", "p95_max_dd", "median_sharpe",
                  "p05_final_equity", "p25_final_equity", "p75_final_equity", "p95_final_equity"]:
            assert k in result

    def test_prob_profit_in_range(self):
        wf = WalkForwardValidator()
        r = wf.monte_carlo(self._trades(200, mean=20.0, std=10.0), n_simulations=500, seed=1)
        assert 0.0 <= r["prob_profit"] <= 1.0

    def test_percentiles_ordered(self):
        wf = WalkForwardValidator()
        r = wf.monte_carlo(self._trades(100), n_simulations=300, seed=2)
        assert r["p05_final_equity"] <= r["p25_final_equity"] <= r["p75_final_equity"] <= r["p95_final_equity"]

    def test_max_dd_nonpositive(self):
        wf = WalkForwardValidator()
        r = wf.monte_carlo(self._trades(100), n_simulations=200, seed=3)
        assert r["median_max_dd"] <= 0.0

    def test_insufficient_trades(self):
        wf = WalkForwardValidator()
        r = wf.monte_carlo(self._trades(3))
        assert r["error"] == "insufficient_trades"

    def test_reproducible_with_seed(self):
        wf = WalkForwardValidator()
        t = self._trades(80, seed=10)
        r1 = wf.monte_carlo(t, n_simulations=500, seed=42)
        r2 = wf.monte_carlo(t, n_simulations=500, seed=42)
        assert r1["median_final_equity"] == r2["median_final_equity"]

    def test_different_seeds_differ(self):
        wf = WalkForwardValidator()
        t = self._trades(80, seed=10)
        r1 = wf.monte_carlo(t, n_simulations=500, seed=1)
        r2 = wf.monte_carlo(t, n_simulations=500, seed=2)
        assert r1["median_final_equity"] != r2["median_final_equity"]

    def test_n_trades_matches_input(self):
        wf = WalkForwardValidator()
        r = wf.monte_carlo(self._trades(60), n_simulations=100, seed=5)
        assert r["n_trades"] == 60
