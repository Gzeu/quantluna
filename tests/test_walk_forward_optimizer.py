"""
QuantLuna — Tests: backtest/walk_forward_optimizer.py
Sprint 25  |  8 tests

Coverage:
  TestFoldGeneration (2)    — correct number + boundaries
  TestGridExpansion (1)     — cartesian product
  TestOptimizeResult (3)    — run on small synthetic series
  TestBestParams (2)        — best_global + best_by_regime non-empty
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.walk_forward_optimizer import WalkForwardOptimizer, _run_fold, _expand_grid


def _make_series(n: int = 800, seed: int = 0):
    rng = np.random.default_rng(seed)
    y = pd.Series(100.0 + np.cumsum(rng.normal(0, 1.0, n)))
    x = pd.Series(50.0  + np.cumsum(rng.normal(0, 0.5, n)))
    return y, x


class TestFoldGeneration:

    def test_correct_fold_count(self):
        opt = WalkForwardOptimizer(train_bars=200, test_bars=50, step_bars=50)
        folds = opt._make_folds(500)
        # (500 - 250) / 50 = 5 folds
        assert len(folds) == 5

    def test_fold_boundaries_non_overlapping_test(self):
        opt = WalkForwardOptimizer(train_bars=200, test_bars=50, step_bars=50)
        folds = opt._make_folds(500)
        for i in range(len(folds) - 1):
            _, _, _, te_e = folds[i]
            _, _, te_s_next, _ = folds[i + 1]
            # Test windows should not overlap
            assert te_e <= te_s_next


class TestGridExpansion:

    def test_cartesian_product(self):
        grid = {"a": [1, 2], "b": [10, 20]}
        combos = WalkForwardOptimizer._expand_grid(grid)
        assert len(combos) == 4
        assert all(set(c.keys()) == {"a", "b"} for c in combos)


class TestOptimizeResult:

    def test_run_returns_result(self):
        y, x = _make_series(800)
        opt = WalkForwardOptimizer(train_bars=200, test_bars=50, step_bars=100, n_jobs=1)
        result = opt.run(y=y, x=x, param_grid={"zscore_window": [10, 20], "zscore_entry": [1.5, 2.0], "regime_min_persistence": [2]})
        assert result.n_folds >= 1
        assert result.result_df is not None
        assert len(result.result_df) > 0

    def test_fold_results_count(self):
        y, x = _make_series(800)
        opt = WalkForwardOptimizer(train_bars=200, test_bars=50, step_bars=100, n_jobs=1)
        result = opt.run(y=y, x=x, param_grid={"zscore_window": [10, 20], "zscore_entry": [2.0], "regime_min_persistence": [2]})
        assert len(result.fold_results) == result.n_folds

    def test_series_too_short_raises(self):
        y, x = _make_series(100)
        opt = WalkForwardOptimizer(train_bars=500, test_bars=100)
        with pytest.raises(ValueError, match="too short"):
            opt.run(y=y, x=x)


class TestBestParams:

    def test_best_params_global_non_empty(self):
        y, x = _make_series(800)
        opt = WalkForwardOptimizer(train_bars=200, test_bars=50, step_bars=200, n_jobs=1)
        result = opt.run(y=y, x=x, param_grid={"zscore_window": [10, 20], "zscore_entry": [2.0], "regime_min_persistence": [2]})
        assert isinstance(result.best_params_global, dict)
        assert len(result.best_params_global) > 0

    def test_best_params_by_regime_keys(self):
        y, x = _make_series(800)
        opt = WalkForwardOptimizer(train_bars=200, test_bars=50, step_bars=200, n_jobs=1)
        result = opt.run(y=y, x=x, param_grid={"zscore_window": [10, 20], "zscore_entry": [2.0], "regime_min_persistence": [2]})
        assert isinstance(result.best_params_by_regime, dict)
        # All keys must be valid regime strings
        valid = {"ranging", "trending", "breakout", "unknown"}
        for key in result.best_params_by_regime.keys():
            assert key in valid
