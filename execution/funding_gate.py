"""
execution/funding_gate.py  —  FundingGate

Extracted from bybit_live_runner.py (Sprint 28 SRP refactor).
Decides whether funding rates permit a new trade entry.

Usage::

    gate = FundingGate(sym_y="BTCUSDT", sym_x="ETHUSDT", threshold=-0.01)
    if gate.is_open(ws_feed):
        ...proceed with trade...
"""
from __future__ import annotations

from loguru import logger


class FundingGate:
    """
    Funding-rate gate.

    Returns ``True`` (gate open, trading allowed) when funding for both
    legs is above ``threshold``.  Fails open on any exception so a
    temporary monitoring outage never silently blocks trading.
    """

    def __init__(
        self,
        sym_y: str,
        sym_x: str,
        threshold: float = -0.01,
    ) -> None:
        self._sym_y      = sym_y
        self._sym_x      = sym_x
        self._threshold  = threshold

    def is_open(self, ws_feed) -> bool:
        """
        Return True if trading is allowed (funding OK or check failed).

        Parameters
        ----------
        ws_feed:
            Active BybitWsFeed instance used to retrieve current funding.
        """
        try:
            from execution.funding_monitor import FundingMonitor
            fm = FundingMonitor(ws_feed)
            y_rate = fm.get_funding_rate(self._sym_y)
            x_rate = fm.get_funding_rate(self._sym_x)
            if y_rate is not None and x_rate is not None:
                blocked = y_rate < self._threshold or x_rate < self._threshold
                if blocked:
                    logger.debug(
                        "FundingGate CLOSED | {}={:.4f} {}={:.4f} threshold={:.4f}",
                        self._sym_y, y_rate, self._sym_x, x_rate, self._threshold,
                    )
                    return False
            return True
        except Exception as exc:
            logger.warning("FundingGate check failed: {} — fail open", exc)
            return True  # Fail open: prefer false-positive trade over missed trade
