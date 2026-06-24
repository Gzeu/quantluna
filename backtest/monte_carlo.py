"""
QuantLuna — Monte Carlo Engine Sprint 8

Monte Carlo robustness layer pentru pairs trading / market-neutral strategies.

Scop:
- Testare robustness peste secvența de trade-uri OOS, nu doar equity path-ul unic
- Estimare distribuție pentru max drawdown, CAGR proxy, ruin probability,
  worst-case percentile și sensitivity la cost inflation
- Validare dacă edge-ul din walk-forward e robust sau depinde de ordinea exactă
  a trade-urilor istorice

Metode implementate:
1. bootstrap_trades  — resample cu replacement din distribuția trade-urilor
2. permutation       — reshuffle ordinea trade-urilor fără replacement
3. block_bootstrap   — resample pe blocuri pentru a păstra clustering parțial
4. cost_stress       — multiplică fees/slippage/funding și rerulează equity paths

Limitări reale:
- Monte Carlo pe trade list nu capturează regime shifts care nu există în sample
- bootstrap cu replacement poate supraestima tails dacă sample-ul e foarte mic
- permutation test nu schimbă distribuția P&L, doar path dependency
- Nu înlocuiește out-of-sample; doar măsoară fragilitatea lui
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class MonteCarloConfig:
    n_sims: int = 2000
    seed: int = 42
    method: str = "bootstrap_trades"   # bootstrap_trades | permutation | block_bootstrap | cost_stress
    block_size: int = 5                 # pentru block_bootstrap
    capital_usd: float = 10_000.0
    ruin_threshold_pct: float = -30.0   # ruin dacă DD <= -30%
    cost_stress_multiplier: float = 1.5 # pentru cost_stress


@dataclass
class MonteCarloSummary:
    n_sims: int
    median_total_pnl: float
    p05_total_pnl: float
    p95_total_pnl: float
    median_max_dd_pct: float
    p95_max_dd_pct: float
    ruin_probability: float
    prob_negative_total_pnl: float
    median_profit_factor: float
    p05_profit_factor: float


class MonteCarloEngine:
    """
    Rulează simulări Monte Carlo peste trade-uri OOS.

    Input așteptat:
    DataFrame cu coloane minime:
      - net_pnl
      - gross_pnl (opțional pentru PF mai bun)
      - fees, slippage, funding_cost (opțional pentru cost_stress)
    """

    def __init__(self, trades_df: pd.DataFrame, cfg: Optional[MonteCarloConfig] = None) -> None:
        self.df = trades_df.copy().reset_index(drop=True)
        self.cfg = cfg or MonteCarloConfig()
        self.rng = np.random.default_rng(self.cfg.seed)
        self._validate()

    def _validate(self) -> None:
        if "net_pnl" not in self.df.columns:
            raise ValueError("MonteCarloEngine requires 'net_pnl' column")
        if len(self.df) < 10:
            raise ValueError("Need at least 10 trades for meaningful Monte Carlo")

    def run(self) -> tuple[pd.DataFrame, MonteCarloSummary]:
        results: List[Dict] = []
        n = len(self.df)

        for sim in range(self.cfg.n_sims):
            path = self._sample_path(n)
            stats = self._path_stats(path)
            stats["sim"] = sim
            results.append(stats)

        res_df = pd.DataFrame(results)
        summary = MonteCarloSummary(
            n_sims=self.cfg.n_sims,
            median_total_pnl=float(res_df["total_pnl"].median()),
            p05_total_pnl=float(res_df["total_pnl"].quantile(0.05)),
            p95_total_pnl=float(res_df["total_pnl"].quantile(0.95)),
            median_max_dd_pct=float(res_df["max_dd_pct"].median()),
            p95_max_dd_pct=float(res_df["max_dd_pct"].quantile(0.95)),
            ruin_probability=float((res_df["max_dd_pct"] <= self.cfg.ruin_threshold_pct).mean()),
            prob_negative_total_pnl=float((res_df["total_pnl"] < 0).mean()),
            median_profit_factor=float(res_df["profit_factor"].median()),
            p05_profit_factor=float(res_df["profit_factor"].quantile(0.05)),
        )
        return res_df, summary

    def _sample_path(self, n: int) -> pd.DataFrame:
        method = self.cfg.method

        if method == "bootstrap_trades":
            idx = self.rng.integers(0, n, size=n)
            return self.df.iloc[idx].reset_index(drop=True)

        if method == "permutation":
            idx = self.rng.permutation(n)
            return self.df.iloc[idx].reset_index(drop=True)

        if method == "block_bootstrap":
            return self._block_bootstrap_path(n)

        if method == "cost_stress":
            stressed = self.df.copy()
            if {"fees", "slippage", "funding_cost", "gross_pnl"}.issubset(stressed.columns):
                extra_cost = (
                    stressed["fees"] + stressed["slippage"] + stressed["funding_cost"]
                ) * (self.cfg.cost_stress_multiplier - 1.0)
                stressed["net_pnl"] = stressed["net_pnl"] - extra_cost
            idx = self.rng.integers(0, n, size=n)
            return stressed.iloc[idx].reset_index(drop=True)

        raise ValueError(f"Unknown Monte Carlo method: {method}")

    def _block_bootstrap_path(self, n: int) -> pd.DataFrame:
        block = max(1, int(self.cfg.block_size))
        chunks = []
        while sum(len(c) for c in chunks) < n:
            start = int(self.rng.integers(0, max(1, n - block + 1)))
            chunks.append(self.df.iloc[start:start + block])
        out = pd.concat(chunks, ignore_index=True).iloc[:n]
        return out.reset_index(drop=True)

    def _path_stats(self, path: pd.DataFrame) -> Dict:
        pnl = path["net_pnl"].astype(float).to_numpy()
        equity = self.cfg.capital_usd + np.cumsum(pnl)
        peak = np.maximum.accumulate(equity)
        dd = equity - peak
        dd_pct = np.where(peak > 0, dd / peak * 100.0, 0.0)

        gains = pnl[pnl > 0].sum() if np.any(pnl > 0) else 0.0
        losses = abs(pnl[pnl < 0].sum()) if np.any(pnl < 0) else 1e-9
        profit_factor = float(gains / losses)

        return {
            "total_pnl": float(pnl.sum()),
            "max_dd": float(dd.min()),
            "max_dd_pct": float(dd_pct.min()),
            "profit_factor": profit_factor,
            "win_rate": float((pnl > 0).mean()),
        }
