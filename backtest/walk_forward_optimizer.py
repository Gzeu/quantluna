"""
QuantLuna — WalkForwardOptimizer
Sprint 25

Rolling-window walk-forward optimization:
  1. Splits price series into overlapping train/test folds
  2. For each fold: grid-searches param combinations on train window
  3. Best config (by Sharpe) evaluated on test window
  4. Aggregates per-regime best params across all folds
  5. Returns OptimizeResult with:
     - per_fold_results: list of FoldResult
     - best_params_global: highest avg Sharpe config
     - best_params_by_regime: {"ranging": cfg, "trending": cfg, ...}
     - result_df: full DataFrame of all fold-param combos

Parallelism: ProcessPoolExecutor (n_jobs=-1 = all CPUs)

Usage:
    from backtest.walk_forward_optimizer import WalkForwardOptimizer, OptimizeConfig
    opt = WalkForwardOptimizer()
    result = opt.run(
        y=prices_y, x=prices_x,
        param_grid={
            "zscore_window": [10, 20, 30],
            "zscore_entry":  [1.5, 2.0, 2.5],
            "regime_min_persistence": [2, 3, 5],
        },
    )
    print(result.best_params_global)
    print(result.best_params_by_regime)
    result.to_json("optimize_result.json")
"""
from __future__ import annotations

import itertools
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_PARAM_GRID: Dict[str, List[Any]] = {
    "zscore_window":          [10, 20, 30],
    "zscore_entry":           [1.5, 2.0, 2.5],
    "regime_min_persistence": [2, 3, 5],
}


@dataclass
class FoldResult:
    fold:          int
    train_start:   int
    train_end:     int
    test_start:    int
    test_end:      int
    best_params:   Dict[str, Any]
    train_sharpe:  float
    test_sharpe:   float
    test_pnl:      float
    dominant_regime: str
    n_trades:      int


@dataclass
class OptimizeResult:
    n_folds:              int
    param_grid:           Dict[str, List[Any]]
    best_params_global:   Dict[str, Any]
    best_params_by_regime: Dict[str, Dict[str, Any]]
    avg_test_sharpe:      float
    avg_test_pnl:         float
    fold_results:         List[FoldResult] = field(default_factory=list)
    result_df:            Optional[pd.DataFrame] = field(default=None, repr=False)

    def to_json(self, path: str) -> None:
        payload = {
            "n_folds":              self.n_folds,
            "param_grid":           self.param_grid,
            "best_params_global":   self.best_params_global,
            "best_params_by_regime": self.best_params_by_regime,
            "avg_test_sharpe":      round(self.avg_test_sharpe, 4),
            "avg_test_pnl":         round(self.avg_test_pnl, 6),
            "fold_results":         [asdict(f) for f in self.fold_results],
        }
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2)
        logger.info(f"OptimizeResult saved to {path}")


