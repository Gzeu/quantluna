"""
QuantLuna — Portfolio Multi-Pair Allocator  (Sprint 10)

Orchestrator principal pentru allocation la nivel de portfolio:
  1. SpreadCorrelationMatrix  — verifică corelația candidat vs activi
  2. KellyCrossPair           — sizing optim cu ajustare cross-pair
  3. DrawdownController       — DD control pe 3 niveluri
  4. PortfolioRisk (Sprint 4) — circuit breaker existent (refolosit)

Flux de decizie pentru un pair candidat nou:
  1. DDController.can_open_new?  → HALT dacă SOFT_LIMIT sau HARD_STOP
  2. CorrelationMatrix.check_new_pair → REJECT dacă corr > threshold
  3. KellyCrossPair.compute → sizing ajustat corr + vol target
  4. PortfolioRisk.add_position → verificare exposure totală
  5. DDController.open_pair → înregistrare pentru tracking

Flux de update (per tick sau per bară):
  1. CorrelationMatrix.update(pair, spread_value)
  2. DDController.update(open_pnl_per_pair)
  3. Acționare forțare exit pentru pairs_force_close

Flux de exit:
  1. CorrelationMatrix.remove(pair_id)
  2. DDController.close_pair(pair_id)
  3. PortfolioRisk.remove_position(pair_id)

Limite reale:
  - Allocatorul nu generează semnale de intrare/ieșire.
    Acestea vin din SignalGenerator/SpreadEngine.
    Allocatorul decide CÂT și DACĂ, nu CÂND.
  - Kelly este estimat pe trade history. Pe pair nou fără
    historical trades, se folosește vol_target_only.
  - Correlation check este punctual (momentul intrării).
    Corelația poate crește după intrare. Monitoring-ul post-entry
    este responsabilitatea DDController + WsWatchdog.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .correlation_matrix import SpreadCorrelationMatrix, CorrelationMatrixConfig
from .kelly import KellyCrossPair, KellyConfig, KellyResult
from .drawdown_controller import DrawdownController, DDConfig, DDLevel, DDSnapshot
from .portfolio_risk import PortfolioRisk, PairExposure


@dataclass
class AllocatorConfig:
    # Kelly
    kelly: KellyConfig = field(default_factory=KellyConfig)
    # Correlation matrix
    correlation: CorrelationMatrixConfig = field(default_factory=CorrelationMatrixConfig)
    # DD control
    drawdown: DDConfig = field(default_factory=DDConfig)
    # Portfolio risk (refolosit din Sprint 4)
    max_total_exposure_pct: float = 0.60
    max_pair_corr: float = 0.70
    max_drawdown: float = 0.15
    capital_usd: float = 10_000.0
    max_concurrent_pairs: int = 5


@dataclass
class AllocationDecision:
    pair_id: str
    allowed: bool
    reject_reason: Optional[str]
    kelly_result: Optional[KellyResult]
    notional_usd: float
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        status = "ALLOWED" if self.allowed else f"REJECTED ({self.reject_reason})"
        notional = f"${self.notional_usd:.0f}" if self.allowed else "$0"
        kelly_str = self.kelly_result.summary() if self.kelly_result else "N/A"
        lines = [
            f"AllocationDecision [{self.pair_id}]: {status} | notional={notional}",
            f"  {kelly_str}",
        ]
        if self.notes:
            lines.append("  Notes: " + " | ".join(self.notes))
        return "\n".join(lines)


class PortfolioAllocator:
    """
    Orchestrator principal pentru multi-pair allocation.

    Utilizare minimă:
      cfg = AllocatorConfig(capital_usd=10_000)
      allocator = PortfolioAllocator(cfg)

      # La fiecare semnal de intrare:
      decision = allocator.request_entry(
          pair_id="ETH/BTC",
          candidate_spread=spread_series,
          trade_pnl_history=historical_pnl_series,
      )
      if decision.allowed:
          # trimite ordin cu decision.notional_usd

      # Per tick/bară, actualizare stare:
      snap = allocator.update_state(
          open_pnl_per_pair={"ETH/BTC": 42.5, "SOL/BTC": -18.2},
          spread_updates={"ETH/BTC": 0.0123, "SOL/BTC": -0.0045},
      )
      for pair_id in snap.pairs_force_close:
          # execuție exit forțat

      # La exit:
      allocator.record_exit("ETH/BTC")
    """

    def __init__(self, cfg: Optional[AllocatorConfig] = None) -> None:
        self.cfg = cfg or AllocatorConfig()
        self._corr = SpreadCorrelationMatrix(self.cfg.correlation)
        self._kelly = KellyCrossPair(self.cfg.kelly)
        self._dd = DrawdownController(self.cfg.drawdown)
        self._portfolio_risk = PortfolioRisk(
            max_total_exposure_pct=self.cfg.max_total_exposure_pct,
            max_pair_corr=self.cfg.max_pair_corr,
            max_drawdown=self.cfg.max_drawdown,
            capital_usdt=self.cfg.capital_usd,
        )
        self._active_pairs: Dict[str, float] = {}  # pair_id -> notional
        self._n_pairs: int = 0

    # ------------------------------------------------------------------
    # Entry request
    # ------------------------------------------------------------------

    def request_entry(
        self,
        pair_id: str,
        candidate_spread: pd.Series,
        trade_pnl_history: Optional[pd.Series] = None,
        current_zscore: float = 0.0,
        entry_beta: float = 1.0,
    ) -> AllocationDecision:
        """
        Evaluează dacă un nou pair poate fi deschis și la ce sizing.

        Parametri:
          pair_id             — identificator unic al perechii
          candidate_spread    — seria de spread values (pentru corr check + vol)
          trade_pnl_history   — P&L per trade trecut (fracție din capital)
                                None → vol_target_only sizing
          current_zscore      — z-score curent al spread-ului
          entry_beta          — hedge ratio curent din Kalman Filter
        """
        notes: List[str] = []
        cfg = self.cfg

        # Gate 1: DD level
        if not self._dd.can_open_new:
            return AllocationDecision(
                pair_id=pair_id,
                allowed=False,
                reject_reason=f"DD_LEVEL={self._dd.level.value} — nicio poziție nouă",
                kelly_result=None,
                notional_usd=0.0,
            )

        # Gate 2: max concurrent pairs
        if self._n_pairs >= cfg.max_concurrent_pairs:
            return AllocationDecision(
                pair_id=pair_id,
                allowed=False,
                reject_reason=f"MAX_PAIRS={cfg.max_concurrent_pairs} atinse",
                kelly_result=None,
                notional_usd=0.0,
            )

        # Gate 3: correlation check
        corr_allowed, max_corr, correlated_with = self._corr.check_new_pair(
            pair_id, candidate_spread
        )
        if not corr_allowed:
            return AllocationDecision(
                pair_id=pair_id,
                allowed=False,
                reject_reason=f"CORR_HIGH: {correlated_with}",
                kelly_result=None,
                notional_usd=0.0,
            )
        if max_corr > 0.5:
            notes.append(f"corr_elevated={max_corr:.3f} — discount aplicat")

        # Gate 4: Kelly sizing cu discount de corelație
        deployed_fraction = sum(self._active_pairs.values()) / cfg.capital_usd
        discount = self._corr.diversification_discount(pair_id, candidate_spread)

        pnl_series = trade_pnl_history if trade_pnl_history is not None else pd.Series(dtype=float)
        kelly_result = self._kelly.compute(
            pair_id=pair_id,
            trade_pnl_series=pnl_series,
            spread_series=candidate_spread,
            capital_usd=cfg.capital_usd,
            deployed_fraction=deployed_fraction,
            diversification_discount=discount,
        )
        notes.extend(kelly_result.notes)

        notional = kelly_result.final_notional_usd
        if notional < 10.0:
            return AllocationDecision(
                pair_id=pair_id,
                allowed=False,
                reject_reason=f"NOTIONAL_TOO_SMALL (${notional:.1f})",
                kelly_result=kelly_result,
                notional_usd=0.0,
                notes=notes,
            )

        # Gate 5: PortfolioRisk exposure check
        exposure = PairExposure(
            pair=pair_id,
            notional_usdt=notional,
            current_pnl=0.0,
            entry_zscore=current_zscore,
            current_zscore=current_zscore,
            beta=entry_beta,
        )
        risk_allowed = self._portfolio_risk.add_position(exposure)
        if not risk_allowed:
            return AllocationDecision(
                pair_id=pair_id,
                allowed=False,
                reject_reason="PORTFOLIO_RISK: exposure limit depășit",
                kelly_result=kelly_result,
                notional_usd=0.0,
                notes=notes,
            )

        # Toate gate-urile trecute
        self._dd.open_pair(pair_id)
        self._active_pairs[pair_id] = notional
        self._n_pairs += 1

        return AllocationDecision(
            pair_id=pair_id,
            allowed=True,
            reject_reason=None,
            kelly_result=kelly_result,
            notional_usd=notional,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Per-tick update
    # ------------------------------------------------------------------

    def update_state(
        self,
        open_pnl_per_pair: Dict[str, float],
        spread_updates: Dict[str, float],
    ) -> DDSnapshot:
        """
        Actualizează starea portfolio la fiecare tick sau bară.

        Parametri:
          open_pnl_per_pair — {pair_id: open_pnl_usd} pentru toate pozițiile active
          spread_updates    — {pair_id: spread_value_curent} pentru correlation matrix
        """
        # Update correlation matrix
        for pair_id, spread_val in spread_updates.items():
            self._corr.update(pair_id, spread_val)

        # Update PnL în PortfolioRisk
        for pair_id, pnl in open_pnl_per_pair.items():
            self._portfolio_risk.update_pnl(pair_id, pnl)

        # DD controller update
        snap = self._dd.update(open_pnl_per_pair)

        return snap

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------

    def record_exit(self, pair_id: str) -> None:
        """Înregistrează exitarea unui pair — curăță toate structurile interne."""
        self._corr.remove(pair_id)
        self._dd.close_pair(pair_id)
        self._portfolio_risk.remove_position(pair_id)
        self._active_pairs.pop(pair_id, None)
        self._n_pairs = max(0, self._n_pairs - 1)

    # ------------------------------------------------------------------
    # Manual controls
    # ------------------------------------------------------------------

    def manual_resume(self) -> bool:
        """Re-activare manuală după HARD_STOP. Necesită apel explicit."""
        return self._dd.manual_resume()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def is_trading_allowed(self) -> bool:
        return self._dd.is_trading_allowed and self._portfolio_risk.is_active

    @property
    def dd_level(self) -> DDLevel:
        return self._dd.level

    def portfolio_summary(self) -> dict:
        risk_summary = self._portfolio_risk.summary()
        corr_df = self._corr.get_correlation_matrix()
        return {
            **risk_summary,
            "dd_level": self._dd.level.value,
            "n_active_pairs": self._n_pairs,
            "active_pairs": list(self._active_pairs.keys()),
            "max_concurrent_pairs": self.cfg.max_concurrent_pairs,
            "correlation_matrix": corr_df.to_dict() if not corr_df.empty else {},
        }
