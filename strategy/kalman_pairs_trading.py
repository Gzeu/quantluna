"""
QuantLuna — KalmanPairsTrading (BaseStrategy wrapper)
Sprint 19

Wraps the existing SignalGenerator v4 (strategy/signal.py) as a BaseStrategy
so it participates in AutoStrategySelector scoring alongside new strategies.
All trading logic stays in SignalGenerator — this is a thin adapter.

Score logic:
  Flagship strategy — highest baseline (0.60).
  Best when: cointegration strong, ranging regime, half-life 4-48h,
             vol_rank 0.15-0.80, spread mean-reverting (autocorr < 0).
  Penalised: trending regime, very long half-life, weak cointegration.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from config.settings import SignalConfig
from core.spread import SpreadEngine
from strategy.base import BaseStrategy, MarketContext, Signal, TradeSignal
from strategy.signal import SignalGenerator
from strategy.signal import TradeSignal as LegacyTradeSignal


class KalmanPairsTrading(BaseStrategy):
    """
    Kalman Filter Pairs Trading — flagship QuantLuna strategy.
    Wraps SignalGenerator v4 as a BaseStrategy.
    """

    def __init__(
        self,
        spread_engine: SpreadEngine,
        cfg: Optional[SignalConfig] = None,
        cooldown_bars: int = 3,
        funding_threshold_annual: float = 0.05,
    ) -> None:
        self._generator = SignalGenerator(
            spread_engine=spread_engine,
            cfg=cfg,
            cooldown_bars=cooldown_bars,
            funding_threshold_annual=funding_threshold_annual,
        )
        self._spread_engine = spread_engine

    @property
    def name(self) -> str:
        return "KalmanPairsTrading"

    @property
    def version(self) -> str:
        return "4.0"

    def generate_live(
        self,
        y: float,
        x: float,
        ts: Optional[pd.Timestamp] = None,
        funding_annual: float = 0.0,
        regime_multiplier: float = 1.0,
        coint_valid: bool = True,
    ) -> TradeSignal:
        legacy: LegacyTradeSignal = self._generator.generate_live(
            y=y, x=x, ts=ts,
            funding_annual=funding_annual,
            regime_multiplier=regime_multiplier,
            coint_valid=coint_valid,
        )
        return self._adapt(legacy)

    def generate_batch(
        self,
        df: pd.DataFrame,
        funding_annual: Optional[pd.Series] = None,
        regime_multiplier: Optional[pd.Series] = None,
        coint_valid_series: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        result = self._generator.generate_batch(
            df=df,
            funding_annual=funding_annual,
            regime_multiplier=regime_multiplier,
            coint_valid_series=coint_valid_series,
        )
        result["strategy_name"] = self.name
        return result

    def score(self, context: MarketContext) -> float:
        score = 0.60  # flagship baseline

        if context.regime == "ranging":    score += 0.15
        elif context.regime == "trending": score -= 0.20
        elif context.regime == "breakout": score -= 0.10

        if context.coint_pvalue < 0.01:    score += 0.15
        elif context.coint_pvalue < 0.05:  score += 0.08
        elif context.coint_pvalue > 0.10:  score -= 0.20

        hl = context.half_life_hours
        if 4 <= hl <= 48:   score += 0.10
        elif hl > 120:       score -= 0.15
        elif hl < 2:         score -= 0.05

        if context.spread_autocorr < -0.15:  score += 0.10
        elif context.spread_autocorr > 0.20: score -= 0.15

        vr = context.vol_rank
        if 0.15 <= vr <= 0.80: score += 0.05
        elif vr > 0.95:         score -= 0.15

        if context.recent_win_rate > 0.55:   score += 0.05
        elif context.recent_win_rate < 0.35: score -= 0.10

        return float(np.clip(score, 0.0, 1.0))

    def reset(self) -> None:
        self._generator.reset()

    def _adapt(self, legacy: LegacyTradeSignal) -> TradeSignal:
        return TradeSignal(
            signal=Signal(int(legacy.signal)),
            confidence=legacy.confidence,
            reason=legacy.reason,
            strategy_name=self.name,
            zscore=legacy.zscore,
            beta=legacy.beta,
            alpha=legacy.alpha,
            spread=legacy.spread,
            regime_multiplier=legacy.regime_multiplier,
            half_life_hours=legacy.half_life_hours,
            timestamp=legacy.timestamp,
            meta={
                "uncertainty":         legacy.uncertainty,
                "kalman_gain":         legacy.kalman_gain,
                "effective_threshold": legacy.effective_threshold,
                "vol_rank":            legacy.vol_rank,
                "dz_blocked":          legacy.dz_blocked,
                "partial_close_pct":   legacy.partial_close_pct,
                "coint_valid":         legacy.coint_valid,
                "bars_in_trade":       legacy.bars_in_trade,
            },
        )
