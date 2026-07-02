"""
QuantLuna — Notifier Bus (Sprint 18)

Fan-out notification bus: sends every message to ALL registered notifiers.
Notifiers are registered by name and can be enabled/disabled at runtime.

Supported notifiers (all optional — only those configured are used):
  - Telegram  (notifications/telegram.py)
  - Slack     (notifications/slack_notifier.py)

Design:
  - fire_and_forget: errors in one notifier don't block others
  - Async-first: all send methods are coroutines
  - Runtime enable/disable per notifier name

Usage:
    bus = NotifierBus()
    bus.register("slack",    SlackNotifier(SlackConfig(webhook_url=...)))
    bus.register("telegram", TelegramNotifier(TelegramConfig(...)))

    await bus.send_entry_signal("BTCUSDT", "LONG", 2.4)
    await bus.send_alert("CircuitBreaker tripped", level="critical")
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from loguru import logger


class NotifierBus:
    """
    Fan-out bus: dispatches notification calls to all registered notifiers.

    Parameters
    ----------
    fail_silent : If True (default), errors in individual notifiers are logged
                  but never raised. Set False to let errors propagate (tests).
    """

    def __init__(self, fail_silent: bool = True) -> None:
        self._notifiers: Dict[str, Any] = {}
        self._enabled:   Dict[str, bool] = {}
        self.fail_silent = fail_silent

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, name: str, notifier: Any) -> None:
        """Register a notifier under a given name."""
        self._notifiers[name] = notifier
        self._enabled[name]   = True
        logger.info(f"NotifierBus: registered notifier '{name}'")

    def enable(self, name: str) -> None:
        """Enable a registered notifier."""
        if name in self._enabled:
            self._enabled[name] = True

    def disable(self, name: str) -> None:
        """Disable a registered notifier without removing it."""
        if name in self._enabled:
            self._enabled[name] = False
            logger.info(f"NotifierBus: disabled notifier '{name}'")

    @property
    def active_notifiers(self) -> list:
        return [n for n, en in self._enabled.items() if en]

    # ------------------------------------------------------------------
    # Fan-out helpers
    # ------------------------------------------------------------------

    async def _fan_out(self, method: str, *args, **kwargs) -> None:
        """Call method(*args, **kwargs) on all active notifiers concurrently."""
        tasks = []
        for name, notifier in self._notifiers.items():
            if not self._enabled.get(name, False):
                continue
            fn = getattr(notifier, method, None)
            if fn is None:
                continue
            tasks.append(self._safe_call(name, fn, *args, **kwargs))
        if tasks:
            await asyncio.gather(*tasks)

    async def _safe_call(self, name: str, fn, *args, **kwargs) -> None:
        try:
            await fn(*args, **kwargs)
        except Exception as exc:
            msg = f"NotifierBus: '{name}'.{fn.__name__} failed: {exc}"
            if self.fail_silent:
                logger.warning(msg)
            else:
                raise

    # ------------------------------------------------------------------
    # Notification API (mirrors individual notifier interface)
    # ------------------------------------------------------------------

    async def send_entry_signal(
        self,
        symbol: str,
        side: str,
        zscore: float,
        confidence: float = 0.0,
        venue: str = "",
    ) -> None:
        await self._fan_out(
            "send_entry_signal",
            symbol=symbol, side=side, zscore=zscore,
            confidence=confidence, venue=venue,
        )

    async def send_exit_signal(
        self,
        symbol: str,
        reason: str,
        pnl: Optional[float] = None,
    ) -> None:
        await self._fan_out("send_exit_signal", symbol=symbol, reason=reason, pnl=pnl)

    async def send_alert(
        self,
        title: str,
        detail: str = "",
        level: str = "warning",
    ) -> None:
        await self._fan_out("send_alert", title=title, detail=detail, level=level)

    async def send_circuit_breaker_trip(
        self,
        reason: str,
        detail: str,
        cooldown_s: float = 0.0,
    ) -> None:
        await self._fan_out(
            "send_circuit_breaker_trip",
            reason=reason, detail=detail, cooldown_s=cooldown_s,
        )

    async def send_daily_summary(
        self,
        trades: int,
        total_pnl: float,
        win_rate: float,
        sharpe: Optional[float] = None,
    ) -> None:
        await self._fan_out(
            "send_daily_summary",
            trades=trades, total_pnl=total_pnl,
            win_rate=win_rate, sharpe=sharpe,
        )

    async def send_raw(self, text: str, level: str = "info") -> None:
        await self._fan_out("send_raw", text=text, level=level)
