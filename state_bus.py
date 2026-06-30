"""
state_bus.py  —  Compatibility shim (Sprint 13)

CANONICAL LOCATION: core/state_bus.py

This file is kept for backward compatibility with imports like:
    from state_bus import StateBus, bus

New code should import from core.state_bus directly:
    from core.state_bus import StateBus, bus

This shim will be removed in a future sprint.
"""
from core.state_bus import StateBus, bus  # noqa: F401

import warnings
warnings.warn(
    "Importing from root state_bus is deprecated. "
    "Use 'from core.state_bus import StateBus, bus' instead.",
    DeprecationWarning,
    stacklevel=2,
)
