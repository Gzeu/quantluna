"""
risk/kelly.py  —  Kelly Criterion sizing for QuantLuna

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

CANONICAL MODULE — do not import from risk.kelly_sizer; that module is a
deprecated compatibility shim and will be removed in a future sprint.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class KellyConfig:
    kelly_fraction: float = 0.25
    vol_target: float = 0.02
    max_position_pct: float = 0.10
    min_position_pct: float = 0.005
    correlation_penalty: bool = True


@dataclass
class KellyResult:
    pair: str
    kelly_f: float
    fractional_f: float
    vol_adjusted_f: float
    corr_adjusted_f: float
    final_pct: float
    final_usdt: float
    capped: bool
    reasoning: str


class KellyCrossPair:
    """
    Kelly sizer for pairs trading strategies (continuous P&L).

    Uses Thorp's continuous Kelly: f* = E[R] / E[R^2]
    with fractional scaling, correlation penalty, and vol targeting.
    """

    def __init__(self, config: Optional[KellyConfig] = None) -> None:
        self.config = config or KellyConfig()

    def size(
        self,
        pnl_history: list[float],
        capital_usdt: float,
        pair_vol: float,
        correlation: float = 0.0,
        pair: str = "UNKNOWN",
    ) -> KellyResult:
        cfg = self.config
        arr = np.array(pnl_history, dtype=float)
        n   = len(arr)

        if n < 5 or pair_vol <= 0:
            pct = cfg.min_position_pct
            return KellyResult(
                pair=pair, kelly_f=0.0, fractional_f=pct,
                vol_adjusted_f=pct, corr_adjusted_f=pct,
                final_pct=pct, final_usdt=round(capital_usdt * pct, 2),
                capped=False, reasoning="insufficient history — using min sizing",
            )

        e_r  = float(np.mean(arr))
        e_r2 = float(np.mean(arr ** 2))
        kelly_f = (e_r / e_r2) if e_r2 > 0 else 0.0

        fractional = kelly_f * cfg.kelly_fraction

        # Vol targeting overlay
        vol_f = (cfg.vol_target / pair_vol) if pair_vol > 0 else fractional
        vol_adj = min(fractional, vol_f)

        # Correlation penalty
        corr_adj = vol_adj
        if cfg.correlation_penalty and abs(correlation) > 0.3:
            discount = 1.0 - (abs(correlation) - 0.3) / 0.7 * 0.5
            corr_adj = vol_adj * max(discount, 0.5)

        capped = False
        final = corr_adj
        if final > cfg.max_position_pct:
            final = cfg.max_position_pct
            capped = True
        elif final < cfg.min_position_pct:
            final = cfg.min_position_pct

        reasoning = (
            f"kelly={kelly_f:.4f} frac={fractional:.4f} "
            f"vol_adj={vol_adj:.4f} corr_adj={corr_adj:.4f}"
            + (" [capped]" if capped else "")
        )

        return KellyResult(
            pair=pair, kelly_f=round(kelly_f, 6), fractional_f=round(fractional, 6),
            vol_adjusted_f=round(vol_adj, 6), corr_adjusted_f=round(corr_adj, 6),
            final_pct=round(final, 6), final_usdt=round(capital_usdt * final, 2),
            capped=capped, reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# KellySizer — simple binary win/loss Kelly for single strategies
# (previously in risk/kelly_sizer.py — canonical location is now here)
# ---------------------------------------------------------------------------

@dataclass
class KellySimpleResult:
    kelly_fraction: float
    fractional_kelly: float
    recommended_pct: float
    capital_usdt: float
    position_usdt: float
    capped: bool


class KellySizer:
    """
    Kelly Criterion sizer using binary win/loss statistics.

    Suitable for strategies where each trade outcome is close to binary
    (e.g., stop-loss / take-profit targets). For continuous P&L distributions
    use KellyCrossPair instead.

    Usage::

        sizer = KellySizer(kelly_fraction=0.25, max_pct=0.10)
        result = sizer.size(
            capital_usdt=10_000.0,
            win_rate=0.60,
            avg_win=150.0,
            avg_loss=100.0,
        )
    """

    def __init__(
        self,
        kelly_fraction: float = 0.25,
        max_pct: float = 0.10,
        min_pct: float = 0.005,
    ) -> None:
        self._kelly_fraction = kelly_fraction
        self._max_pct = max_pct
        self._min_pct = min_pct

    def size(
        self,
        capital_usdt: float,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
    ) -> KellySimpleResult:
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return self._fallback(capital_usdt)

        b = abs(avg_win / avg_loss)
        p = win_rate
        q = 1.0 - p
        full_kelly = (b * p - q) / b

        if full_kelly <= 0:
            return self._fallback(capital_usdt)

        frac   = full_kelly * self._kelly_fraction
        capped = False

        if frac > self._max_pct:
            frac   = self._max_pct
            capped = True
        elif frac < self._min_pct:
            frac = self._min_pct

        return KellySimpleResult(
            kelly_fraction=round(full_kelly, 6),
            fractional_kelly=round(frac, 6),
            recommended_pct=round(frac * 100, 4),
            capital_usdt=capital_usdt,
            position_usdt=round(capital_usdt * frac, 2),
            capped=capped,
        )

    def size_from_analytics(self, capital_usdt: float, analytics) -> KellySimpleResult:
        """Convenience: accepts a PerformanceMetrics object."""
        return self.size(
            capital_usdt=capital_usdt,
            win_rate=analytics.win_rate,
            avg_win=analytics.avg_win,
            avg_loss=abs(analytics.avg_loss),
        )

    def _fallback(self, capital_usdt: float) -> KellySimpleResult:
        frac = self._min_pct
        return KellySimpleResult(
            kelly_fraction=0.0,
            fractional_kelly=frac,
            recommended_pct=round(frac * 100, 4),
            capital_usdt=capital_usdt,
            position_usdt=round(capital_usdt * frac, 2),
            capped=False,
        )
