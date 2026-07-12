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

Fix #7 (Gap #3): generate_batch() now accepts coint_pvalue_series: Optional[pd.Series]
  so the real per-bar ADF p-value produced by WalkForwardEngine._build_coint_pvalue_series
  reaches score() and the meta dict, instead of being discarded.

Changes (code review 2026-07-12):
  - Patch 6: changed coint_pvalue fallback from 0.05 to 0.10.
    0.05 sits exactly on the score() boundary between the p<0.05 bonus
    and the p>0.10 penalty, triggering neither. 0.10 is the conservative
    fallback that correctly triggers coint_p010_penalty in score().
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
    """
    # Starting score before adjustments
    baseline: float = 0.60

    # Regime adjustments
    regime_ranging_bonus:    float =  0.15
    regime_trending_penalty: float = -0.20
    regime_breakout_penalty: float = -0.10

    # Cointegration p-value adjustments
    coint_p001_bonus:   float =  0.15   # p < 0.01
    coint_p005_bonus:   float =  0.08   # p < 0.05
    coint_p010_penalty: float = -0.20   # p > 0.10

    # Half-life adjustments (hours)
    hl_optimal_low:     float =   4.0
    hl_optimal_high:    float =  48.0
    hl_long_threshold:  float = 120.0
    hl_short_threshold: float =   2.0
    hl_optimal_bonus:   float =  0.10
    hl_long_penalty:    float = -0.15
    hl_short_penalty:   float = -0.05

    # Spread autocorrelation adjustments
    autocorr_good_threshold: float = -0.15
    autocorr_bad_threshold:  float =  0.20
    autocorr_good_bonus:     float =  0.10
    autocorr_bad_penalty:    float = -0.15

    # Volatility rank adjustments
    vol_rank_good_low:        float = 0.15
    vol_rank_good_high:       float = 0.80
    vol_rank_extreme:         float = 0.95
    vol_rank_good_bonus:      float =  0.05
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
        self._weights = scoring_weights or KalmanScoringWeights()

    @property
    def name(self) -> str:
        return "KalmanPairsTrading"

    @property
    def version(self) -> str:
        return "4.2"

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
        coint_pvalue_series: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        """
        Fix #7 (Gap #3): added coint_pvalue_series parameter.

        Previously generate_batch() only accepted coint_valid_series (bool) which
        prevented the real per-bar ADF p-value from reaching the downstream meta dict
        and score() comparisons (p < 0.01 / p < 0.05 / p > 0.10 thresholds).

        Now: if coint_pvalue_series is provided its values are attached to every row
        in the result DataFrame under column 'coint_pvalue', making them available
        to AutoStrategySelector.generate_batch() and MarketContext construction.

        Patch 6: fallback changed from 0.05 to 0.10.
        0.05 sits exactly on the score() boundary (neither bonus nor penalty).
        0.10 is the conservative fallback that triggers coint_p010_penalty.
        """
        result = self._generator.generate_batch(
            df=df,
            funding_annual=funding_annual,
            regime_multiplier=regime_multiplier,
            coint_valid_series=coint_valid_series,
        )
        result["strategy_name"] = self.name

        if coint_pvalue_series is not None:
            pv = coint_pvalue_series.reset_index(drop=True)
            result["coint_pvalue"] = pv.reindex(result.index).fillna(0.10).to_numpy(dtype=float)
        else:
            # Patch 6: conservative fallback — triggers coint_p010_penalty in score()
            result["coint_pvalue"] = 0.10

        return result

    def score(self, context: MarketContext) -> float:
        """
        Fix #6: all numeric adjustments read from self._weights (KalmanScoringWeights)
        instead of inline magic numbers.
        """
        w = self._weights
        score = w.baseline

        if context.regime == "ranging":
            score += w.regime_ranging_bonus
        elif context.regime == "trending":
            score += w.regime_trending_penalty
        elif context.regime == "breakout":
            score += w.regime_breakout_penalty

        if context.coint_pvalue < 0.01:
            score += w.coint_p001_bonus
        elif context.coint_pvalue < 0.05:
            score += w.coint_p005_bonus
        elif context.coint_pvalue > 0.10:
            score += w.coint_p010_penalty

        hl = context.half_life_hours
        if hl is not None and not (hl != hl):  # guard against None/NaN
            if w.hl_optimal_low <= hl <= w.hl_optimal_high:
                score += w.hl_optimal_bonus
            elif hl > w.hl_long_threshold:
                score += w.hl_long_penalty
            elif hl < w.hl_short_threshold:
                score += w.hl_short_penalty

        if context.spread_autocorr < w.autocorr_good_threshold:
            score += w.autocorr_good_bonus
        elif context.spread_autocorr > w.autocorr_bad_threshold:
            score += w.autocorr_bad_penalty

        vr = context.vol_rank
        if w.vol_rank_good_low <= vr <= w.vol_rank_good_high:
            score += w.vol_rank_good_bonus
        elif vr > w.vol_rank_extreme:
            score += w.vol_rank_extreme_penalty

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
