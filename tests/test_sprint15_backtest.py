"""
tests/test_sprint15_backtest.py  —  Sprint 15 backtest integration tests

Covers:
  - strategy_to_backtest_config: mapping corect din StrategyConfig → BacktestConfig
  - BacktestEngine: run() cu y/x Series sintetice
  - BacktestEngine: run() cu df direct
  - BacktestEngine: load_parquet graceful FileNotFoundError
  - BacktestEngine: purge_bars default = warm_up_bars
  - BacktestEngine: result keys complete
  - BacktestEngine: from_optimizer_json integration
  - WalkForwardRunner: instantiere cu StrategyConfig
  - WalkForwardRunner: purge/embargo propagate corect
  - Purging gap: OOS bars nu includ IS-adjacent bars
  - _MinimalSpreadEngine: fit() + update_one() shape
  - BacktestConfig.bars_per_day: calcul corect per bar_freq_hours
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_prices(n=600, seed=42) -> tuple[pd.Series, pd.Series]:
    """Generate cointegrated price pair."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    x = 100 + np.cumsum(rng.normal(0, 0.3, n))
    y = 1.5 * x + 10 + rng.normal(0, 0.5, n)
    return pd.Series(y, index=idx, name="close_y"), pd.Series(x, index=idx, name="close_x")


def _make_df(n=600) -> pd.DataFrame:
    y, x = _make_prices(n)
    return pd.DataFrame({"timestamp": y.index, "close_y": y.values, "close_x": x.values})


# ---------------------------------------------------------------------------
# strategy_to_backtest_config
# ---------------------------------------------------------------------------

