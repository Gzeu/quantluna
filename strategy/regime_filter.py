"""
QuantLuna — Regime Filter (Sprint 18)

Gate layer between signal generation and order submission.
Combines outputs from:
  - MultiTimeframeConfirmation  (strategy/multi_timeframe.py)
  - VolatilityRegime            (core/volatility_regime.py)
  - CircuitBreaker              (risk/circuit_breaker.py)
  - SpreadMonitor               (core/spread_monitor.py)

All four must agree before an entry is allowed:
  1. MTF: LTF and HTF z-scores aligned
  2. Vol regime: not EXTREME, size_multiplier > 0
  3. Circuit breaker: is_open (not tripped)
  4. Spread monitor: last report healthy

Also adjusts position size via vol-regime size_multiplier.

Usage:
    rf = RegimeFilter(
        mtf=MultiTimeframeConfirmation(),
        vol_regime=VolatilityRegime(),
        circuit_breaker=CircuitBreaker(),
        spread_monitor=SpreadMonitor(),
    )
    gate = rf.check(ltf_zscore=2.1, htf_zscore=1.8, spread_report=report)
    if gate.allowed:
        qty = base_qty * gate.size_multiplier
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger


@dataclass
class GateResult:
    """Result of a single regime filter check."""
    allowed:          bool
    size_multiplier:  float          # 0.0 = blocked, >0 = scale qty by this
    blocked_by:       List[str] = field(default_factory=list)  # reasons for block
    vol_regime:       str = ""       # current regime label
    mtf_confirmed:    bool = False
    cb_open:          bool = True
    spread_healthy:   bool = True

    @property
    def summary(self) -> str:
        if self.allowed:
            return (
                f"ENTRY ALLOWED | size_mult={self.size_multiplier:.2f} "
                f"vol={self.vol_regime}"
            )
        return f"ENTRY BLOCKED by: {', '.join(self.blocked_by)}"


class RegimeFilter:
    """
    Unified regime gate for entry decisions.

    Parameters
    ----------
    mtf            : MultiTimeframeConfirmation instance (or None to skip)
    vol_regime     : VolatilityRegime instance (or None to skip)
    circuit_breaker: CircuitBreaker instance (or None to skip)
    spread_monitor : SpreadMonitor instance (or None to skip)
    require_all    : If False, only enabled components are checked
    """

    def __init__(
        self,
        mtf=None,
        vol_regime=None,
        circuit_breaker=None,
        spread_monitor=None,
        require_all: bool = True,
    ) -> None:
        self._mtf             = mtf
        self._vol_regime      = vol_regime
        self._circuit_breaker = circuit_breaker
        self._spread_monitor  = spread_monitor
        self.require_all      = require_all

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        ltf_zscore: float,
        htf_zscore: Optional[float] = None,
        spread_report=None,           # SpreadHealthReport | None
    ) -> GateResult:
        """
        Run all enabled filters and return a GateResult.

        Parameters
        ----------
        ltf_zscore   : lower-timeframe z-score (signal)
        htf_zscore   : higher-timeframe z-score (optional MTF confirmation)
        spread_report: SpreadHealthReport from SpreadMonitor.update() (optional)
        """
        blocked_by: List[str] = []
        size_multiplier: float = 1.0
        vol_regime_label: str = "NORMAL"
        mtf_confirmed: bool = True
        cb_open: bool = True
        spread_healthy: bool = True

        # 1. Circuit Breaker
        if self._circuit_breaker is not None:
            cb_open = self._circuit_breaker.is_open
            if not cb_open:
                blocked_by.append("circuit_breaker")

        # 2. Volatility Regime
        if self._vol_regime is not None:
            size_multiplier = self._vol_regime.size_multiplier
            vol_regime_label = self._vol_regime.current_regime.value \
                if hasattr(self._vol_regime.current_regime, "value") \
                else str(self._vol_regime.current_regime)
            if not self._vol_regime.entry_allowed:
                blocked_by.append(f"vol_regime={vol_regime_label}")
                size_multiplier = 0.0

        # 3. Multi-Timeframe Confirmation
        if self._mtf is not None and htf_zscore is not None:
            mtf_confirmed = self._mtf.confirm(ltf_zscore, htf_zscore)
            if not mtf_confirmed:
                blocked_by.append("mtf_misaligned")

        # 4. Spread Health
        if spread_report is not None:
            spread_healthy = spread_report.healthy
            if not spread_healthy:
                alert_types = ", ".join(
                    a.alert_type.value for a in getattr(spread_report, "alerts", [])
                )
                blocked_by.append(f"spread_unhealthy[{alert_types}]")

        allowed = len(blocked_by) == 0 and size_multiplier > 0

        result = GateResult(
            allowed=allowed,
            size_multiplier=size_multiplier if allowed else 0.0,
            blocked_by=blocked_by,
            vol_regime=vol_regime_label,
            mtf_confirmed=mtf_confirmed,
            cb_open=cb_open,
            spread_healthy=spread_healthy,
        )

        if not allowed:
            logger.info(f"RegimeFilter: {result.summary}")
        else:
            logger.debug(f"RegimeFilter: {result.summary}")

        return result

    def update_vol_regime(self, spread_return: float) -> None:
        """Convenience: forward a spread return to the vol regime updater."""
        if self._vol_regime is not None:
            self._vol_regime.update(spread_return)

    def record_trade(self, pnl: float) -> None:
        """Convenience: forward trade PnL to the circuit breaker."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_trade(pnl)

    def record_order_result(self, success: bool) -> None:
        """Convenience: forward order result to the circuit breaker."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_order_result(success)

    @property
    def entry_allowed(self) -> bool:
        """Quick check — True if circuit breaker is open and vol regime allows entry."""
        cb_ok  = (self._circuit_breaker is None) or self._circuit_breaker.is_open
        vol_ok = (self._vol_regime is None) or self._vol_regime.entry_allowed
        return cb_ok and vol_ok
