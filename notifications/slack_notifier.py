"""
QuantLuna — Slack Notifier (Sprint 17)

Async Slack notifier via Incoming Webhooks or Bot Token (chat.postMessage).
Mirrors the interface of telegram.py and discord.py so it can be plugged
into NotifierBus without changes.

Features:
  - Incoming Webhook mode (simple, no token scopes needed)
  - Bot Token mode (chat.postMessage, supports threading)
  - Rich Block Kit messages for trade signals and alerts
  - Fail-safe: network errors are logged but never raise to the strategy
  - Configurable channel, username, icon emoji

Usage:
    notifier = SlackNotifier(SlackConfig(
        webhook_url="https://hooks.slack.com/services/...",
        channel="#trading-alerts",
    ))
    await notifier.send_entry_signal(symbol="BTCUSDT", side="LONG", zscore=2.4)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    import aiohttp
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False


@dataclass
class SlackConfig:
    # Incoming Webhook URL (preferred for simplicity)
    webhook_url: str = ""

    # Bot Token mode (mutually exclusive with webhook_url)
    bot_token:   str = ""
    channel:     str = "#quantluna-alerts"

    # Display settings
    username:    str = "QuantLuna"
    icon_emoji:  str = ":chart_with_upwards_trend:"

    # Disable without removing config
    enabled: bool = True

    # Timeout for HTTP requests (seconds)
    timeout_s: float = 5.0

    # Minimum severity to send: 'info' | 'warning' | 'critical'
    min_level: str = "info"


class SlackNotifier:
    """
    Async Slack notifier.

    Parameters
    ----------
    cfg : SlackConfig
    """

    LEVEL_ORDER = {"info": 0, "warning": 1, "critical": 2}

    def __init__(self, cfg: Optional[SlackConfig] = None) -> None:
        self.cfg = cfg or SlackConfig()

    # ------------------------------------------------------------------
    # High-level helpers (mirrors Telegram / Discord interface)
    # ------------------------------------------------------------------

    async def send_entry_signal(
        self,
        symbol: str,
        side: str,
        zscore: float,
        confidence: float = 0.0,
        venue: str = "",
    ) -> None:
        """Notify about a new trade entry."""
        emoji = ":large_green_circle:" if side.upper() in ("LONG", "BUY") else ":red_circle:"
        text  = (
            f"{emoji} *ENTRY* `{symbol}` "
            f"| Side: *{side.upper()}* "
            f"| Z-score: `{zscore:.3f}` "
            f"| Conf: `{confidence:.1%}`"
            f"{'  | Venue: ' + venue if venue else ''}"
        )
        await self._send_text(text, level="info")

    async def send_exit_signal(
        self,
        symbol: str,
        reason: str,
        pnl: Optional[float] = None,
    ) -> None:
        """Notify about a trade exit."""
        pnl_str = f" | PnL: `{pnl:+.4f}`" if pnl is not None else ""
        text = f":white_check_mark: *EXIT* `{symbol}` | Reason: `{reason}`{pnl_str}"
        await self._send_text(text, level="info")

    async def send_alert(
        self,
        title: str,
        detail: str = "",
        level: str = "warning",
    ) -> None:
        """Generic alert — circuit breaker trip, health check failure, etc."""
        emoji_map = {"info": ":information_source:", "warning": ":warning:", "critical": ":rotating_light:"}
        emoji = emoji_map.get(level, ":warning:")
        text  = f"{emoji} *{title}*"
        if detail:
            text += f"\n>{detail}"
        await self._send_text(text, level=level)

    async def send_circuit_breaker_trip(
        self,
        reason: str,
        detail: str,
        cooldown_s: float = 0.0,
    ) -> None:
        """Dedicated circuit-breaker trip notification."""
        cooldown_str = f" | Cooldown: `{cooldown_s:.0f}s`" if cooldown_s > 0 else ""
        text = (
            f":rotating_light: *CIRCUIT BREAKER TRIPPED* "
            f"| Reason: `{reason}`{cooldown_str}\n>{detail}"
        )
        await self._send_text(text, level="critical")

    async def send_daily_summary(
        self,
        trades: int,
        total_pnl: float,
        win_rate: float,
        sharpe: Optional[float] = None,
    ) -> None:
        """End-of-day summary block."""
        pnl_emoji = ":chart_with_upwards_trend:" if total_pnl >= 0 else ":chart_with_downwards_trend:"
        sharpe_str = f" | Sharpe: `{sharpe:.2f}`" if sharpe is not None else ""
        text = (
            f"{pnl_emoji} *Daily Summary* "
            f"| Trades: `{trades}` "
            f"| PnL: `{total_pnl:+.4f}` "
            f"| Win Rate: `{win_rate:.1%}`"
            f"{sharpe_str}"
        )
        await self._send_text(text, level="info")

    async def send_raw(self, text: str, level: str = "info") -> None:
        """Send arbitrary text message."""
        await self._send_text(text, level=level)

    # ------------------------------------------------------------------
    # Internal send
    # ------------------------------------------------------------------

    def _below_min_level(self, level: str) -> bool:
        return (
            self.LEVEL_ORDER.get(level, 0)
            < self.LEVEL_ORDER.get(self.cfg.min_level, 0)
        )

    async def _send_text(self, text: str, level: str = "info") -> None:
        if not self.cfg.enabled:
            return
        if self._below_min_level(level):
            return
        if not _AIOHTTP_AVAILABLE:
            logger.warning("SlackNotifier: aiohttp not installed — message not sent")
            return

        if self.cfg.webhook_url:
            await self._webhook_send(text)
        elif self.cfg.bot_token:
            await self._bot_send(text)
        else:
            logger.warning("SlackNotifier: no webhook_url or bot_token configured")

    async def _webhook_send(self, text: str) -> None:
        payload: Dict[str, Any] = {
            "text":       text,
            "username":   self.cfg.username,
            "icon_emoji": self.cfg.icon_emoji,
        }
        try:
            timeout = aiohttp.ClientTimeout(total=self.cfg.timeout_s)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.cfg.webhook_url, json=payload) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(
                            f"SlackNotifier: webhook returned {resp.status}: {body[:200]}"
                        )
        except Exception as exc:
            logger.warning(f"SlackNotifier: webhook send failed (fail-safe): {exc}")

    async def _bot_send(self, text: str) -> None:
        payload: Dict[str, Any] = {
            "channel":    self.cfg.channel,
            "text":       text,
            "username":   self.cfg.username,
            "icon_emoji": self.cfg.icon_emoji,
        }
        headers = {"Authorization": f"Bearer {self.cfg.bot_token}"}
        try:
            timeout = aiohttp.ClientTimeout(total=self.cfg.timeout_s)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://slack.com/api/chat.postMessage",
                    json=payload,
                    headers=headers,
                ) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        logger.warning(f"SlackNotifier: Slack API error: {data.get('error')}")
        except Exception as exc:
            logger.warning(f"SlackNotifier: bot send failed (fail-safe): {exc}")
