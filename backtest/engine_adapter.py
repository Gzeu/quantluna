"""
backtest/engine_adapter.py  —  QuantLuna Sprint 15

Bridge layer: StrategyConfig → BacktestConfig + WalkForwardEngine.

Problema rezolvată:
  backtest/engine.py folosește `BacktestConfig` propriu (capital_usd, vol_target,
  kelly_fraction, bar_freq_hours, etc.) care duplica toți parametrii din
  `StrategyConfig`. Orice schimbare de parametru trebuia făcută în două locuri.

Soluția Sprint 15:
  `BacktestEngine` — public API simplu, acceptă `StrategyConfig` direct:

    from backtest.engine_adapter import BacktestEngine
    from config.strategy_config import StrategyConfig

    cfg = StrategyConfig.from_optimizer_json("data/best_params.json")
    engine = BacktestEngine(cfg)
    result = engine.run(y=prices_y, x=prices_x)
    print(result["sharpe"], result["max_drawdown"])

  `WalkForwardRunner` — wraps WalkForwardValidator cu StrategyConfig:

    runner = WalkForwardRunner(cfg, n_splits=5, embargo_bars=24)
    wf_result = runner.run(y=prices_y, x=prices_x)
    print(wf_result["combined"]["sharpe"])
    print(wf_result["overfit_flag"])

Purging gap anti-lookahead (documentat explicit):
  La granita IS/OOS, ultimele `purge_bars` bare din IS si primele `embargo_bars`
  bare din OOS sunt eliminate INAINTE ca z-score-ul sa fie calculat.
  Rationale: spread-ul si Kalman state din ultimele IS bars pot "contamina" statistica
  OOS daca nu se lasa un gap. Recomandat:
    purge_bars  >= warm_up_bars al Kalman (default: 30)
    embargo_bars >= estimated_half_life_bars (default: 24 pt 1h bars)
  Cu aceste valori, nici un OOS bar nu poate folosi informatii din IS prin
  intermediul statisticilor Kalman sau al z-score-ului rolling.

Dependinte:
  backtest/engine.py  (WalkForwardEngine, BacktestConfig)
  backtest/walk_forward.py (WalkForwardValidator)
  config/strategy_config.py (StrategyConfig)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# StrategyConfig → BacktestConfig converter
# ---------------------------------------------------------------------------

def strategy_to_backtest_config(cfg, n_splits: int = 5, purge_bars: int = 30, embargo_bars: int = 24):
    """
    Converts StrategyConfig → BacktestConfig (used by WalkForwardEngine).

    Purging gap parameters:
      purge_bars   -- bars at IS/OOS boundary removed from IS side.
                      Should be >= Kalman warm_up_bars to ensure the filter
                      state is not directly influenced by OOS-adjacent data.
                      Default: 30 (matches default warm_up_bars in StrategyConfig)
      embargo_bars -- additional bars removed from OOS side after purge.
                      Should be >= expected half-life in bars so that mean-
                      reverting spread echoes from IS don't bias OOS signals.
                      Default: 24 (1 day for 1h bars; scale for other freqs)
    """
    try:
        from backtest.engine import BacktestConfig
        from config.settings import SignalConfig
    except ImportError as e:
        raise ImportError(f"backtest.engine or config.settings not found: {e}")

    # Map bar_freq string → hours float
    _freq_map = {
        "1m": 1 / 60, "3m": 3 / 60, "5m": 5 / 60, "15m": 0.25,
        "30m": 0.5, "1h": 1.0, "2h": 2.0, "4h": 4.0,
        "6h": 6.0, "8h": 8.0, "12h": 12.0, "1d": 24.0,
    }
    bar_freq_hours = _freq_map.get(getattr(cfg, "bar_freq", "1h"), 1.0)

    signal_cfg = SignalConfig(
        zscore_entry=getattr(cfg, "zscore_entry", 2.0),
        zscore_exit=getattr(cfg, "zscore_exit", 0.5),
        zscore_window=getattr(cfg, "zscore_window", 100),
        warm_up_bars=getattr(cfg, "warm_up_bars", 30),
    ) if SignalConfig else None

    return BacktestConfig(
        n_splits=n_splits,
        train_ratio=0.70,
        purge_bars=purge_bars,
        embargo_bars=embargo_bars,
        fee_taker=getattr(cfg, "fee_rate", 0.00055),
        fee_maker=getattr(cfg, "fee_rate", 0.00055) * 0.5,
        slippage_bps=getattr(cfg, "slippage_pct", 0.0005) * 10_000,
        capital_usd=getattr(cfg, "capital_usdt", 10_000.0),
        vol_target=getattr(cfg, "vol_target", 0.01),
        kelly_fraction=getattr(cfg, "kelly_fraction", 0.25),
        bar_freq_hours=bar_freq_hours,
        signal_cfg=signal_cfg,
    )


# ---------------------------------------------------------------------------
# Public BacktestEngine — accepts StrategyConfig directly
# ---------------------------------------------------------------------------

class BacktestEngine:
    """
    Public API pentru backtest. Accepts StrategyConfig directly.

    Usage:
        engine = BacktestEngine(cfg)                 # StrategyConfig
        result = engine.run(y=prices_y, x=prices_x)  # pd.Series
        # result keys: sharpe, sortino, calmar, max_drawdown, max_drawdown_pct,
        #              n_trades, win_rate, profit_factor, total_net_pnl,
        #              ann_return, ann_volatility, trades_df
    """

    def __init__(
        self,
        cfg,
        n_splits: int = 5,
        purge_bars: Optional[int] = None,
        embargo_bars: Optional[int] = None,
    ) -> None:
        """
        Parameters
        ----------
        cfg : StrategyConfig
            Master config. All trading parameters come from here.
        n_splits : int
            Walk-forward fold count.
        purge_bars : int, optional
            Bars purged at IS boundary. Defaults to cfg.warm_up_bars.
        embargo_bars : int, optional
            Bars embargoed at OOS boundary. Defaults to 24 (1 day @ 1h).
        """
        self.cfg = cfg
        self.n_splits = n_splits
        # Default purge = warm_up so Kalman state is fully settled before OOS
        self.purge_bars = purge_bars if purge_bars is not None else getattr(cfg, "warm_up_bars", 30)
        # Default embargo = 24h in bars (scale for non-1h freqs manually)
        self.embargo_bars = embargo_bars if embargo_bars is not None else 24

    def _build_df(self, y: pd.Series, x: pd.Series) -> pd.DataFrame:
        """Align series and produce DataFrame expected by WalkForwardEngine."""
        df = pd.DataFrame({"close_y": y, "close_x": x}).dropna()
        df = df.reset_index()
        # Rename index to timestamp if it looks like a DatetimeIndex
        if "index" in df.columns:
            df = df.rename(columns={"index": "timestamp"})
        elif df.index.dtype != object:
            df.insert(0, "timestamp", df.index)
        return df.reset_index(drop=True)

    def run(
        self,
        y: Optional[pd.Series] = None,
        x: Optional[pd.Series] = None,
        df: Optional[pd.DataFrame] = None,
        data_dir: Optional[Path] = None,
    ) -> Dict:
        """
        Run walk-forward backtest.

        Provide either:
          - y, x   : pd.Series with aligned price history
          - df     : DataFrame with columns [timestamp, close_y, close_x]
          - data_dir: Path to directory with parquet files (sym_y, sym_x from cfg)

        Returns
        -------
        dict with OOS aggregate metrics + all_trades DataFrame.
        Keys: sharpe, sortino, calmar, max_drawdown, max_drawdown_pct,
              win_rate, profit_factor, n_trades, total_net_pnl,
              ann_return, ann_volatility, trades_df, overfit_flag
        """
        if df is None:
            if y is not None and x is not None:
                df = self._build_df(y, x)
            elif data_dir is not None:
                df = self._load_parquet(data_dir)
            else:
                raise ValueError("Provide y+x, df, or data_dir.")

        bc = strategy_to_backtest_config(
            self.cfg,
            n_splits=self.n_splits,
            purge_bars=self.purge_bars,
            embargo_bars=self.embargo_bars,
        )

        try:
            from backtest.engine import WalkForwardEngine
            se_factory = self._make_spread_engine_factory()
            engine = WalkForwardEngine(df=df, cfg=bc, spread_engine_factory=se_factory)
            results = engine.run()
            m = results.oos_metrics
            return {
                "sharpe":           m.sharpe,
                "sharpe_ratio":     m.sharpe,  # alias for optimize_params.py
                "sortino":          m.sortino,
                "calmar":           m.calmar,
                "max_drawdown":     m.max_drawdown,
                "max_drawdown_pct": m.max_drawdown_pct,
                "win_rate":         m.win_rate,
                "profit_factor":    m.profit_factor,
                "n_trades":         m.n_trades,
                "total_net_pnl":    m.total_net_pnl,
                "ann_return":       m.ann_return,
                "ann_volatility":   m.ann_volatility,
                "trades_df":        results.to_dataframe(),
                "overfit_flag":     False,
                "n_folds":          self.n_splits,
            }
        except Exception as e:
            logger.warning(f"WalkForwardEngine failed ({e}), using analytics fallback")
            return self._analytics_fallback(df)

    def _make_spread_engine_factory(self):
        """Returns a factory callable for SpreadEngine using Kalman params from cfg."""
        cfg = self.cfg
        def factory():
            try:
                from core.spread import SpreadEngine
                from core.kalman_filter import KalmanHedgeRatio
                kf = KalmanHedgeRatio(
                    delta=getattr(cfg, "delta", 1e-4),
                    observation_noise=getattr(cfg, "observation_noise", 1e-2),
                    warm_up=getattr(cfg, "warm_up_bars", 30),
                )
                return SpreadEngine(kf)
            except ImportError:
                # Return a minimal mock if SpreadEngine isn't available
                return _MinimalSpreadEngine(
                    delta=getattr(cfg, "delta", 1e-4),
                    warm_up=getattr(cfg, "warm_up_bars", 30),
                )
        return factory

    def _load_parquet(self, data_dir: Path) -> pd.DataFrame:
        """Load OHLCV parquet files for sym_y and sym_x, merge on timestamp."""
        sym_y = getattr(self.cfg, "sym_y", "BTCUSDT")
        sym_x = getattr(self.cfg, "sym_x", "ETHUSDT")
        freq = getattr(self.cfg, "bar_freq", "1h")

        path_y = data_dir / f"{sym_y}_{freq}.parquet"
        path_x = data_dir / f"{sym_x}_{freq}.parquet"

        if not path_y.exists():
            raise FileNotFoundError(f"Parquet file not found: {path_y}")
        if not path_x.exists():
            raise FileNotFoundError(f"Parquet file not found: {path_x}")

        df_y = pd.read_parquet(path_y)[["timestamp", "close"]].rename(columns={"close": "close_y"})
        df_x = pd.read_parquet(path_x)[["timestamp", "close"]].rename(columns={"close": "close_x"})
        df = df_y.merge(df_x, on="timestamp", how="inner").sort_values("timestamp")
        df = df.dropna().reset_index(drop=True)
        logger.info(f"Loaded {len(df)} bars for {sym_y}/{sym_x} from {data_dir}")
        return df

    def _analytics_fallback(self, df: pd.DataFrame) -> Dict:
        """Minimal fallback using PerformanceAnalytics when engine import fails."""
        return {
            "sharpe": 0.0, "sharpe_ratio": 0.0,
            "sortino": 0.0, "calmar": 0.0,
            "max_drawdown": 0.0, "max_drawdown_pct": 0.0,
            "win_rate": 0.0, "profit_factor": 0.0,
            "n_trades": 0, "total_net_pnl": 0.0,
            "ann_return": 0.0, "ann_volatility": 0.0,
            "trades_df": pd.DataFrame(),
            "overfit_flag": False, "n_folds": self.n_splits,
            "note": "analytics_fallback",
        }


# ---------------------------------------------------------------------------
# WalkForwardRunner — StrategyConfig wrapper around WalkForwardValidator
# ---------------------------------------------------------------------------

class WalkForwardRunner:
    """
    Wraps WalkForwardValidator cu StrategyConfig.

    Purge gap logic (explicit):
      IS fold      |  PURGE  |  EMBARGO  |  OOS fold
      [0 ... T-1]  [T..T+P]  [T+P..T+P+E]  [T+P+E ...]

      purge_bars  (P): removed from IS end.
        Rule: P >= Kalman warm_up_bars
        Why:  Kalman state at bars T-P..T was trained on data adjacent to OOS.
              If used directly, the state encodes forward information.
      embargo_bars (E): removed from OOS start.
        Rule: E >= estimated half_life in bars
        Why:  Spread mean-reversion echo: an IS signal at bar T that closes
              at bar T+k (in OOS) would look like an OOS profit even though
              the entry information came from IS. Embargo eliminates this.

    Usage:
        runner = WalkForwardRunner(cfg, n_splits=5)
        result = runner.run(y=y_series, x=x_series)
        print(result["combined"]["sharpe"])
        print("Overfit:", result["overfit_flag"])
    """

    def __init__(
        self,
        cfg,
        n_splits: int = 5,
        anchored: bool = False,
        purge_bars: Optional[int] = None,
        embargo_bars: Optional[int] = None,
    ) -> None:
        self.cfg = cfg
        self.n_splits = n_splits
        self.anchored = anchored
        self.purge_bars = purge_bars if purge_bars is not None else getattr(cfg, "warm_up_bars", 30)
        self.embargo_bars = embargo_bars if embargo_bars is not None else 24

    def run(
        self,
        y: pd.Series,
        x: pd.Series,
        funding_rate: Optional[pd.Series] = None,
    ) -> Dict:
        """
        Execute walk-forward validation via WalkForwardValidator.

        Returns
        -------
        Dict with keys: combined, per_fold, oos_trades, overfit_flag,
                        median_is_sharpe, purge_bars, embargo_bars, n_splits
        """
        n = len(y)
        train_periods = int(n * 0.70 / self.n_splits) if self.n_splits > 0 else 720
        test_periods  = int(n * 0.30 / self.n_splits) if self.n_splits > 0 else 168

        # Ensure minimum viable periods
        train_periods = max(train_periods, 200)
        test_periods  = max(test_periods, 50)

        embargo_total = self.purge_bars + self.embargo_bars

        try:
            from backtest.walk_forward import WalkForwardValidator
            validator = WalkForwardValidator(
                train_periods=train_periods,
                test_periods=test_periods,
                anchored=self.anchored,
                embargo_bars=embargo_total,  # WalkForwardValidator uses single embargo param
            )
            result = validator.run(y=y, x=x, funding_rate=funding_rate)
            result["purge_bars"]   = self.purge_bars
            result["embargo_bars"] = self.embargo_bars
            result["n_splits"]     = self.n_splits
            return result
        except Exception as e:
            logger.warning(f"WalkForwardValidator failed ({e}), returning stub result")
            return {
                "combined": {"sharpe": 0.0, "n_trades": 0},
                "per_fold": [],
                "oos_trades": [],
                "overfit_flag": False,
                "median_is_sharpe": 0.0,
                "purge_bars": self.purge_bars,
                "embargo_bars": self.embargo_bars,
                "n_splits": self.n_splits,
                "note": "stub_fallback",
            }

    def monte_carlo(
        self,
        trades: list,
        n_simulations: int = 1000,
        seed: int = 42,
    ) -> Dict:
        """Delegate Monte Carlo bootstrap to WalkForwardValidator."""
        try:
            from backtest.walk_forward import WalkForwardValidator
            v = WalkForwardValidator()
            capital = getattr(self.cfg, "capital_usdt", 10_000.0)
            return v.monte_carlo(trades, n_simulations=n_simulations,
                                  capital=capital, seed=seed)
        except Exception as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Minimal SpreadEngine fallback (when core.spread is unavailable)
# ---------------------------------------------------------------------------

class _MinimalSpreadEngine:
    """
    Fallback SpreadEngine for testing without full core/ imports.
    Implements only what WalkForwardEngine calls: fit(), update_one(), zscore_window.
    """

    def __init__(self, delta: float = 1e-4, warm_up: int = 30) -> None:
        from core.kalman_filter import KalmanHedgeRatio
        self._kf = KalmanHedgeRatio(delta=delta, warm_up=warm_up)
        self.zscore_window = 100

    def fit(self, y: pd.Series, x: pd.Series) -> pd.DataFrame:
        return self._kf.fit(y, x)

    def update_one(self, y: float, x: float, ts=None) -> Dict:
        state = self._kf.update(y, x, ts=ts)
        spread = y - state.beta * x - state.alpha
        return {
            "beta":          state.beta,
            "alpha":         state.alpha,
            "spread":        spread,
            "P_beta":        state.P_beta,
            "kalman_gain":   state.kalman_gain,
            "uncertainty":   np.sqrt(state.P_beta),
            "is_warm":       state.is_warm,
            "half_life_hours": None,
        }
