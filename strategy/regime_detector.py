"""
strategy/regime_detector.py  —  DEPRECATED compatibility shim.

CANONICAL LOCATION: core.regime_detector

Regime detection logic lives in core/regime_detector.py.
strategy/regime_filter.py is the correct strategy-layer gating module.

This file is dead code in the current codebase (strategy/__init__.py
imports from regime_filter, not regime_detector). It is kept temporarily
as a re-export shim to prevent ImportError if any external script or
notebook directly imported from this path.

All code should use::

    from core.regime_detector import RegimeDetector, VolRegime, RegimeState
    from strategy.regime_filter import RegimeFilter, GateResult

This file will be deleted in a future sprint.
"""
import warnings
warnings.warn(
    "strategy.regime_detector is deprecated. "
    "Use core.regime_detector for detection logic and "
    "strategy.regime_filter for strategy-layer gating.",
    DeprecationWarning,
    stacklevel=2,
)

from core.regime_detector import (  # noqa: F401, E402
    RegimeDetector,
    VolRegime,
    RegimeState,
    _REGIME_MULTIPLIER,
)

__all__ = ["RegimeDetector", "VolRegime", "RegimeState", "_REGIME_MULTIPLIER"]
