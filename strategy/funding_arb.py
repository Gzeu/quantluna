"""
QuantLuna — Funding Rate Arbitrage Strategy
Sprint 19

Logica (crypto perpetual futures):
  Funding pozitiv ridicat -> SHORT_SPREAD (incasezi funding de la longi)
  Funding negativ ridicat -> LONG_SPREAD (incasezi funding de la shorti)
  Exit: funding neutral, zscore stop, funding flip, max_hold_bars.

Score optim: |funding_annual| > 20%, ranging/trending.
Ignorat complet sub 5%/an.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from strategy.base import BaseStrategy, MarketContext, Signal, TradeSignal


class FundingRateArbitrage(BaseStrategy):

    def __init__(
        self,
        entry_funding_annual: float = 0.20,
        exit_funding_annual: float = 0.05,
        stop_zscore: float = 2.5,
        max_hold_bars: int = 48,
        funding_flip_exit: bool = True,
    ) -> None:
        self.entry_funding = entry_funding_annual
        self.exit_funding  = exit_funding_annual
        self.stop_zscore   = stop_zscore
        self.max_hold_bars = max_hold_bars
        self.funding_flip_exit = funding_flip_exit
        self._in_trade = False
        self._entry_side = 0
        self._bars_in_trade = 0
        self._entry_funding_val = 0.0
        self._total_funding_collected = 0.0

    @property
    def name(self) -> str:
        return "FundingRateArbitrage"

    @property
    def version(self) -> str:
        return "1.0"

    def generate_live(
        self,
        y: float,
        x: float,
        ts: Optional[pd.Timestamp] = None,
        funding_annual: float = 0.0,
        regime_multiplier: float = 1.0,
        coint_valid: bool = True,
    ) -> TradeSignal:
        zscore = float(y)
        sig, conf, reason = self._bar_logic(zscore, funding_annual, regime_multiplier)
        return TradeSignal(
            signal=sig, confidence=conf, reason=reason,
            strategy_name=self.name, zscore=zscore,
            regime_multiplier=regime_multiplier, timestamp=ts,
            meta={
                "funding_annual":          round(funding_annual, 6),
                "bars_in_trade":           self._bars_in_trade,
                "total_funding_collected": round(self._total_funding_collected, 6),
            },
        )

    def generate_batch(
        self,
        df: pd.DataFrame,
        funding_annual: Optional[pd.Series] = None,
        regime_multiplier: Optional[pd.Series] = None,
        coint_valid_series: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        df = df.copy()
        df["signal"] = int(Signal.EXIT)
        df["confidence"] = 0.0
        df["reason"] = ""
        df["strategy_name"] = self.name
        df["funding_collected"] = 0.0
        self.reset()
        zscore_col = "zscore" if "zscore" in df.columns else None
        for i in range(len(df)):
            z    = float(df[zscore_col].iloc[i]) if zscore_col else 0.0
            fund = float(funding_annual.iloc[i]) if funding_annual is not None else 0.0
            reg  = float(regime_multiplier.iloc[i]) if regime_multiplier is not None else 1.0
            sig, conf, reason = self._bar_logic(z, fund, reg)
            df.iat[i, df.columns.get_loc("signal")]        = int(sig)
            df.iat[i, df.columns.get_loc("confidence")]    = conf
            df.iat[i, df.columns.get_loc("reason")]        = reason
            df.iat[i, df.columns.get_loc("strategy_name")] = self.name
            if self._in_trade and fund != 0:
                df.iat[i, df.columns.get_loc("funding_collected")] = abs(fund) / 8760
        return df

    def score(self, context: MarketContext) -> float:
        f = abs(context.funding_annual)
        if f < 0.05:   return 0.0
        if f < 0.10:   score = 0.20
        elif f < 0.20: score = 0.45
        elif f < 0.50: score = 0.70
        else:          score = 0.90
        if context.regime == "ranging":    score += 0.05
        elif context.regime == "trending": score += 0.03
        elif context.regime == "breakout": score -= 0.10
        if context.recent_win_rate > 0.6:  score += 0.05
        return float(np.clip(score, 0.0, 1.0))

    def reset(self) -> None:
        self._in_trade = False; self._entry_side = 0
        self._bars_in_trade = 0; self._entry_funding_val = 0.0
        self._total_funding_collected = 0.0

    def _bar_logic(self, zscore: float, funding_annual: float, regime_multiplier: float):
        if self._in_trade and abs(zscore) >= self.stop_zscore:
            return self._do_exit("zscore_stop")
        if self._in_trade and self._bars_in_trade >= self.max_hold_bars:
            return self._do_exit("max_hold")
        if self._in_trade and self.funding_flip_exit:
            if self._entry_side == -1 and funding_annual < 0:
                return self._do_exit("funding_flip")
            if self._entry_side == 1 and funding_annual > 0:
                return self._do_exit("funding_flip")
        if self._in_trade and abs(funding_annual) < self.exit_funding:
            return self._do_exit("funding_neutral")
        if self._in_trade:
            self._bars_in_trade += 1
            self._total_funding_collected += abs(funding_annual) / 8760
            conf = float(np.clip(abs(funding_annual) / max(self.entry_funding, 1e-9), 0.5, 1.0))
            return (Signal.SHORT_SPREAD if self._entry_side == -1 else Signal.LONG_SPREAD), conf, "hold_funding"
        if regime_multiplier <= 0.0:
            return Signal.EXIT, 0.0, "regime_breakdown"
        if funding_annual >= self.entry_funding:
            conf = float(np.clip(funding_annual / (2 * self.entry_funding), 0.3, 1.0))
            self._in_trade = True; self._entry_side = -1
            self._bars_in_trade = 1; self._entry_funding_val = funding_annual
            return Signal.SHORT_SPREAD, conf, "funding_short_entry"
        if funding_annual <= -self.entry_funding:
            conf = float(np.clip(abs(funding_annual) / (2 * self.entry_funding), 0.3, 1.0))
            self._in_trade = True; self._entry_side = 1
            self._bars_in_trade = 1; self._entry_funding_val = funding_annual
            return Signal.LONG_SPREAD, conf, "funding_long_entry"
        return Signal.EXIT, 0.0, "funding_insufficient"

    def _do_exit(self, reason: str):
        self._in_trade = False; self._entry_side = 0
        self._bars_in_trade = 0; self._total_funding_collected = 0.0
        return Signal.EXIT, 1.0, reason