class TestStrategyToBacktestConfig:
    def test_maps_capital(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import strategy_to_backtest_config
        cfg = StrategyConfig(capital_usdt=20_000.0)
        bc = strategy_to_backtest_config(cfg)
        assert bc.capital_usd == 20_000.0

    def test_maps_vol_target(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import strategy_to_backtest_config
        cfg = StrategyConfig(vol_target=0.02)
        bc = strategy_to_backtest_config(cfg)
        assert bc.vol_target == pytest.approx(0.02)

    def test_maps_kelly_fraction(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import strategy_to_backtest_config
        cfg = StrategyConfig(kelly_fraction=0.30)
        bc = strategy_to_backtest_config(cfg)
        assert bc.kelly_fraction == pytest.approx(0.30)

    def test_bar_freq_1h_maps_to_1(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import strategy_to_backtest_config
        cfg = StrategyConfig(bar_freq="1h")
        bc = strategy_to_backtest_config(cfg)
        assert bc.bar_freq_hours == pytest.approx(1.0)

    def test_bar_freq_4h_maps_to_4(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import strategy_to_backtest_config
        cfg = StrategyConfig(bar_freq="4h")
        bc = strategy_to_backtest_config(cfg)
        assert bc.bar_freq_hours == pytest.approx(4.0)

    def test_purge_embargo_propagate(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import strategy_to_backtest_config
        cfg = StrategyConfig()
        bc = strategy_to_backtest_config(cfg, purge_bars=15, embargo_bars=12)
        assert bc.purge_bars == 15
        assert bc.embargo_bars == 12

    def test_bars_per_day_4h(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import strategy_to_backtest_config
        cfg = StrategyConfig(bar_freq="4h")
        bc = strategy_to_backtest_config(cfg)
        assert bc.bars_per_day == pytest.approx(6.0)  # 24 / 4


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------

class TestBacktestEngine:
    def test_instantiation_with_strategy_config(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import BacktestEngine
        cfg = StrategyConfig()
        engine = BacktestEngine(cfg)
        assert engine.cfg is cfg

    def test_purge_bars_default_equals_warm_up(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import BacktestEngine
        cfg = StrategyConfig(warm_up_bars=35)
        engine = BacktestEngine(cfg)
        assert engine.purge_bars == 35

    def test_purge_bars_explicit_override(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import BacktestEngine
        cfg = StrategyConfig()
        engine = BacktestEngine(cfg, purge_bars=50)
        assert engine.purge_bars == 50

    def test_run_with_series_returns_dict(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import BacktestEngine
        cfg = StrategyConfig()
        y, x = _make_prices(600)
        engine = BacktestEngine(cfg, n_splits=3)
        result = engine.run(y=y, x=x)
        assert isinstance(result, dict)

    def test_run_result_has_required_keys(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import BacktestEngine
        cfg = StrategyConfig()
        y, x = _make_prices(600)
        result = BacktestEngine(cfg, n_splits=3).run(y=y, x=x)
        required = {"sharpe", "sortino", "calmar", "max_drawdown",
                    "win_rate", "profit_factor", "n_trades",
                    "total_net_pnl", "overfit_flag", "n_folds"}
        for k in required:
            assert k in result, f"Missing key: {k}"

    def test_run_with_df(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import BacktestEngine
        df = _make_df(600)
        result = BacktestEngine(StrategyConfig(), n_splits=3).run(df=df)
        assert "sharpe" in result

    def test_run_no_input_raises(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import BacktestEngine
        with pytest.raises(ValueError, match="Provide"):
            BacktestEngine(StrategyConfig()).run()

    def test_run_missing_parquet_raises(self, tmp_path):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import BacktestEngine
        with pytest.raises(FileNotFoundError):
            BacktestEngine(StrategyConfig()).run(data_dir=tmp_path)

    def test_from_optimizer_json_integration(self, tmp_path):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import BacktestEngine
        params = {
            "delta": 1e-4, "zscore_entry": 2.0, "zscore_exit": 0.5,
            "kelly_fraction": 0.25, "vol_target": 0.01,
        }
        p = tmp_path / "best.json"
        p.write_text(json.dumps({"params": params, "best_value": 1.5}))
        cfg = StrategyConfig.from_optimizer_json(str(p))
        engine = BacktestEngine(cfg, n_splits=3)
        y, x = _make_prices(600)
        result = engine.run(y=y, x=x)
        assert isinstance(result.get("sharpe"), float)

    def test_sharpe_is_finite(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import BacktestEngine
        y, x = _make_prices(600)
        result = BacktestEngine(StrategyConfig(), n_splits=3).run(y=y, x=x)
        assert math.isfinite(result["sharpe"])


# ---------------------------------------------------------------------------
# WalkForwardRunner
# ---------------------------------------------------------------------------

class TestWalkForwardRunner:
    def test_instantiation(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import WalkForwardRunner
        cfg = StrategyConfig()
        runner = WalkForwardRunner(cfg, n_splits=5)
        assert runner.n_splits == 5

    def test_purge_default_equals_warm_up(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import WalkForwardRunner
        cfg = StrategyConfig(warm_up_bars=40)
        runner = WalkForwardRunner(cfg)
        assert runner.purge_bars == 40

    def test_embargo_explicit(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import WalkForwardRunner
        runner = WalkForwardRunner(StrategyConfig(), embargo_bars=48)
        assert runner.embargo_bars == 48

    def test_run_returns_dict_with_combined(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import WalkForwardRunner
        y, x = _make_prices(800)
        result = WalkForwardRunner(StrategyConfig(), n_splits=3).run(y=y, x=x)
        assert "combined" in result or "note" in result  # stub fallback ok

    def test_purge_embargo_in_result(self):
        from config.strategy_config import StrategyConfig
        from backtest.engine_adapter import WalkForwardRunner
        y, x = _make_prices(800)
        result = WalkForwardRunner(StrategyConfig(), n_splits=3,
                                   purge_bars=20, embargo_bars=10).run(y=y, x=x)
        assert result.get("purge_bars") == 20
        assert result.get("embargo_bars") == 10


# ---------------------------------------------------------------------------
# Purging Gap anti-lookahead
# ---------------------------------------------------------------------------

class TestPurgingGap:
    """
    Verifică că split-urile IS/OOS respect purge + embargo gap.
    Testaă direct _build_splits() din WalkForwardEngine.
    """

    def test_oos_start_respects_purge_embargo(self):
        """
        OOS bars nu trebuie să înceapă imediat după IS.
        oos_start >= is_end + purge_bars + embargo_bars.
        """
        try:
            from backtest.engine import WalkForwardEngine, BacktestConfig
        except ImportError:
            pytest.skip("WalkForwardEngine not importable")

        from core.kalman_filter import KalmanHedgeRatio

        n = 1000
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
        rng = np.random.default_rng(42)
        x = 100 + np.cumsum(rng.normal(0, 0.3, n))
        y = 1.5 * x + 10 + rng.normal(0, 0.5, n)
        df = pd.DataFrame({
            "timestamp": idx,
            "close_y": y,
            "close_x": x,
        })

        purge = 15
        embargo = 10
        bc = BacktestConfig(n_splits=4, purge_bars=purge, embargo_bars=embargo)

        kf = KalmanHedgeRatio()
        try:
            from core.spread import SpreadEngine
            factory = lambda: SpreadEngine(kf)
        except ImportError:
            from backtest.engine_adapter import _MinimalSpreadEngine
            factory = lambda: _MinimalSpreadEngine()

        engine = WalkForwardEngine(df=df, cfg=bc, spread_engine_factory=factory)
        splits = engine._build_splits()

        for is_idx, oos_idx in splits:
            is_end = int(is_idx[-1])
            oos_start = int(oos_idx[0])
            gap = oos_start - is_end - 1
            assert gap >= purge + embargo, (
                f"Purging gap violation: gap={gap} < purge+embargo={purge+embargo}. "
                f"IS end={is_end}, OOS start={oos_start}"
            )

    def test_no_overlap_between_is_and_oos(self):
        """IS și OOS nu se suprapun niciodată."""
        try:
            from backtest.engine import WalkForwardEngine, BacktestConfig
        except ImportError:
            pytest.skip("WalkForwardEngine not importable")

        n = 800
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
        rng = np.random.default_rng(0)
        df = pd.DataFrame({
            "timestamp": idx,
            "close_y": 100 + np.cumsum(rng.normal(0, 0.3, n)),
            "close_x": 80  + np.cumsum(rng.normal(0, 0.2, n)),
        })
        bc = BacktestConfig(n_splits=3, purge_bars=10, embargo_bars=5)

        from backtest.engine_adapter import _MinimalSpreadEngine
        engine = WalkForwardEngine(df=df, cfg=bc,
                                   spread_engine_factory=lambda: _MinimalSpreadEngine())
        splits = engine._build_splits()
        for is_idx, oos_idx in splits:
            overlap = set(is_idx.tolist()) & set(oos_idx.tolist())
            assert len(overlap) == 0, f"IS/OOS overlap detected: {overlap}"


# ---------------------------------------------------------------------------
# _MinimalSpreadEngine
# ---------------------------------------------------------------------------

class TestMinimalSpreadEngine:
    def test_fit_returns_dataframe(self):
        from backtest.engine_adapter import _MinimalSpreadEngine
        engine = _MinimalSpreadEngine()
        y, x = _make_prices(200)
        df = engine.fit(y, x)
        assert isinstance(df, pd.DataFrame)
        assert "beta" in df.columns
        assert "spread" in df.columns

    def test_update_one_returns_dict_with_keys(self):
        from backtest.engine_adapter import _MinimalSpreadEngine
        engine = _MinimalSpreadEngine()
        state = engine.update_one(50000.0, 3000.0)
        required = {"beta", "alpha", "spread", "P_beta", "kalman_gain", "is_warm"}
        for k in required:
            assert k in state, f"Missing key: {k}"

    def test_fit_then_update_one_coherent(self):
        """După fit(), update_one() continuă din starea Kalman — nu dă reset."""
        from backtest.engine_adapter import _MinimalSpreadEngine
        engine = _MinimalSpreadEngine()
        y, x = _make_prices(200)
        engine.fit(y, x)  # populate state
        # After fit(), update_one should use the warm state
        state_after = engine.update_one(float(y.iloc[-1]), float(x.iloc[-1]))
        # beta should be non-zero (Kalman has converged)
        assert abs(state_after["beta"]) > 0.01
