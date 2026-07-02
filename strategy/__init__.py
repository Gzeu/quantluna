"""
strategy package — public exports
"""
from strategy.regime_filter import RegimeFilter, GateResult

try:
    from strategy.multi_timeframe import MultiTimeframeConfirmation  # type: ignore[attr-defined]
except Exception:
    MultiTimeframeConfirmation = None  # type: ignore[assignment]

try:
    from strategy.signal_generator import SignalGenerator  # type: ignore[attr-defined]
except Exception:
    SignalGenerator = None  # type: ignore[assignment]

__all__ = [
    "RegimeFilter", "GateResult",
    "MultiTimeframeConfirmation",
    "SignalGenerator",
]
