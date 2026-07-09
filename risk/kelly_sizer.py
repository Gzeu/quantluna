"""
risk/kelly_sizer.py  —  DEPRECATED compatibility shim.

CANONICAL LOCATION: risk.kelly

KellySizer and KellySimpleResult have been merged into risk/kelly.py.
All new code should import directly::

    from risk.kelly import KellySizer, KellySimpleResult

This file will be deleted after all internal imports have been migrated.
"""
import warnings
warnings.warn(
    "risk.kelly_sizer is deprecated. Import KellySizer from risk.kelly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from risk.kelly import KellySizer, KellySimpleResult as KellyResult  # noqa: F401, E402

__all__ = ["KellySizer", "KellyResult"]
