"""
QuantLuna — Kelly Cross-Pair Sizing  (Sprint 10)

Kelly Criterion adaptat pentru pairs trading / market-neutral strategies.

Formula Kelly standard:
  f* = (p * b - q) / b
  unde: p = win rate, q = 1-p, b = win/loss ratio

Probleme cu Kelly standard în pairs trading:
  1. P&L-ul unui trade de pairs nu e binar (win/loss) — e continuu
  2. Perechi simultane corelate → Kelly supraevaluează edge-ul agregat
  3. Crypto fat tails → Kelly complet duce frecvent la ruin în practică

Soluții implementate:
  1. Kelly continuu: f* = E[R] / E[R^2] (formula Thorp pentru distribuții continue)
  2. Fractional Kelly: f = f* * kelly_fraction (default 0.25)
  3. Cross-pair penalty: f_pair = f * diversification_discount(corr)
  4. Volatility targeting: sizing final reglat la vol_target / pair_vol
  5. Hard cap per pair și per portfolio agregat

Limite reale:
  - Kelly se bazează pe distribuția istorică a P&L-ului. Pe crypto,
    distribuția viitoare diferă frecvent de cea istorică.
  - Fractional Kelly (0.25) este conservator în mod intenționat.
    Kelly 0.5 → drawdowns mari în perioade adverse.
    Kelly 1.0 → teoretic optim pe termen lung, practic insuportabil.
  - Pe sample mic de trades (< 20), estimatele E[R] și E[R^2] sunt
    instabile. Folosiți sizing conservativ (vol target only) sub 20 trades.
  - Correlation-adjusted Kelly presupune că corelațiile rămân stabile.
    La correlation breakdown, discount-ul devine incorect instantaneu.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class KellyConfig:
    kelly_fraction: float = 0.25       # fractional Kelly (0.25 = 1/4 Kelly)
    vol_target: float = 0.01           # 1% vol target pe trade, din capital
    max_fraction_per_pair: float = 0.20  # max 20% capital per pair
    max_fraction_portfolio: float = 0.60 # max 60% capital total deployed
    min_trades_for_kelly: int = 20     # sub această valoare, folosim vol target only
    risk_free_rate: float = 0.0        # pentru Sharpe intern (ignorat în crypto)


@dataclass
class KellyResult:
    pair_id: str
    kelly_full: float          # f* Kelly complet (nu folosiți direct)
    kelly_fractional: float    # f* * kelly_fraction
    kelly_corr_adjusted: float # kelly_fractional * diversification_discount
    vol_target_fraction: float # sizing bazat exclusiv pe vol target
    final_fraction: float      # min(kelly_corr_adjusted, vol_target_fraction, cap)
    final_notional_usd: float  # fracție * capital
    n_trades_used: int
    method_used: str           # "kelly" sau "vol_target_only" (sample mic)
    notes: list

    def summary(self) -> str:
        return (
            f"Kelly [{self.pair_id}]: "
            f"f*={self.kelly_full:.4f} | "
            f"frac={self.kelly_fractional:.4f} | "
            f"corr_adj={self.kelly_corr_adjusted:.4f} | "
            f"final={self.final_fraction:.4f} "
            f"(${self.final_notional_usd:.0f}) | "
            f"method={self.method_used}"
        )


class KellyCrossPair:
    """
    Calculează sizing optim Kelly pentru un pair cu ajustare
    pentru corelație cu pozițiile active și pentru vol target.

    Utilizare:
      kelly = KellyCrossPair(cfg)
      result = kelly.compute(
          pair_id="ETH/BTC",
          trade_pnl_series=historical_pnl,
          spread_series=current_spread,
          capital_usd=10_000,
          deployed_fraction=0.20,
          diversification_discount=0.80,
      )
    """

    def __init__(self, cfg: Optional[KellyConfig] = None) -> None:
        self.cfg = cfg or KellyConfig()

    def compute(
        self,
        pair_id: str,
        trade_pnl_series: pd.Series,
        spread_series: pd.Series,
        capital_usd: float,
        deployed_fraction: float = 0.0,
        diversification_discount: float = 1.0,
    ) -> KellyResult:
        """
        Parametri:
          trade_pnl_series      — P&L per trade, normalizat ca fracție din capital
                                  (ex: [0.005, -0.002, 0.008, ...])
          spread_series         — seria de spread valori pentru vol target
          capital_usd           — capitalul total disponibil
          deployed_fraction     — fracția deja deployată în alte perechi [0, 1]
          diversification_discount — factor [0, 1] din SpreadCorrelationMatrix
        """
        cfg = self.cfg
        notes = []
        pnl = trade_pnl_series.dropna().astype(float).to_numpy()
        n = len(pnl)

        # --- Vol target sizing ---
        # FIX-2: spread-ul pairs trading e o diferență de prețuri, NU un preț.
        # pct_change() pe spread produce valori instabile sau ±inf când
        # spread-ul trece prin zero (frecvent în crypto). Calculăm volatilitatea
        # ca std() absolut al spread-ului, normalizat față de media sa absolută
        # pentru a obține o fracție comparabilă cu vol_target.
        spread_arr = pd.Series(spread_series).dropna().astype(float)
        if len(spread_arr) > 5:
            pair_vol_abs = float(spread_arr.std())
            spread_mean_abs = float(spread_arr.abs().mean())
            if spread_mean_abs > 1e-8 and pair_vol_abs > 0:
                pair_vol = pair_vol_abs / spread_mean_abs
            else:
                # spread_mean_abs ≈ 0 → spread oscilează în jurul lui 0 (normal
                # în pairs trading); folosim std absolut ca proxy direct
                pair_vol = pair_vol_abs if pair_vol_abs > 0 else 0.02
                notes.append("spread_near_zero — pair_vol calculat ca std absolut")
        else:
            pair_vol = 0.02
            notes.append("spread_buf_mic (<5 valori) — pair_vol fallback 0.02")

        pair_vol = max(pair_vol, 1e-4)  # floor realist (evită sizing astronomic)
        vol_target_fraction = min(
            cfg.vol_target / pair_vol,
            cfg.max_fraction_per_pair,
        )

        # --- Kelly continuu (Thorp) ---
        if n < cfg.min_trades_for_kelly:
            notes.append(f"vol_target_only (n={n} < {cfg.min_trades_for_kelly} trades)")
            kelly_full = vol_target_fraction
            kelly_frac = vol_target_fraction
            method = "vol_target_only"
        else:
            e_r = float(pnl.mean())
            e_r2 = float((pnl ** 2).mean())

            if e_r <= 0:
                notes.append("negative_edge — E[R] <= 0, Kelly=0")
                kelly_full = 0.0
                kelly_frac = 0.0
                method = "kelly"
            else:
                if e_r2 < 1e-12:
                    kelly_full = cfg.max_fraction_per_pair
                else:
                    kelly_full = float(e_r / e_r2)

                kelly_full = min(kelly_full, 1.0)  # cap la 100% capital
                kelly_frac = kelly_full * cfg.kelly_fraction
                method = "kelly"

        # --- Correlation adjustment ---
        kelly_corr_adj = kelly_frac * max(0.0, diversification_discount)

        # --- Portfolio cap: nu depășim spațiul rămas ---
        remaining = max(0.0, cfg.max_fraction_portfolio - deployed_fraction)
        kelly_corr_adj = min(kelly_corr_adj, remaining)
        vol_target_fraction = min(vol_target_fraction, remaining)

        # --- Final fraction: minimul dintre Kelly ajustat și vol target ---
        final_fraction = min(kelly_corr_adj, vol_target_fraction)
        final_fraction = max(0.0, min(final_fraction, cfg.max_fraction_per_pair))

        final_notional = final_fraction * capital_usd

        # Edge case warnings
        if final_fraction < 0.005:
            notes.append("tiny_allocation (<0.5% capital) — verifică edge quality")
        if diversification_discount < 0.60:
            notes.append(f"high_correlation_discount ({diversification_discount:.2f}) — pair redundant")

        return KellyResult(
            pair_id=pair_id,
            kelly_full=kelly_full,
            kelly_fractional=kelly_frac,
            kelly_corr_adjusted=kelly_corr_adj,
            vol_target_fraction=vol_target_fraction,
            final_fraction=final_fraction,
            final_notional_usd=final_notional,
            n_trades_used=n,
            method_used=method,
            notes=notes,
        )
