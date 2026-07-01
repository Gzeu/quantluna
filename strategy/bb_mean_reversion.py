"""
QuantLuna — Bollinger Bands Mean Reversion Strategy
Sprint 19

Logica:
  - Bollinger Bands pe spread (rolling mean +/- k*std)
  - Entry LONG_SPREAD cand spread < lower band
  - Entry SHORT_SPREAD cand spread > upper band
  - Exit la rolling mean; hard stop la n_std_stop * std

Score optim: ranging, vol_rank 0.2-0.75, coint p < 0.05, autocorr < -0.1
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional

import numpy as np
import pandas as pd
from loguru import logger

from strategy.base import BaseStrategy, MarketContext, Signal, TradeSignal


class BollingerBandsMeanReversion(BaseStrategy):

    def __init__(
        self,
        window: int = 20,
        n_std_entry: float = 2.0,
        n_std_exit: float = 0.0,
        n_std_stop: float = 3.5,
        min_std: float = 1e-6,
        cooldown: int = 2,
    ) -> None:
        self.window = window
        self.n_std_entry = n_std_entry
        self.n_std_exit = n_std_exit
        self.n_std_stop = n_std_stop
        self.min_std = min_std
        self._base_cooldown = cooldown
        self._spread_buf: Deque[float] = deque(maxlen=window)
        self._in_trade = False
        self._entry_side = 0
        self._bars_in_trade = 0
        self._cooldown_remaining = 0

    @property
    def name(self) -> str:
        return "BollingerBandsMeanReversion"

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
        spread = y - x
        self._spread_buf.append(spread)
        if len(self._spread_buf) < self.window:
            return self._make_exit("warming_up", ts)
        buf = np.array(self._spread_buf)
        mu  = float(np.mean(buf))
        std = float(np.std(buf, ddof=1))
        if std < self.min_std:
            return self._make_exit("flat_spread", ts)
        upper = mu + self.n_std_entry * std
        lower = mu - self.n_std_entry * std
        sig, conf, reason = self._bar_logic(
            spread, mu, std, upper, lower,
            mu + self.n_std_stop * std, mu - self.n_std_stop * std,
            mu + self.n_std_exit * std, mu - self.n_std_exit * std,
            funding_annual, regime_multiplier,
        )
        bb_z = (spread - mu) / std
        return TradeSignal(
            signal=sig, confidence=conf, reason=reason,
            strategy_name=self.name, zscore=bb_z, spread=spread,
            regime_multiplier=regime_multiplier, timestamp=ts,
            meta={"bb_upper": upper, "bb_lower": lower, "bb_mid": mu},
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
        df["bb_upper"] = np.nan
        df["bb_lower"] = np.nan
        df["bb_mid"]   = np.nan
        self.reset()
        if "spread" not in df.columns:
            return df
        spreads   = df["spread"].values
        roll_mean = pd.Series(spreads).rolling(self.window).mean().values
        roll_std  = pd.Series(spreads).rolling(self.window).std().values
        for i in range(len(df)):
            mu  = roll_mean[i]
            std = roll_std[i]
            if np.isnan(mu) or np.isnan(std) or std < self.min_std:
                df.iat[i, df.columns.get_loc("reason")] = "warming_up"
                continue
            upper = mu + self.n_std_entry * std
            lower = mu - self.n_std_entry * std
            df.iat[i, df.columns.get_loc("bb_upper")] = upper
            df.iat[i, df.columns.get_loc("bb_lower")] = lower
            df.iat[i, df.columns.get_loc("bb_mid")]   = mu
            sp   = float(spreads[i])
            fund = float(funding_annual.iloc[i]) if funding_annual is not None else 0.0
            reg  = float(regime_multiplier.iloc[i]) if regime_multiplier is not None else 1.0
            sig, conf, reason = self._bar_logic(
                sp, mu, std, upper, lower,
                mu + self.n_std_stop * std, mu - self.n_std_stop * std,
                mu + self.n_std_exit * std, mu - self.n_std_exit * std,
                fund, reg,
            )
            df.iat[i, df.columns.get_loc("signal")]        = int(sig)
            df.iat[i, df.columns.get_loc("confidence")]    = conf
            df.iat[i, df.columns.get_loc("reason")]        = reason
            df.iat[i, df.columns.get_loc("strategy_name")] = self.name
        return df

    def score(self, context: MarketContext) -> float:
        score = 0.50
        if context.regime == "ranging":    score += 0.20
        elif context.regime == "trending": score -= 0.25
        elif context.regime == "breakout": score -= 0.15
        vr = context.vol_rank
        if 0.20 <= vr <= 0.75:  score += 0.15
        elif vr > 0.90:          score -= 0.20
        elif vr < 0.10:          score -= 0.10
        if context.coint_pvalue < 0.05:    score += 0.10
        elif context.coint_pvalue > 0.10:  score -= 0.15
        if context.spread_autocorr < -0.10:  score += 0.10
        elif context.spread_autocorr > 0.20: score -= 0.10
        hl = context.half_life_hours
        if 2 <= hl <= 48:  score += 0.10
        elif hl > 120:      score -= 0.10
        return float(np.clip(score, 0.0, 1.0))

    def reset(self) -> None:
        self._spread_buf.clear()
        self._in_trade = False
        self._entry_side = 0
        self._bars_in_trade = 0
        self._cooldown_remaining = 0

    def _bar_logic(self, spread, mu, std, upper, lower,
                   stop_upper, stop_lower, exit_upper, exit_lower,
                   funding_annual, regime_multiplier):
        if self._in_trade:
            if self._entry_side == 1 and spread >= stop_upper:
                return self._do_exit("hard_stop")
            if self._entry_side == -1 and spread <= stop_lower:
                return self._do_exit("hard_stop")
        if self._in_trade:
            if self._entry_side == 1 and spread >= exit_upper:
                return self._do_exit("mean_reversion")
            if self._entry_side == -1 and spread <= exit_lower:
                return self._do_exit("mean_reversion")
            self._bars_in_trade += 1
            conf = float(np.clip(abs(spread - mu) / (self.n_std_entry * std + 1e-9), 0.0, 1.0))
            sig = Signal.LONG_SPREAD if self._entry_side == 1 else Signal.SHORT_SPREAD
            return sig, conf, "hold"
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            return Signal.EXIT, 0.0, "cooldown"
        if abs(funding_annual) > 0.05:
            return Signal.EXIT, 0.0, "funding_gate"
        if regime_multiplier <= 0.0:
            return Signal.EXIT, 0.0, "regime_breakdown"
        if spread <= lower:
            conf = float(np.clip((abs((spread-mu)/std) - self.n_std_entry) / self.n_std_entry, 0.0, 1.0))
            self._in_trade = True; self._entry_side = 1; self._bars_in_trade = 1
            return Signal.LONG_SPREAD, conf, "bb_entry_long"
        if spread >= upper:
            conf = float(np.clip((abs((spread-mu)/std) - self.n_std_entry) / self.n_std_entry, 0.0, 1.0))
            self._in_trade = True; self._entry_side = -1; self._bars_in_trade = 1
            return Signal.SHORT_SPREAD, conf, "bb_entry_short"
        return Signal.EXIT, 0.0, "no_signal"

    def _do_exit(self, reason: str):
        self._in_trade = False; self._entry_side = 0
        self._bars_in_trade = 0; self._cooldown_remaining = self._base_cooldown
        return Signal.EXIT, 1.0, reason
