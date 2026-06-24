"""
tests/test_backtest.py

Integration tests pentru WalkForwardEngine (backtest/engine.py).

Acopera:
  - smoke run complet
  - metrici OOS prezente si tipuri corecte
  - equity curve sanity
  - non-leakage assertion: adaugarea de bare OOS nu schimba IS anchor stats
  - bar_freq_hours: funding cost diferit pe timeframes diferite
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from backtest.engine import BacktestConfig, WalkForwardEngine, BacktestResults
from core.kalman_filter import KalmanHedgeRatio
from core.spread import SpreadEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(n: int = 1000, beta: float = 1.3, noise: float = 0.03, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x = pd.Series(
        np.cumsum(rng.normal(0, 0.005, n)) + 10.0,
        index=pd.date_range("2024-01-01", periods=n, freq="1h"),
    )
    y = beta * x + rng.normal(0, noise, n)
    y.index = x.index
    return pd.DataFrame({
        "timestamp": x.index,
        "close_y": y.values,
        "close_x": x.values,
    })


def _factory():
    return SpreadEngine(
        kalman=KalmanHedgeRatio(delta=1e-4, warm_up=30),
        zscore_window=60,
        min_warm_periods=30,
    )


def _run_engine(n: int = 1000, bar_freq_hours: float = 1.0, seed: int = 7) -> BacktestResults:
    df = _make_df(n=n, seed=seed)
    cfg = BacktestConfig(
        n_splits=3,
        train_ratio=0.7,
        purge_bars=5,
        embargo_bars=3,
        capital_usd=10_000.0,
        bar_freq_hours=bar_freq_hours,
    )
    engine = WalkForwardEngine(df=df, cfg=cfg, spread_engine_factory=_factory)
    return engine.run()


# ---------------------------------------------------------------------------
# Smoke — engine ruleaza fara exceptii
# ---------------------------------------------------------------------------

class TestBacktestSmoke:
    def test_runs_without_error(self):
        results = _run_engine()
        assert results is not None

    def test_returns_backtest_results_type(self):
        results = _run_engine()
        assert isinstance(results, BacktestResults)

    def test_oos_metrics_present(self):
        results = _run_engine()
        assert results.oos_metrics is not None

    def test_per_fold_metrics_count(self):
        # 3 splits => 6 PerformanceMetrics (IS + OOS per fold)
        results = _run_engine()
        assert len(results.per_fold_metrics) == 6


# ---------------------------------------------------------------------------
# OOS Metrics — tipuri si range-uri rezonabile
# ---------------------------------------------------------------------------

class TestOOSMetrics:
    def test_metric_types(self):
        results = _run_engine()
        m = results.oos_metrics
        assert isinstance(m.sharpe, float)
        assert isinstance(m.sortino, float)
        assert isinstance(m.calmar, float)
        assert isinstance(m.n_trades, int)
        assert isinstance(m.win_rate, float)
        assert isinstance(m.total_net_pnl, float)

    def test_win_rate_in_range(self):
        results = _run_engine()
        assert 0.0 <= results.oos_metrics.win_rate <= 1.0

    def test_max_drawdown_nonpositive(self):
        results = _run_engine()
        assert results.oos_metrics.max_drawdown <= 0.0

    def test_max_drawdown_pct_nonpositive(self):
        results = _run_engine()
        assert results.oos_metrics.max_drawdown_pct <= 0.0

    def test_n_trades_nonnegative(self):
        results = _run_engine()
        assert results.oos_metrics.n_trades >= 0


# ---------------------------------------------------------------------------
# Non-Leakage Assertion (FIX-BT-1)
# Adaugarea de bare OOS suplimentare NU trebuie sa schimbe IS anchor stats.
# ---------------------------------------------------------------------------

class TestOOSNonLeakage:
    """IS anchor (mean, std) trebuie sa fie invariant fata de lungimea OOS."""

    def _get_is_anchor(self, df: pd.DataFrame):
        """Extrage anchor_mean/std din IS tail fold-0, identic cu ce face engine-ul."""
        cfg = BacktestConfig(n_splits=3, train_ratio=0.7, purge_bars=5, embargo_bars=3)
        engine = WalkForwardEngine(df=df, cfg=cfg, spread_engine_factory=_factory)
        splits = engine._build_splits()
        is_idx, _ = splits[0]
        se = _factory()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            is_df = se.fit(
                df.iloc[is_idx]["close_y"].reset_index(drop=True),
                df.iloc[is_idx]["close_x"].reset_index(drop=True),
            )
        tail = is_df["spread"].iloc[-se.zscore_window:].dropna()
        return float(tail.mean()), float(tail.std())

    def test_anchor_invariant_to_oos_length(self):
        """
        Luam acelasi seed dar truniem OOS-ul la doua lungimi diferite.
        IS anchor trebuie sa fie identic — OOS nu influenteaza IS stats.
        """
        df_base = _make_df(n=1000, seed=99)
        # Scurtam OOS-ul pastrand aceleasi IS bare
        df_short = df_base.iloc[:800].copy().reset_index(drop=True)
        df_long  = df_base.copy()

        # Recalculeaza timestamp pentru ca reset_index strica DatetimeIndex
        df_short["timestamp"] = pd.date_range("2024-01-01", periods=len(df_short), freq="1h")
        df_long["timestamp"]  = pd.date_range("2024-01-01", periods=len(df_long),  freq="1h")

        mean_short, std_short = self._get_is_anchor(df_short)
        mean_long,  std_long  = self._get_is_anchor(df_long)

        assert abs(mean_short - mean_long) < 1e-8, (
            f"IS anchor mean changed when OOS extended: {mean_short:.8f} vs {mean_long:.8f}"
        )
        assert abs(std_short - std_long) < 1e-8, (
            f"IS anchor std changed when OOS extended: {std_short:.8f} vs {std_long:.8f}"
        )

    def test_is_anchor_std_nonzero(self):
        """IS tail std > 0 => spread nu e degenerat pe date sintetice."""
        df = _make_df(n=1000, seed=7)
        _, std = self._get_is_anchor(df)
        assert std > 1e-10, "IS anchor std is zero — spread degenerat"


# ---------------------------------------------------------------------------
# bar_freq_hours  (FIX-BT-2)
# ---------------------------------------------------------------------------

class TestBarFreqHours:
    def test_bars_per_day_property_1h(self):
        assert BacktestConfig(bar_freq_hours=1.0).bars_per_day == pytest.approx(24.0)

    def test_bars_per_day_property_4h(self):
        assert BacktestConfig(bar_freq_hours=4.0).bars_per_day == pytest.approx(6.0)

    def test_bars_per_day_property_15m(self):
        assert BacktestConfig(bar_freq_hours=0.25).bars_per_day == pytest.approx(96.0)

    def test_funding_cost_proportional_to_bar_freq(self):
        """
        Acelasi trade (24 bare held, notional 1000) trebuie sa produca
        funding_cost de 4x mai mare pe 4h bars decat pe 1h bars.
        """
        def fund(cfg: BacktestConfig, bars_held: int = 24, notional: float = 1000.0) -> float:
            holding_days = bars_held / cfg.bars_per_day
            return notional * cfg.funding_rate_annual * holding_days / 365

        cfg_1h = BacktestConfig(bar_freq_hours=1.0)
        cfg_4h = BacktestConfig(bar_freq_hours=4.0)
        assert fund(cfg_4h) == pytest.approx(fund(cfg_1h) * 4.0, rel=1e-6)

    def test_engine_accepts_4h_config(self):
        df = _make_df(n=1000, seed=7)
        cfg = BacktestConfig(n_splits=3, bar_freq_hours=4.0)
        engine = WalkForwardEngine(df=df, cfg=cfg, spread_engine_factory=_factory)
        results = engine.run()
        assert results is not None

    def test_invalid_bar_freq_raises(self):
        df = _make_df(n=1000)
        with pytest.raises(ValueError, match="bar_freq_hours"):
            WalkForwardEngine(
                df=df,
                cfg=BacktestConfig(bar_freq_hours=0.0),
                spread_engine_factory=_factory,
            )
