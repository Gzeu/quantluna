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

Fix #6: KalmanScoringWeights dataclass — all score() numeric adjustments
  are now configurable and can be tuned via the Optuna optimizer's SearchSpace.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from config.settings import SignalConfig
from core.spread import SpreadEngine
from strategy.base import BaseStrategy, MarketContext, Signal, TradeSignal
from strategy.signal import SignalGenerator
from strategy.signal import TradeSignal as LegacyTradeSignal


@dataclass
class KalmanScoringWeights:
    """
    All numeric scoring adjustments for KalmanPairsTrading.score().

    Externalised from hardcoded literals so that:
      1. They can be passed as constructor args for A/B testing.
      2. optimizer.py SearchSpace can include them as Optuna parameters.
      3. Values are documented in one place with clear semantics.

    Usage with optimizer:
        ss.kalman_score_baseline_low  = 0.50
        ss.kalman_score_baseline_high = 0.70
        # ... then trial.suggest_float("kalman_score_baseline", ...)
        # and pass KalmanScoringWeights(baseline=params["kalman_score_baseline"])
    """
    # Starting score before adjustments
    baseline: float = 0.60

    # Regime adjustments
    regime_ranging_bonus:   float =  0.15
    regime_trending_penalty: float = -0.20
    regime_breakout_penalty: float = -0.10

    # Cointegration p-value adjustments
    coint_p001_bonus:   float =  0.15   # p < 0.01
    coint_p005_bonus:   float =  0.08   # p < 0.05
    coint_p010_penalty: float = -0.20   # p > 0.10

    # Half-life adjustments (hours)
    hl_optimal_low:    float =   4.0   # optimal range lower bound
    hl_optimal_high:   float =  48.0   # optimal range upper bound
    hl_long_threshold: float = 120.0   # above this → penalty
    hl_short_threshold: float =  2.0   # below this → penalty
    hl_optimal_bonus:  float =  0.10
    hl_long_penalty:   float = -0.15
    hl_short_penalty:  float = -0.05

    # Spread autocorrelation adjustments
    autocorr_good_threshold: float = -0.15   # below → mean-reverting
    autocorr_bad_threshold:  float =  0.20   # above → trending spread
    autocorr_good_bonus:     float =  0.10
    autocorr_bad_penalty:    float = -0.15

    # Volatility rank adjustments
    vol_rank_good_low:   float = 0.15
    vol_rank_good_high:  float = 0.80
    vol_rank_extreme:    float = 0.95   # above → extreme vol penalty
    vol_rank_good_bonus: float =  0.05
    vol_rank_extreme_penalty: float = -0.15

    # Recent win rate adjustments
    win_rate_good_threshold: float = 0.55
    win_rate_bad_threshold:  float = 0.35
    win_rate_good_bonus:     float =  0.05
    win_rate_bad_penalty:    float = -0.10


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
        scoring_weights: Optional[KalmanScoringWeights] = None,
    ) -> None:
        self._generator = SignalGenerator(
            spread_engine=spread_engine,
            cfg=cfg,
            cooldown_bars=cooldown_bars,
            funding_threshold_annual=funding_threshold_annual,
        )
        self._spread_engine = spread_engine
        # Fix #6: use configurable weights instead of hardcoded literals
        self._weights = scoring_weights or KalmanScoringWeights()

    @property
    def name(self) -> str:
        return "KalmanPairsTrading"

    @property
    def version(self) -> str:
        return "4.1"

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
        """
        Fix #6: all numeric adjustments read from self._weights (KalmanScoringWeights)
        instead of inline magic numbers. Semantics are unchanged; values are identical
        to the previous hardcoded defaults.
        """
        w = self._weights
        score = w.baseline

        # Regime
        if context.regime == "ranging":
            score += w.regime_ranging_bonus
        elif context.regime == "trending":
            score += w.regime_trending_penalty
        elif context.regime == "breakout":
            score += w.regime_breakout_penalty

        # Cointegration p-value
        if context.coint_pvalue < 0.01:
            score += w.coint_p001_bonus
        elif context.coint_pvalue < 0.05:
            score += w.coint_p005_bonus
        elif context.coint_pvalue > 0.10:
            score += w.coint_p010_penalty

        # Half-life
        hl = context.half_life_hours
        if w.hl_optimal_low <= hl <= w.hl_optimal_high:
            score += w.hl_optimal_bonus
        elif hl > w.hl_long_threshold:
            score += w.hl_long_penalty
        elif hl < w.hl_short_threshold:
            score += w.hl_short_penalty

        # Spread autocorrelation
        if context.spread_autocorr < w.autocorr_good_threshold:
            score += w.autocorr_good_bonus
        elif context.spread_autocorr > w.autocorr_bad_threshold:
            score += w.autocorr_bad_penalty

        # Volatility rank
        vr = context.vol_rank
        if w.vol_rank_good_low <= vr <= w.vol_rank_good_high:
            score += w.vol_rank_good_bonus
        elif vr > w.vol_rank_extreme:
            score += w.vol_rank_extreme_penalty

        # Recent win rate
        if context.recent_win_rate > w.win_rate_good_threshold:
            score += w.win_rate_good_bonus
        elif context.recent_win_rate < w.win_rate_bad_threshold:
            score += w.win_rate_bad_penalty

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
