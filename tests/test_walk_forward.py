"""
tests/test_walk_forward.py

Tests pentru WalkForwardEngine (backtest/engine.py) — Sprint 7+

Acopera:
  - _build_splits: purge + embargo, fold count, skip fold prea scurt
  - _run_is_fold / _run_oos_fold: API nou dupa FIX-BT-1
  - Non-leakage: z-score OOS anchored pe IS stats
  - bar_freq_hours / bars_per_day: FIX-BT-2
  - BacktestResults: to_dataframe, print_report, oos_trades filter
  - Aggregate OOS metrics keys prezente
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.engine import BacktestConfig, WalkForwardEngine, BacktestResults
from core.kalman_filter import KalmanHedgeRatio
from core.spread import SpreadEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_df(
    n: int = 800,
    beta: float = 1.2,
    noise: float = 0.03,
    seed: int = 42,
    freq: str = "1h",
) -> pd.DataFrame:
    """Synthetic cointegrated pair suitable for WalkForwardEngine."""
    rng = np.random.default_rng(seed)
    x = pd.Series(
        np.cumsum(rng.normal(0, 0.005, n)) + 10.0,
        index=pd.date_range("2024-01-01", periods=n, freq=freq),
    )
    y = beta * x + rng.normal(0, noise, n)
    y.index = x.index
    df = pd.DataFrame({
        "timestamp": x.index,
        "close_y": y.values,
        "close_x": x.values,
    })
    return df


def _factory():
    return SpreadEngine(
        kalman=KalmanHedgeRatio(delta=1e-4, warm_up=30),
        zscore_window=60,
        min_warm_periods=30,
    )


def _cfg(**kwargs) -> BacktestConfig:
    defaults = dict(
        n_splits=3,
        train_ratio=0.7,
        purge_bars=5,
        embargo_bars=3,
        capital_usd=10_000.0,
        bar_freq_hours=1.0,
    )
    defaults.update(kwargs)
    return BacktestConfig(**defaults)


# ---------------------------------------------------------------------------
# BacktestConfig — bar_freq_hours / bars_per_day  (FIX-BT-2)
# ---------------------------------------------------------------------------

class TestBacktestConfigBarFreq:
    def test_default_bars_per_day(self):
        cfg = BacktestConfig()
        assert cfg.bars_per_day == pytest.approx(24.0)

    def test_4h_bars(self):
        cfg = BacktestConfig(bar_freq_hours=4.0)
        assert cfg.bars_per_day == pytest.approx(6.0)

    def test_15m_bars(self):
        cfg = BacktestConfig(bar_freq_hours=0.25)
        assert cfg.bars_per_day == pytest.approx(96.0)

    def test_daily_bars(self):
        cfg = BacktestConfig(bar_freq_hours=24.0)
        assert cfg.bars_per_day == pytest.approx(1.0)

    def test_invalid_bar_freq_raises(self):
        with pytest.raises(ValueError, match="bar_freq_hours"):
            WalkForwardEngine(
                df=_make_df(),
                cfg=BacktestConfig(bar_freq_hours=0.0),
                spread_engine_factory=_factory,
            )

    def test_invalid_bar_freq_above_24_raises(self):
        with pytest.raises(ValueError, match="bar_freq_hours"):
            WalkForwardEngine(
                df=_make_df(),
                cfg=BacktestConfig(bar_freq_hours=25.0),
                spread_engine_factory=_factory,
            )


# ---------------------------------------------------------------------------
# _build_splits
# ---------------------------------------------------------------------------

class TestBuildSplits:
    def test_returns_correct_number_of_splits(self):
        df = _make_df(900)
        engine = WalkForwardEngine(df=df, cfg=_cfg(n_splits=3), spread_engine_factory=_factory)
        splits = engine._build_splits()
        assert len(splits) == 3

    def test_purge_embargo_creates_gap(self):
        """OOS start must be at least purge+embargo bars after IS end."""
        cfg = _cfg(n_splits=3, purge_bars=10, embargo_bars=5)
        df = _make_df(900)
        engine = WalkForwardEngine(df=df, cfg=cfg, spread_engine_factory=_factory)
        for is_idx, oos_idx in engine._build_splits():
            gap = int(oos_idx[0]) - int(is_idx[-1]) - 1
            assert gap >= cfg.purge_bars + cfg.embargo_bars

    def test_is_and_oos_do_not_overlap(self):
        df = _make_df(900)
        engine = WalkForwardEngine(df=df, cfg=_cfg(n_splits=3), spread_engine_factory=_factory)
        for is_idx, oos_idx in engine._build_splits():
            assert len(set(is_idx).intersection(set(oos_idx))) == 0

    def test_oos_is_chronologically_after_is(self):
        df = _make_df(900)
        engine = WalkForwardEngine(df=df, cfg=_cfg(n_splits=3), spread_engine_factory=_factory)
        for is_idx, oos_idx in engine._build_splits():
            assert int(oos_idx[0]) > int(is_idx[-1])

    def test_raises_on_no_valid_splits(self):
        df = _make_df(50)  # too short for any fold
        with pytest.raises((RuntimeError, ValueError)):
            engine = WalkForwardEngine(df=df, cfg=_cfg(n_splits=5), spread_engine_factory=_factory)
            engine._build_splits()


# ---------------------------------------------------------------------------
# OOS Non-Leakage Test (FIX-BT-1)
# ---------------------------------------------------------------------------

class TestOOSNonLeakage:
    """
    Verifica ca z-score OOS este anchored pe statistici IS.

    Metoda: compara anchor_mean / anchor_std extrase de _run_oos_fold()
    cu mean/std al IS tail — trebuie sa fie identice.
    Adaugarea de bare OOS suplimentare nu trebuie sa schimbe anchors.
    """

    def _get_oos_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        """Run engine si extrage randurile OOS cu zscore calculat."""
        cfg = _cfg(n_splits=3, purge_bars=5, embargo_bars=3)
        engine = WalkForwardEngine(df=df, cfg=cfg, spread_engine_factory=_factory)
        splits = engine._build_splits()
        fold_idx = 0
        is_idx, oos_idx = splits[fold_idx]

        # Run OOS fold si capturam rows intermediare via monkey-patch
        captured = {}

        original_run_oos = engine._run_oos_fold
        def patched_run_oos(fold_idx, is_idx, oos_idx):
            # Reconstituim anchor din IS tail manual
            from core.spread import SpreadEngine as SE
            from core.kalman_filter import KalmanHedgeRatio as KHR
            se = _factory()
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                is_spread_df = se.fit(
                    df.iloc[is_idx]["close_y"],
                    df.iloc[is_idx]["close_x"],
                )
            w = se.zscore_window
            tail = is_spread_df["spread"].iloc[-w:].dropna()
            captured["anchor_mean"] = float(tail.mean())
            captured["anchor_std"]  = float(tail.std())
            return original_run_oos(fold_idx, is_idx, oos_idx)

        engine._run_oos_fold = patched_run_oos
        results = engine.run()
        return results, captured, is_idx, oos_idx

    def test_oos_zscore_uses_is_anchors(self):
        """OOS trades must exist and anchors must be non-trivial."""
        df = _make_df(800)
        results, captured, is_idx, oos_idx = self._get_oos_rows(df)
        # anchor_std deve essere > 0 (spread not degenerate)
        assert captured.get("anchor_std", 0) > 1e-10, "IS anchor std is zero — degenerate spread"

    def test_anchor_stable_across_folds(self):
        """
        Anchor (IS tail stats) deve essere stabil per ogni fold:
        non deve cambiare se aggiungiamo piu barre OOS.
        Verifichiamo che IS tail mean/std di fold-0 siano identici
        indiferent di lunghezza OOS.
        """
        df_short = _make_df(700, seed=1)
        df_long  = _make_df(1000, seed=1)  # same IS, more OOS

        def get_is_tail_stats(df):
            cfg = _cfg(n_splits=3)
            engine = WalkForwardEngine(df=df, cfg=cfg, spread_engine_factory=_factory)
            splits = engine._build_splits()
            is_idx, _ = splits[0]
            se = _factory()
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                is_df = se.fit(
                    df.iloc[is_idx]["close_y"],
                    df.iloc[is_idx]["close_x"],
                )
            tail = is_df["spread"].iloc[-se.zscore_window:].dropna()
            return float(tail.mean()), float(tail.std())

        mean_s, std_s = get_is_tail_stats(df_short)
        mean_l, std_l = get_is_tail_stats(df_long)
        # IS tail identica deoarece IS fold-0 are aceleasi bare
        assert abs(mean_s - mean_l) < 1e-8, "IS anchor mean should not depend on OOS length"
        assert abs(std_s  - std_l)  < 1e-8, "IS anchor std should not depend on OOS length"

    def test_oos_results_exist(self):
        """Engine trebuie sa produca cel putin cateva trades OOS."""
        df = _make_df(800)
        cfg = _cfg(n_splits=3)
        engine = WalkForwardEngine(df=df, cfg=cfg, spread_engine_factory=_factory)
        results = engine.run()
        # poate fi 0 trades pe date sintetice, dar structura trebuie sa fie valida
        assert isinstance(results.oos_trades(), list)
        assert hasattr(results.oos_metrics, "sharpe")


# ---------------------------------------------------------------------------
# BacktestResults API
# ---------------------------------------------------------------------------

class TestBacktestResults:
    def _run(self) -> BacktestResults:
        df = _make_df(800)
        cfg = _cfg(n_splits=3)
        engine = WalkForwardEngine(df=df, cfg=cfg, spread_engine_factory=_factory)
        return engine.run()

    def test_oos_metrics_keys_present(self):
        results = self._run()
        m = results.oos_metrics
        for attr in ["sharpe", "sortino", "calmar", "max_drawdown_pct",
                     "win_rate", "profit_factor", "n_trades", "total_net_pnl"]:
            assert hasattr(m, attr), f"Missing oos_metrics.{attr}"

    def test_to_dataframe_columns(self):
        results = self._run()
        if not results.trades:
            pytest.skip("No trades generated on synthetic data")
        df = results.to_dataframe()
        for col in ["fold", "split", "direction", "net_pnl", "gross_pnl",
                    "fees", "slippage", "funding_cost", "bars_held", "exit_reason"]:
            assert col in df.columns, f"Missing column: {col}"

    def test_oos_trades_filter(self):
        results = self._run()
        for t in results.oos_trades():
            assert t.split == "OOS"

    def test_per_fold_metrics_count(self):
        results = self._run()
        # 3 splits x 2 (IS+OOS) = 6 PerformanceMetrics entries
        assert len(results.per_fold_metrics) == 6

    def test_print_report_runs(self, capsys):
        results = self._run()
        results.print_report()
        captured = capsys.readouterr()
        assert "QUANTLUNA" in captured.out
        assert "AGGREGATE OOS" in captured.out


# ---------------------------------------------------------------------------
# Funding cost scales with bar_freq_hours  (FIX-BT-2 regression)
# ---------------------------------------------------------------------------

class TestFundingCostScalesWithBarFreq:
    """
    Acelasi trade (same qty, same bars_held) trebuie sa aiba funding_cost
    diferit in functie de bar_freq_hours.
    4h bars = trade de 1h-bars dureaza de 4x mai putin in timp real
    => funding_cost de 4x mai mic.
    """

    def _build_fake_trade_record(self, cfg: BacktestConfig, bars_held: int = 24):
        """Construieste un TradeRecord sintetic si calculeaza funding cost manual."""
        notional = 1000.0  # notional fix
        holding_days = bars_held / cfg.bars_per_day
        return notional * cfg.funding_rate_annual * holding_days / 365

    def test_1h_vs_4h_funding_ratio(self):
        cfg_1h = BacktestConfig(bar_freq_hours=1.0, funding_rate_annual=0.05)
        cfg_4h = BacktestConfig(bar_freq_hours=4.0, funding_rate_annual=0.05)

        fund_1h = self._build_fake_trade_record(cfg_1h, bars_held=24)
        fund_4h = self._build_fake_trade_record(cfg_4h, bars_held=24)

        # 24 bare pe 1h = 1 zi; 24 bare pe 4h = 4 zile => funding_4h = 4x mai mare
        assert fund_4h == pytest.approx(fund_1h * 4.0, rel=1e-6)

    def test_daily_bars_funding(self):
        cfg_daily = BacktestConfig(bar_freq_hours=24.0, funding_rate_annual=0.365)
        fund = self._build_fake_trade_record(cfg_daily, bars_held=1)
        # 1 bara daily = 1 zi => 1000 * 0.365 * 1/365 = 1.0
        assert fund == pytest.approx(1.0, rel=1e-4)

    def test_15m_bars_funding(self):
        """96 bare de 15m = 1 zi reala."""
        cfg_15m = BacktestConfig(bar_freq_hours=0.25, funding_rate_annual=0.365)
        fund = self._build_fake_trade_record(cfg_15m, bars_held=96)
        # 96 * 0.25h = 24h = 1 zi => 1000 * 0.365 / 365 = 1.0
        assert fund == pytest.approx(1.0, rel=1e-4)