class WalkForwardOptimizer:
    """
    Walk-forward parameter optimizer.

    Parameters
    ----------
    train_bars   : bars in each training window (default 500)
    test_bars    : bars in each test window (default 100)
    step_bars    : step between folds (default = test_bars)
    n_jobs       : parallel workers (-1 = all CPUs, 1 = no parallelism)
    min_trades   : minimum trades required for a fold to be valid (default 3)
    """

    def __init__(
        self,
        train_bars: int = 500,
        test_bars:  int = 100,
        step_bars:  Optional[int] = None,
        n_jobs:     int = -1,
        min_trades: int = 3,
    ) -> None:
        self.train_bars = train_bars
        self.test_bars  = test_bars
        self.step_bars  = step_bars or test_bars
        self.n_jobs     = n_jobs
        self.min_trades = min_trades

    def run(
        self,
        y: pd.Series,
        x: pd.Series,
        param_grid: Optional[Dict[str, List[Any]]] = None,
    ) -> OptimizeResult:
        grid = param_grid or _DEFAULT_PARAM_GRID
        folds = self._make_folds(len(y))
        if not folds:
            raise ValueError(
                f"Series too short ({len(y)}) for train_bars={self.train_bars} + test_bars={self.test_bars}"
            )

        combos = self._expand_grid(grid)
        logger.info(
            f"WalkForwardOptimizer: {len(folds)} folds x {len(combos)} combos = {len(folds)*len(combos)} backtests"
        )

        # Run all (fold, combo) pairs
        all_rows: List[Dict] = []
        workers = min(self.n_jobs if self.n_jobs > 0 else os.cpu_count() or 4, len(folds))

        if workers <= 1 or len(folds) == 1:
            for fold_idx, (tr_s, tr_e, te_s, te_e) in enumerate(folds):
                rows = _run_fold(
                    fold_idx, tr_s, tr_e, te_s, te_e,
                    y.values, x.values, combos,
                )
                all_rows.extend(rows)
        else:
            with ProcessPoolExecutor(max_workers=workers) as exe:
                futures = {
                    exe.submit(
                        _run_fold, fold_idx, tr_s, tr_e, te_s, te_e,
                        y.values, x.values, combos,
                    ): fold_idx
                    for fold_idx, (tr_s, tr_e, te_s, te_e) in enumerate(folds)
                }
                for fut in as_completed(futures):
                    try:
                        all_rows.extend(fut.result())
                    except Exception as e:
                        logger.warning(f"Fold error: {e}")

        if not all_rows:
            raise RuntimeError("All folds failed.")

        df = pd.DataFrame(all_rows)
        fold_results = self._aggregate_fold_results(df, folds)
        best_global  = self._best_global(df)
        best_regime  = self._best_by_regime(df)
        avg_sharpe   = float(df[df["split"] == "test"]["sharpe"].mean())
        avg_pnl      = float(df[df["split"] == "test"]["pnl"].mean())

        return OptimizeResult(
            n_folds=len(folds),
            param_grid=grid,
            best_params_global=best_global,
            best_params_by_regime=best_regime,
            avg_test_sharpe=avg_sharpe,
            avg_test_pnl=avg_pnl,
            fold_results=fold_results,
            result_df=df,
        )

    # ------------------------------------------------------------------
    # Fold helpers
    # ------------------------------------------------------------------

    def _make_folds(self, n: int) -> List[Tuple[int, int, int, int]]:
        folds = []
        start = 0
        while start + self.train_bars + self.test_bars <= n:
            tr_s = start
            tr_e = start + self.train_bars
            te_s = tr_e
            te_e = te_s + self.test_bars
            folds.append((tr_s, tr_e, te_s, te_e))
            start += self.step_bars
        return folds

    @staticmethod
    def _expand_grid(grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
        keys   = list(grid.keys())
        values = list(grid.values())
        return [dict(zip(keys, combo)) for combo in itertools.product(*values)]

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def _aggregate_fold_results(
        self,
        df: pd.DataFrame,
        folds: List[Tuple[int, int, int, int]],
    ) -> List[FoldResult]:
        results = []
        for fold_idx, (tr_s, tr_e, te_s, te_e) in enumerate(folds):
            sub = df[df["fold"] == fold_idx]
            train_sub = sub[sub["split"] == "train"]
            test_sub  = sub[sub["split"] == "test"]
            if train_sub.empty or test_sub.empty:
                continue

            best_train_row = train_sub.loc[train_sub["sharpe"].idxmax()]
            best_params = {k: best_train_row[k] for k in _DEFAULT_PARAM_GRID.keys() if k in best_train_row.index}

            # Corresponding test row for best train params
            mask = pd.Series([True] * len(test_sub), index=test_sub.index)
            for k, v in best_params.items():
                if k in test_sub.columns:
                    mask &= test_sub[k] == v
            test_row = test_sub[mask]
            test_sharpe = float(test_row["sharpe"].iloc[0]) if not test_row.empty else 0.0
            test_pnl    = float(test_row["pnl"].iloc[0])    if not test_row.empty else 0.0
            n_trades    = int(test_row["n_trades"].iloc[0])  if not test_row.empty else 0
            dom_regime  = str(test_row["dominant_regime"].iloc[0]) if not test_row.empty else "unknown"

            results.append(FoldResult(
                fold=fold_idx,
                train_start=tr_s, train_end=tr_e,
                test_start=te_s,  test_end=te_e,
                best_params=best_params,
                train_sharpe=float(best_train_row["sharpe"]),
                test_sharpe=test_sharpe,
                test_pnl=test_pnl,
                dominant_regime=dom_regime,
                n_trades=n_trades,
            ))
        return results

    def _best_global(self, df: pd.DataFrame) -> Dict[str, Any]:
        param_keys = [k for k in _DEFAULT_PARAM_GRID.keys() if k in df.columns]
        test_df = df[df["split"] == "test"]
        if test_df.empty:
            return {}
        grouped = test_df.groupby(param_keys)["sharpe"].mean()
        best_idx = grouped.idxmax()
        if not isinstance(best_idx, tuple):
            best_idx = (best_idx,)
        return dict(zip(param_keys, best_idx))

    def _best_by_regime(
        self, df: pd.DataFrame
    ) -> Dict[str, Dict[str, Any]]:
        param_keys  = [k for k in _DEFAULT_PARAM_GRID.keys() if k in df.columns]
        test_df     = df[df["split"] == "test"]
        regimes     = test_df["dominant_regime"].unique() if "dominant_regime" in test_df.columns else []
        result: Dict[str, Dict[str, Any]] = {}
        for regime in regimes:
            regime_df = test_df[test_df["dominant_regime"] == regime]
            if regime_df.empty:
                continue
            grouped = regime_df.groupby(param_keys)["sharpe"].mean()
            best_idx = grouped.idxmax()
            if not isinstance(best_idx, tuple):
                best_idx = (best_idx,)
            result[str(regime)] = dict(zip(param_keys, best_idx))
        return result


# ------------------------------------------------------------------
# Module-level function (must be picklable for ProcessPoolExecutor)
# ------------------------------------------------------------------

def _run_fold(
    fold_idx: int,
    tr_s: int, tr_e: int,
    te_s: int, te_e: int,
    y_arr: np.ndarray,
    x_arr: np.ndarray,
    combos: List[Dict[str, Any]],
) -> List[Dict]:
    """
    Evaluate all param combos on one fold (train + test windows).
    Returns list of row dicts for the result DataFrame.
    """
    rows = []
    for combo in combos:
        for split, s, e in [("train", tr_s, tr_e), ("test", te_s, te_e)]:
            y_slice = pd.Series(y_arr[s:e])
            x_slice = pd.Series(x_arr[s:e])
            metrics = _backtest_combo(y_slice, x_slice, combo)
            row = {"fold": fold_idx, "split": split, **combo, **metrics}
            rows.append(row)
    return rows


def _backtest_combo(
    y: pd.Series,
    x: pd.Series,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Minimal vectorized backtest for one param combo on one window.
    Returns {sharpe, pnl, n_trades, dominant_regime}.
    """
    try:
        from backtest.auto_selector_runner import AutoSelectorRunner
        from core.regime_detector import TrendRegimeDetector

        class _Cfg:
            pass
        cfg = _Cfg()
        for k, v in params.items():
            setattr(cfg, k, v)
        cfg.sym_y = "Y"; cfg.sym_x = "X"
        cfg.funding_threshold_annual = 0.20
        cfg.half_life_hours = 24.0
        cfg.adx_window = 14
        cfg.regime_window = getattr(cfg, "zscore_window", 20)

        runner = AutoSelectorRunner(cfg, regime_window=cfg.regime_window)
        result = runner.run(y=y, x=x)

        pnl    = result.get("total_pnl", 0.0)
        n_bars = result.get("total_bars", 1)
        df_res = result.get("result_df")

        # Sharpe: annualised (assuming hourly bars = 8760 bars/year)
        if df_res is not None and "signal" in df_res.columns and "spread" in df_res.columns:
            rets = (
                df_res["signal"].shift(1).fillna(0).astype(float)
                * df_res["spread"].diff().fillna(0.0)
            )
            mean_r = float(rets.mean())
            std_r  = float(rets.std()) + 1e-10
            sharpe = float((mean_r / std_r) * np.sqrt(8760))
        else:
            sharpe = 0.0

        # Dominant regime
        regime_dist = result.get("regime_distribution", {})
        dominant_regime = max(regime_dist, key=lambda k: regime_dist[k]) if regime_dist else "unknown"

        return {
            "sharpe":          round(sharpe, 4),
            "pnl":             round(pnl, 6),
            "n_trades":        result.get("n_trades", 0),
            "dominant_regime": dominant_regime,
        }
    except Exception as e:
        return {"sharpe": -99.0, "pnl": 0.0, "n_trades": 0, "dominant_regime": "unknown", "error": str(e)}
