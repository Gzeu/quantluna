"""
QuantLuna — WalkForwardValidator v2
Anchored + rolling walk-forward with purged embargo.
Monte Carlo bootstrap from real trade P&L distribution.
Overfit detection: OOS Sharpe < 0.5 x IS Sharpe -> flag.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import QuantLunaConfig
from backtest.engine import BacktestEngine
from backtest.analytics import PerformanceAnalytics


class WalkForwardValidator:
    """
    Walk-forward validation for pairs trading.

    Parameters
    ----------
    train_periods : bars for in-sample window
    test_periods  : bars for out-of-sample window
    anchored      : True = expanding window, False = rolling window
    embargo_bars  : bars purged at fold boundary (>= half-life recommended)
    cfg           : QuantLunaConfig
    """

    def __init__(
        self,
        train_periods: int = 720,
        test_periods: int = 168,
        anchored: bool = False,
        embargo_bars: int = 0,
        cfg: Optional[QuantLunaConfig] = None,
    ) -> None:
        self.train_periods = train_periods
        self.test_periods = test_periods
        self.anchored = anchored
        self.embargo_bars = embargo_bars
        self.cfg = cfg or QuantLunaConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        y: pd.Series,
        x: pd.Series,
        funding_rate: Optional[pd.Series] = None,
        freq_hours: float = 1.0,
    ) -> Dict:
        """
        Execute walk-forward validation.

        Returns
        -------
        dict with keys:
          combined      : aggregate OOS metrics dict
          per_fold      : list of per-fold metric dicts
          oos_trades    : concatenated OOS trade list
          overfit_flag  : True if OOS Sharpe < 0.5 x median IS Sharpe
        """
        folds = self._build_folds(len(y))
        if not folds:
            raise ValueError(
                f"Not enough bars ({len(y)}) for even one fold "
                f"(train={self.train_periods} + embargo={self.embargo_bars} + test={self.test_periods})"
            )

        logger.info(
            f"Walk-forward: {len(folds)} folds | "
            f"{'anchored' if self.anchored else 'rolling'} | "
            f"embargo={self.embargo_bars} bars"
        )

        all_oos_trades: list = []
        all_oos_equity: list = []
        fold_metrics: list = []
        is_sharpes: list = []

        for fold_idx, (train_idx, test_idx) in enumerate(folds):
            y_train, x_train = y.iloc[train_idx], x.iloc[train_idx]
            y_test, x_test = y.iloc[test_idx], x.iloc[test_idx]
            fr_test = funding_rate.iloc[test_idx] if funding_rate is not None else None

            engine = BacktestEngine(cfg=self.cfg)
            is_result = engine.run(y_train, x_train, freq_hours=freq_hours)
            is_sharpe = is_result["metrics"].get("sharpe", 0.0)
            is_sharpes.append(is_sharpe)

            oos_result = engine.run(y_test, x_test, funding_rate=fr_test, freq_hours=freq_hours)
            oos_metrics = oos_result["metrics"]
            fold_metrics.append(oos_metrics)
            all_oos_trades.extend(oos_result["trades"])
            all_oos_equity.extend(oos_result["equity"])

            logger.info(
                f"  Fold {fold_idx+1}/{len(folds)} | "
                f"IS Sharpe={is_sharpe:.2f} | "
                f"OOS Sharpe={oos_metrics.get('sharpe', 0):.2f} | "
                f"OOS DD={oos_metrics.get('max_drawdown', 0):.1%} | "
                f"Trades={oos_metrics.get('n_trades', 0)}"
            )

        oos_equity_s = pd.Series(all_oos_equity) if all_oos_equity else pd.Series([self.cfg.risk.capital])
        combined = PerformanceAnalytics.compute(oos_equity_s, all_oos_trades, freq_hours=freq_hours)
        combined["n_folds"] = len(folds)
        combined["fold_sharpes"] = [m.get("sharpe", 0.0) for m in fold_metrics]
        combined["sharpe_stability"] = float(np.std(combined["fold_sharpes"]))

        median_is = float(np.median(is_sharpes)) if is_sharpes else 0.0
        oos_sharpe = combined.get("sharpe", 0.0)
        overfit = bool(median_is > 0.2 and oos_sharpe < 0.5 * median_is)
        if overfit:
            logger.warning(
                f"OVERFIT FLAG: median IS Sharpe={median_is:.2f} vs OOS={oos_sharpe:.2f} "
                f"(OOS < 50% of IS)"
            )

        return {
            "combined": combined,
            "per_fold": fold_metrics,
            "oos_trades": all_oos_trades,
            "overfit_flag": overfit,
            "median_is_sharpe": median_is,
        }

    def monte_carlo(
        self,
        trades: list,
        n_simulations: int = 1000,
        capital: float = 10_000.0,
        confidence_levels: Tuple[float, ...] = (0.05, 0.25, 0.75, 0.95),
        seed: Optional[int] = None,
    ) -> Dict:
        """
        Bootstrap Monte Carlo from real trade P&L distribution.

        Sampling is WITH replacement from the empirical P&L vector.
        No parametric distribution assumption.

        Returns
        -------
        dict with percentile equity outcomes, max drawdown distribution,
        probability of profit, and expected Sharpe distribution.
        """
        pnls = [t.pnl_net for t in trades if hasattr(t, "pnl_net")]
        if len(pnls) < 5:
            logger.warning("Monte Carlo: fewer than 5 trades -- results unreliable")
            return {"error": "insufficient_trades", "n_trades": len(pnls)}

        rng = np.random.default_rng(seed)
        pnl_arr = np.asarray(pnls, dtype=float)
        n = len(pnl_arr)

        sim_finals = np.empty(n_simulations)
        sim_max_dds = np.empty(n_simulations)
        sim_sharpes = np.empty(n_simulations)

        for i in range(n_simulations):
            sample = rng.choice(pnl_arr, size=n, replace=True)
            equity = np.empty(n + 1)
            equity[0] = capital
            equity[1:] = capital + np.cumsum(sample)
            peak = np.maximum.accumulate(equity)
            dd = (equity - peak) / np.where(peak > 0, peak, 1e-12)
            sim_finals[i] = equity[-1]
            sim_max_dds[i] = dd.min()
            sigma = sample.std()
            sim_sharpes[i] = (sample.mean() / sigma * np.sqrt(n)) if sigma > 0 else 0.0

        result: Dict = {
            "n_simulations": n_simulations,
            "n_trades": n,
            "prob_profit": float((sim_finals > capital).mean()),
            "median_final_equity": float(np.median(sim_finals)),
            "median_max_dd": float(np.median(sim_max_dds)),
            "p95_max_dd": float(np.percentile(sim_max_dds, 95)),
            "median_sharpe": float(np.median(sim_sharpes)),
        }
        for cl in confidence_levels:
            pct = int(cl * 100)
            result[f"p{pct:02d}_final_equity"] = float(np.percentile(sim_finals, pct))

        logger.info(
            f"MC({n_simulations}): P(profit)={result['prob_profit']:.1%} | "
            f"Median equity=${result['median_final_equity']:,.0f} | "
            f"p95 DD={result['p95_max_dd']:.1%}"
        )
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_folds(self, n: int) -> List[Tuple[range, range]]:
        folds: List[Tuple[range, range]] = []
        start = 0
        total_needed = self.train_periods + self.embargo_bars + self.test_periods
        while start + total_needed <= n:
            train_end = start + self.train_periods
            test_start = train_end + self.embargo_bars
            test_end = test_start + self.test_periods
            train_idx = range(0 if self.anchored else start, train_end)
            test_idx = range(test_start, test_end)
            folds.append((train_idx, test_idx))
            start += self.test_periods
        return folds
