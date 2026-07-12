"""
state_bus.py  —  Compatibility shim (Sprint 13)

CANONICAL LOCATION: core/state_bus.py

This file is kept ONLY for backward compatibility with legacy imports:
    from state_bus import StateBus, bus

New code MUST import from core.state_bus directly:
    from core.state_bus import StateBus, bus

This shim will be REMOVED in Sprint 30.
Do NOT add new code here.
"""
from __future__ import annotations

import warnings

warnings.warn(
    "[QuantLuna] Importing from root 'state_bus' is deprecated and will be "
    "removed in Sprint 30. Use 'from core.state_bus import StateBus, bus' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from core.state_bus import StateBus, bus  # noqa: F401, E402
