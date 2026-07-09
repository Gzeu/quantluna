"""
strategy/base.py  —  DEPRECATED compatibility shim.

CANONICAL LOCATION: strategy.base_strategy

PairsBaseStrategy (previously called BaseStrategy in this file) has been
merged into strategy/base_strategy.py. All new pairs strategies should
subclass PairsBaseStrategy directly::

    from strategy.base_strategy import PairsBaseStrategy, Signal, TradeSignal, MarketContext

    class MyPairsStrategy(PairsBaseStrategy):
        ...

This shim re-exports PairsBaseStrategy as BaseStrategy to preserve backward
compatibility. It will be deleted in a future sprint.
"""
import warnings
warnings.warn(
    "strategy.base is deprecated. "
    "Subclass PairsBaseStrategy from strategy.base_strategy instead.",
    DeprecationWarning,
    stacklevel=2,
)

from strategy.base_strategy import (  # noqa: F401, E402
    PairsBaseStrategy as BaseStrategy,
    Signal,
    TradeSignal,
    MarketContext,
)

__all__ = ["BaseStrategy", "Signal", "TradeSignal", "MarketContext"]
