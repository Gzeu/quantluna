"""
QuantLuna — VolatilityRegime Adapter (Sprint 20)

Normalisează interfata VolatilityRegime pentru RegimeFilter.
RegimeFilter aşteaptă un obiect cu:
  - .size_multiplier  → float
  - .entry_allowed    → bool
  - .current_regime   → object cu .value (str)
  - update(spread_return) sau update(value) → None

Dacă VolatilityRegime intern are altă API, acest adapter face bridging.

Usage:
    from core.vol_regime_adapter import VolRegimeAdapter
    vr = VolRegimeAdapter()
    vr.update(spread_return=0.002)
    print(vr.size_multiplier, vr.entry_allowed)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from loguru import logger


class RegimeLabel(str, Enum):
    LOW     = "LOW"
    NORMAL  = "NORMAL"
    HIGH    = "HIGH"
    EXTREME = "EXTREME"


_SIZE_MAP = {
    RegimeLabel.LOW:     1.2,   # slightly larger in calm markets
    RegimeLabel.NORMAL:  1.0,
    RegimeLabel.HIGH:    0.6,
    RegimeLabel.EXTREME: 0.0,   # no entry
}

_ENTRY_MAP = {
    RegimeLabel.LOW:     True,
    RegimeLabel.NORMAL:  True,
    RegimeLabel.HIGH:    True,
    RegimeLabel.EXTREME: False,
}


try:
    from core.volatility_regime import VolatilityRegime as _VR
    _VR_AVAILABLE = True
except Exception:
    _VR_AVAILABLE = False


class VolRegimeAdapter:
    """
    Adapter: wraps VolatilityRegime and exposes the interface RegimeFilter expects.

    Falls back to an EWMA-based regime estimator if VolatilityRegime
    is not importable.

    Parameters
    ----------
    ewma_span     : span for EWMA volatility estimate (fallback)
    low_thresh    : annualised vol below which regime = LOW
    high_thresh   : annualised vol above which regime = HIGH
    extreme_thresh: annualised vol above which regime = EXTREME
    vr_kwargs     : keyword args forwarded to VolatilityRegime
    """

    def __init__(
        self,
        ewma_span:      int   = 20,
        low_thresh:     float = 0.01,
        high_thresh:    float = 0.03,
        extreme_thresh: float = 0.06,
        **vr_kwargs,
    ) -> None:
        self._ewma_span      = ewma_span
        self._low_thresh     = low_thresh
        self._high_thresh    = high_thresh
        self._extreme_thresh = extreme_thresh
        self._vr             = None

        # EWMA fallback state
        self._ewma_var:  float = 0.0
        self._alpha:     float = 2.0 / (ewma_span + 1)
        self._bar_count: int   = 0
        self._current_regime: RegimeLabel = RegimeLabel.NORMAL

        if _VR_AVAILABLE:
            try:
                self._vr = _VR(**vr_kwargs)
                logger.debug("VolRegimeAdapter: using VolatilityRegime")
            except Exception as exc:
                logger.warning(f"VolRegimeAdapter: VolatilityRegime init failed ({exc}), using EWMA fallback")

    # ------------------------------------------------------------------
    # Main interface
    # ------------------------------------------------------------------

    def update(self, spread_return: float) -> None:
        """Feed a new spread return (price change / price)."""
        self._bar_count += 1

        if self._vr is not None:
            try:
                if hasattr(self._vr, "update"):
                    self._vr.update(spread_return)
                elif hasattr(self._vr, "step"):
                    self._vr.step(spread_return)
                return
            except Exception as exc:
                logger.warning(f"VolRegimeAdapter: VR update error ({exc}), using EWMA")

        # EWMA fallback
        r2 = spread_return ** 2
        self._ewma_var = self._alpha * r2 + (1 - self._alpha) * self._ewma_var
        vol = self._ewma_var ** 0.5

        if vol >= self._extreme_thresh:
            self._current_regime = RegimeLabel.EXTREME
        elif vol >= self._high_thresh:
            self._current_regime = RegimeLabel.HIGH
        elif vol <= self._low_thresh:
            self._current_regime = RegimeLabel.LOW
        else:
            self._current_regime = RegimeLabel.NORMAL

    # ------------------------------------------------------------------
    # Properties (RegimeFilter interface)
    # ------------------------------------------------------------------

    @property
    def size_multiplier(self) -> float:
        if self._vr is not None:
            val = getattr(self._vr, "size_multiplier", None)
            if val is not None:
                return float(val)
        return _SIZE_MAP[self._current_regime]

    @property
    def entry_allowed(self) -> bool:
        if self._vr is not None:
            val = getattr(self._vr, "entry_allowed", None)
            if val is not None:
                return bool(val)
        return _ENTRY_MAP[self._current_regime]

    @property
    def current_regime(self) -> RegimeLabel:
        if self._vr is not None:
            regime = getattr(self._vr, "current_regime", None)
            if regime is not None:
                return regime
        return self._current_regime

    @property
    def ewma_vol(self) -> float:
        """Current EWMA volatility estimate (fallback path only)."""
        return self._ewma_var ** 0.5
