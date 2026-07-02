"""
QuantLuna — AutoSelectorRunner
Sprint 20 + Sprint 23 (TrendRegimeDetector integration)

Backtest runner powered by AutoStrategySelector.
Sprint 23: regime column auto-populated from TrendRegimeDetector
           instead of hardcoded 'ranging'.

Usage:
    from backtest.auto_selector_runner import AutoSelectorRunner
    runner = AutoSelectorRunner(cfg)
    result = runner.run(y=prices_y, x=prices_x)
    # result["active_strategy_distribution"] -> {name: n_bars}
    # result["regime_distribution"]           -> {"ranging": 1200, ...}
    # result["switch_history"]                -> list

    from api.strategy import register_selector
    register_selector(job_id, runner.selector)
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from core.regime_detector import TrendRegimeDetector
from strategy.auto_selector import AutoStrategySelector
from strategy.bb_mean_reversion import BollingerBandsMeanReversion
from strategy.funding_arb import FundingRateArbitrage
from strategy.zscore_momentum import ZScoreMomentum

logger = logging.getLogger(__name__)


class AutoSelectorRunner:

    def __init__(
        self,
        cfg,
        extra_strategies: Optional[List] = None,
        hysteresis_bonus: float = 0.10,
        min_score: float = 0.30,
        switch_cooldown: int = 5,
        regime_window: int = 24,
    ) -> None:
        self.cfg = cfg
        self.regime_detector = TrendRegimeDetector(
            window=regime_window,
            adx_window=getattr(cfg, "adx_window", 14),
            min_persistence=getattr(cfg, "regime_min_persistence", 3),
        )
        base = [
            BollingerBandsMeanReversion(
                window=max(getattr(cfg, "zscore_window", 20), 5),
                n_std_entry=getattr(cfg, "zscore_entry", 2.0),
            ),
            ZScoreMomentum(entry_threshold=getattr(cfg, "zscore_entry", 1.5)),
            FundingRateArbitrage(entry_funding_annual=getattr(cfg, "funding_threshold_annual", 0.20)),
        ]
        self.selector = AutoStrategySelector(
            strategies=(extra_strategies or []) + base,
            hysteresis_bonus=hysteresis_bonus,
            min_score_threshold=min_score,
            switch_cooldown_bars=switch_cooldown,
        )

    def run(
        self,
        y: Optional[pd.Series] = None,
        x: Optional[pd.Series] = None,
        df: Optional[pd.DataFrame] = None,
        funding_annual: Optional[pd.Series] = None,
        regime_multiplier: Optional[pd.Series] = None,
        zscore_series: Optional[pd.Series] = None,
        half_life_series: Optional[pd.Series] = None,
        vol_rank_series: Optional[pd.Series] = None,
        regime_series: Optional[pd.Series] = None,
    ) -> Dict:
        if df is None:
            if y is not None and x is not None:
                df = pd.DataFrame({"close_y": y, "close_x": x}).dropna().reset_index(drop=True)
            else:
                raise ValueError("Provide y+x or df")
        df = df.copy()

        if "spread" not in df.columns and "close_y" in df.columns:
            df["spread"] = df["close_y"] - df["close_x"]

        if "zscore" not in df.columns:
            win = max(getattr(self.cfg, "zscore_window", 20), 5)
            mu = df["spread"].rolling(win).mean()
            sd = df["spread"].rolling(win).std()
            df["zscore"] = (df["spread"] - mu) / sd.replace(0, np.nan)

        if zscore_series is not None:             df["zscore"]          = zscore_series.values[:len(df)]
        if half_life_series is not None:          df["half_life_hours"] = half_life_series.values[:len(df)]
        elif "half_life_hours" not in df.columns: df["half_life_hours"] = 24.0
        if vol_rank_series is not None:           df["vol_rank"]        = vol_rank_series.values[:len(df)]
        elif "vol_rank" not in df.columns:        df["vol_rank"]        = 0.5

        # Sprint 23: auto-detect regime if not provided externally
        if regime_series is not None:
            df["regime"] = regime_series.values[:len(df)]
        elif "regime" not in df.columns or (df["regime"] == "ranging").all():
            logger.info("AutoSelectorRunner: detecting regime from spread via TrendRegimeDetector")
            df["regime"] = self.regime_detector.classify_series(df["spread"]).values

        result_df = self.selector.generate_batch(
            df=df,
            funding_annual=funding_annual,
            regime_multiplier=regime_multiplier,
            zscore_col="zscore",
            spread_col="spread",
            half_life_col="half_life_hours",
            regime_col="regime",
            vol_rank_col="vol_rank",
        )
        return self._build_result(result_df)

    def _build_result(self, df: pd.DataFrame) -> Dict:
        summary = self.selector.scores_summary()
        dist: Dict[str, int] = {}
        if "active_strategy" in df.columns:
            dist = {str(n): int(c) for n, c in df["active_strategy"].value_counts().items()}
        regime_dist: Dict[str, int] = {}
        if "regime" in df.columns:
            regime_dist = {str(r): int(c) for r, c in df["regime"].value_counts().items()}
        return {
            "n_trades":   int((df["signal"].diff() != 0).sum()),
            "total_bars": len(df),
            "total_pnl":  round(self._simple_pnl(df), 6),
            "active_strategy_distribution": dist,
            "regime_distribution":           regime_dist,
            "switch_count":   len(summary["switch_history"]),
            "switch_history": summary["switch_history"],
            "scores_summary": summary,
            "result_df":      df,
        }

    @staticmethod
    def _simple_pnl(df: pd.DataFrame) -> float:
        if "spread" not in df.columns or "signal" not in df.columns:
            return 0.0
        return float(
            (df["signal"].shift(1).fillna(0).astype(float) * df["spread"].diff().fillna(0.0)).sum()
        )
