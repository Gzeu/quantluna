"""
execution/emergency_stop.py — hard emergency stop with atomic state and notifications.

Triggers an immediate halt of all trading, writes a sentinel file to disk
(persists across restarts), and fires a critical alert via NotifierBus.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SENTINEL_FILE = Path("state/EMERGENCY_STOP")


class EmergencyStop:
    """
    One-shot hard stop.

    Once triggered the stop is permanent until the sentinel file is manually
    deleted and the process is restarted.

    Usage::

        estop = EmergencyStop(notifier_bus=bus)

        # In trading loop:
        if estop.active:
            return

        # Trigger:
        await estop.trigger(reason="drawdown exceeded 20%")
    """

    def __init__(
        self,
        sentinel_path: Path = _SENTINEL_FILE,
        notifier_bus=None,
    ) -> None:
        self._path = sentinel_path
        self._bus = notifier_bus
        self._active: bool = self._path.exists()

    @property
    def active(self) -> bool:
        """True if emergency stop is in effect."""
        return self._active

    async def trigger(self, reason: str = "manual") -> None:
        """Trigger emergency stop. Idempotent."""
        if self._active:
            return

        self._active = True
        ts = datetime.now(timezone.utc).isoformat()

        # Write sentinel to disk so restarts stay halted
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(f"{ts}\nReason: {reason}\n", encoding="utf-8")
        except OSError as exc:
            logger.error("EmergencyStop: failed to write sentinel file: %s", exc)

        logger.critical("EMERGENCY STOP triggered: %s", reason)

        # Notify all channels
        if self._bus is not None:
            try:
                await self._bus.send_critical_alert(
                    title="🛑 EMERGENCY STOP",
                    message=f"Trading halted immediately.\nReason: {reason}\nTime: {ts}",
                )
            except Exception as exc:
                logger.error("EmergencyStop: notification failed: %s", exc)

    def clear(self) -> None:
        """Remove sentinel file and reset state (manual recovery only)."""
        if self._path.exists():
            self._path.unlink()
        self._active = False
        logger.warning("EmergencyStop cleared — trading can resume after restart")

    def status(self) -> dict:
        """Return current stop status."""
        reason: Optional[str] = None
        if self._path.exists():
            try:
                reason = self._path.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        return {"active": self._active, "sentinel_file": str(self._path), "reason": reason}
