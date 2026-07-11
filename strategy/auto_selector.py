"""
QuantLuna — AutoStrategySelector
Sprint 19

Intelligent strategy switcher: scoreste toate strategiile disponibile
per-bar si selecteaza automat cea mai potrivita pe baza contextului.

Strategii gestionate:
  1. KalmanPairsTrading        — flagship, default (baseline 0.60)
  2. BollingerBandsMeanReversion — ranging, vol medie
  3. ZScoreMomentum              — trending/breakout, autocorr pozitiva
  4. FundingRateArbitrage        — funding extrem (> 20%/an)

Logica de selectie per bar:
  1. Construieste MarketContext
  2. score(context) -> [0,1] per strategie
  3. Hysteresis: +hysteresis_bonus pentru strategia activa
  4. max score; daca sub min_score_threshold -> EXIT
  5. Switch: reset starea veche + switch_cooldown_bars

Fix #2: generate_batch() accepta coint_pvalue_series in loc de valoare
        hardcodata 0.05 — wired in MarketContext per bar.
Fix #3: switch cooldown nu mai forteaza EXIT prematur — continua cu
        strategia activa pe toata durata cooldown-ului.
Fix #7 (Gap #1): MarketContext.coint_pvalue populat cu float real din
        coint_pvalue_series (nu bool din coint_valid_series).
        generate_batch() transmite coint_pvalue_series si la strategia activa
        (KalmanPairsTrading.generate_batch) pentru wiring complet end-to-end.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from strategy.base import BaseStrategy, MarketContext, Signal, TradeSignal
from strategy.bb_mean_reversion import BollingerBandsMeanReversion
from strategy.funding_arb import FundingRateArbitrage
from strategy.zscore_momentum import ZScoreMomentum


class AutoStrategySelector:

    def __init__(
        self,
        strategies: Optional[List[BaseStrategy]] = None,
        hysteresis_bonus: float = 0.10,
        min_score_threshold: float = 0.30,
        win_rate_window: int = 20,
        autocorr_window: int = 30,
        switch_cooldown_bars: int = 5,
    ) -> None:
        self.strategies: List[BaseStrategy] = strategies or [
            BollingerBandsMeanReversion(window=20, n_std_entry=2.0),
            ZScoreMomentum(entry_threshold=1.5),
            FundingRateArbitrage(entry_funding_annual=0.20),
        ]
        self.hysteresis_bonus     = hysteresis_bonus
        self.min_score_threshold  = min_score_threshold
        self.win_rate_window      = win_rate_window
        self.autocorr_window      = autocorr_window
        self.switch_cooldown_bars = switch_cooldown_bars

        self._active_strategy: Optional[BaseStrategy] = None
        self._active_name: str = ""
        self._switch_cooldown_remaining: int = 0
        self._trade_outcomes: Deque[int] = deque(maxlen=win_rate_window)
        self._last_entry_zscore: float = 0.0
        self._in_trade: bool = False
        self._spread_buf: Deque[float] = deque(maxlen=autocorr_window)
        self._last_scores: Dict[str, float] = {}
        self._switch_history: List[Dict] = []
        self._total_bars: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_live(
        self,
        y: float,
        x: float,
        ts: Optional[pd.Timestamp] = None,
        funding_annual: float = 0.0,
        regime_multiplier: float = 1.0,
        coint_valid: bool = True,
        zscore: float = 0.0,
        half_life_hours: float = 24.0,
        vol_rank: float = 0.5,
        regime: str = "ranging",
        coint_pvalue: float = 0.05,
        spread: float = 0.0,
    ) -> TradeSignal:
        self._total_bars += 1
        if spread != 0.0:
            self._spread_buf.append(spread)

        context = MarketContext(
            zscore=zscore, half_life_hours=half_life_hours,
            vol_rank=vol_rank, regime=regime,
            funding_annual=funding_annual,
            coint_pvalue=float(coint_pvalue),  # Fix #7: always float
            spread_autocorr=self._compute_autocorr(),
            recent_win_rate=self._recent_win_rate(), is_warm=True,
        )

        selected, scores = self._select(context)
        self._last_scores = scores

        if selected is None:
            return TradeSignal(
                signal=Signal.EXIT, confidence=0.0,
                reason="no_strategy_above_threshold",
                strategy_name="AutoSelector", zscore=zscore, timestamp=ts,
                meta={"scores": scores, "context": context.to_dict()},
            )

        if self._active_strategy is not None and selected.name != self._active_name:
            self._on_switch(self._active_strategy, selected, ts)

        self._active_strategy = selected
        self._active_name     = selected.name

        use_z = selected.name in ("ZScoreMomentum", "FundingRateArbitrage")
        signal = selected.generate_live(
            y=zscore if use_z else y, x=x, ts=ts,
            funding_annual=funding_annual,
            regime_multiplier=regime_multiplier,
            coint_valid=coint_valid,
        )

        self._track_trade(signal, zscore)
        if self._switch_cooldown_remaining > 0:
            self._switch_cooldown_remaining -= 1

        signal.meta.update({
            "selector_scores": scores,
            "active_strategy": selected.name,
            "context":         context.to_dict(),
            "total_bars":      self._total_bars,
        })
        return signal

    def generate_batch(
        self,
        df: pd.DataFrame,
        funding_annual: Optional[pd.Series] = None,
        regime_multiplier: Optional[pd.Series] = None,
        coint_valid_series: Optional[pd.Series] = None,
        zscore_col: str = "zscore",
        spread_col: str = "spread",
        half_life_col: str = "half_life_hours",
        regime_col: Optional[str] = None,
        vol_rank_col: Optional[str] = None,
        coint_pvalue_series: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        """
        Fix #2: added coint_pvalue_series parameter — real per-bar float p-value.
        Fix #7 (Gap #1): MarketContext.coint_pvalue now receives float from
          coint_pvalue_series instead of bool from coint_valid_series.
          Also passes coint_pvalue_series down to KalmanPairsTrading.generate_batch()
          so the p-value is available in its result 'coint_pvalue' column.
        """
        df = df.copy()
        df["signal"] = int(Signal.EXIT)
        df["confidence"] = 0.0
        df["reason"] = ""
        df["strategy_name"] = ""
        df["active_strategy"] = ""
        self.reset()

        n = len(df)
        # Fix #7: real per-bar float coint p-value (not bool)
        coint_p_arr = (
            coint_pvalue_series.reset_index(drop=True).to_numpy(dtype=float)
            if coint_pvalue_series is not None
            else np.full(n, 0.05, dtype=float)
        )
        fund_arr  = funding_annual.to_numpy(dtype=float)  if funding_annual    is not None else np.zeros(n, dtype=float)
        reg_arr   = regime_multiplier.to_numpy(dtype=float) if regime_multiplier is not None else np.ones(n,  dtype=float)
        coint_arr = coint_valid_series.to_numpy(dtype=bool) if coint_valid_series is not None else np.ones(n,  dtype=bool)

        for i in range(n):
            row    = df.iloc[i]
            z      = float(row.get(zscore_col,    0.0))  if zscore_col    in df.columns else 0.0
            sp     = float(row.get(spread_col,    0.0))  if spread_col    in df.columns else 0.0
            hl     = float(row.get(half_life_col, 24.0)) if half_life_col in df.columns else 24.0
            fund   = float(fund_arr[i])
            reg    = float(reg_arr[i])
            reg_str = str(row.get(regime_col, "ranging")) if regime_col else "ranging"
            vr     = float(row.get(vol_rank_col, 0.5))   if vol_rank_col  else 0.5
            cok    = bool(coint_arr[i])
            cp     = float(coint_p_arr[i])  # Fix #7: real float per bar

            if sp != 0.0:
                self._spread_buf.append(sp)

            context = MarketContext(
                zscore=z, half_life_hours=hl, vol_rank=vr,
                regime=reg_str, funding_annual=fund,
                coint_pvalue=cp,          # Fix #7: float not bool
                spread_autocorr=self._compute_autocorr(),
                recent_win_rate=self._recent_win_rate(),
            )

            selected, scores = self._select(context)
            if selected is None:
                df.iat[i, df.columns.get_loc("reason")]          = "no_strategy"
                df.iat[i, df.columns.get_loc("active_strategy")] = "none"
                continue

            if self._active_strategy is not None and selected.name != self._active_name:
                self._on_switch(self._active_strategy, selected, None)

            self._active_strategy = selected
            self._active_name     = selected.name

            mini       = df.iloc[[i]].copy()
            pv_mini    = pd.Series([cp])  # Fix #7: pass single-bar p-value series
            cok_mini   = pd.Series([cok])

            # Fix #7: KalmanPairsTrading.generate_batch now accepts coint_pvalue_series
            import inspect
            sig_params = inspect.signature(selected.generate_batch).parameters
            if "coint_pvalue_series" in sig_params:
                out = selected.generate_batch(
                    mini,
                    pd.Series([fund]),
                    pd.Series([reg]),
                    cok_mini,
                    coint_pvalue_series=pv_mini,
                )
            else:
                out = selected.generate_batch(
                    mini, pd.Series([fund]), pd.Series([reg]), cok_mini
                )

            df.iat[i, df.columns.get_loc("signal")]          = int(out["signal"].iloc[0])
            df.iat[i, df.columns.get_loc("confidence")]      = float(out["confidence"].iloc[0])
            df.iat[i, df.columns.get_loc("reason")]          = str(out["reason"].iloc[0])
            df.iat[i, df.columns.get_loc("strategy_name")]   = selected.name
            df.iat[i, df.columns.get_loc("active_strategy")] = selected.name

            if self._switch_cooldown_remaining > 0:
                self._switch_cooldown_remaining -= 1

        return df

    def scores_summary(self) -> Dict:
        return {
            "active_strategy": self._active_name,
            "scores":          self._last_scores,
            "recent_win_rate": round(self._recent_win_rate(), 4),
            "switch_history":  self._switch_history[-10:],
            "total_bars":      self._total_bars,
        }

    def reset(self) -> None:
        for s in self.strategies: s.reset()
        self._active_strategy = None; self._active_name = ""
        self._switch_cooldown_remaining = 0
        self._spread_buf.clear(); self._trade_outcomes.clear()
        self._last_entry_zscore = 0.0; self._in_trade = False
        self._last_scores = {}; self._total_bars = 0

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _select(
        self, context: MarketContext
    ) -> Tuple[Optional[BaseStrategy], Dict[str, float]]:
        """
        Fix #3: during switch cooldown, always continue with the active strategy
        instead of forcing a premature EXIT.
        """
        if self._switch_cooldown_remaining > 0:
            scores = {s.name: s.score(context) for s in self.strategies}
            if self._active_strategy is not None:
                return self._active_strategy, scores
            return None, scores

        scores: Dict[str, float] = {}
        for s in self.strategies:
            base = s.score(context)
            if s.name == self._active_name:
                base = min(1.0, base + self.hysteresis_bonus)
            scores[s.name] = round(base, 4)

        best_name  = max(scores, key=lambda k: scores[k])
        best_score = scores[best_name]

        if best_score < self.min_score_threshold:
            logger.debug(f"AutoSelector: all below threshold {self.min_score_threshold}: {scores}")
            return None, scores

        return next(s for s in self.strategies if s.name == best_name), scores

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _on_switch(self, old: BaseStrategy, new: BaseStrategy, ts) -> None:
        logger.info(f"AutoSelector SWITCH: {old.name} -> {new.name} @ {ts}")
        old.reset()
        self._switch_cooldown_remaining = self.switch_cooldown_bars
        self._switch_history.append({
            "from": old.name, "to": new.name,
            "timestamp": str(ts) if ts else None,
            "scores": dict(self._last_scores),
        })

    def _compute_autocorr(self) -> float:
        buf = list(self._spread_buf)
        if len(buf) < 10: return 0.0
        arr = np.array(buf)
        if np.std(arr) < 1e-9: return 0.0
        try:
            return float(np.clip(np.corrcoef(arr[:-1], arr[1:])[0, 1], -1.0, 1.0))
        except Exception:
            return 0.0

    def _recent_win_rate(self) -> float:
        if not self._trade_outcomes: return 0.5
        return float(np.mean(list(self._trade_outcomes)))

    def _track_trade(self, signal: TradeSignal, current_zscore: float) -> None:
        sig = signal.signal
        if sig in (Signal.LONG_SPREAD, Signal.SHORT_SPREAD) and not self._in_trade:
            self._in_trade = True; self._last_entry_zscore = current_zscore
        elif sig == Signal.EXIT and self._in_trade:
            self._in_trade = False
            ez = self._last_entry_zscore
            win = 1 if (ez < 0 and current_zscore > ez) or (ez >= 0 and current_zscore < ez) else 0
            self._trade_outcomes.append(win)
