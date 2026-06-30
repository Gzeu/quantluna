"""
notifications/telegram_notifier.py  —  QuantLuna Telegram Notification System

Sprint 11 — funcționalități:
  - Trade entry / exit alerts cu PnL, sizing, z-score
  - HALT / HARD_STOP / PAIR_DD alerts critice
  - Daily PnL summary (scheduled sau on-demand)
  - Queue overflow și WebSocket DEAD alerts
  - Retry logic cu exponential backoff (3 încercări)
  - Markdown formatting profesionist
  - Rate limiting (max 1 mesaj/secundă per Telegram API limits)
  - Async-first, non-blocking — niciodată nu blochează trading loop
  - Fallback silent la erori de delivery (logging only)

Usage:
    from notifications import TelegramNotifier, NotifierConfig, AlertLevel

    notifier = TelegramNotifier(NotifierConfig(
        bot_token="YOUR_BOT_TOKEN",
        chat_id="YOUR_CHAT_ID",
    ))

    # În LiveTrader sau orice modul:
    await notifier.send_trade_entry(
        pair="BTCUSDT/ETHUSDT",
        side_y="buy",
        zscore=-2.34,
        notional_usd=1200.0,
        hedge_ratio=0.0512,
        method="kelly",
    )
    await notifier.send_trade_exit(
        pair="BTCUSDT/ETHUSDT",
        pnl_usd=47.2,
        pnl_pct=0.039,
        trade_count=14,
        reason="signal",
    )
    await notifier.send_halt(reason="HARD_STOP", details="DD 15% atins", pair="BTCUSDT/ETHUSDT")
    await notifier.send_daily_summary(realized_pnl=124.5, trade_count=7, win_rate=0.71, max_dd=0.043)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_MIN_SEND_INTERVAL_S = 1.0  # Telegram: max ~1 msg/s per bot


class AlertLevel(Enum):
    INFO     = "ℹ️"
    SUCCESS  = "✅"
    WARNING  = "⚠️"
    CRITICAL = "🚨"


@dataclass
class NotifierConfig:
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = True
    timeout_s: float = 8.0
    max_retries: int = 3
    retry_base_delay_s: float = 1.0
    parse_mode: str = "Markdown"
    # Filtrare: dezactivează tipuri specifice de alerte
    notify_entries: bool = True
    notify_exits: bool = True
    notify_halts: bool = True
    notify_daily_summary: bool = True
    notify_watchdog: bool = True
    # Threshold: trimite exit alert doar dacă |pnl_usd| >= min_pnl_notify
    min_pnl_notify_usd: float = 0.0


class TelegramNotifier:
    """
    Async Telegram notifier pentru QuantLuna.

    Toate metodele sunt non-blocking și fail-safe:
    erorile de delivery sunt logate dar nu propagate,
    astfel încât trading loop-ul nu este niciodată afectat.
    """

    def __init__(self, config: NotifierConfig) -> None:
        self.cfg = config
        self._last_send_ts: float = 0.0
        self._send_lock = asyncio.Lock()
        self._url = _TELEGRAM_API.format(token=config.bot_token)

    @property
    def is_configured(self) -> bool:
        return bool(self.cfg.bot_token and self.cfg.chat_id and self.cfg.enabled)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_trade_entry(
        self,
        pair: str,
        side_y: str,
        zscore: float,
        notional_usd: float,
        hedge_ratio: float,
        method: str = "kelly",
        exchange: str = "",
    ) -> None:
        """Alert la deschiderea unei poziții."""
        if not self.cfg.notify_entries:
            return
        direction = "LONG" if side_y == "buy" else "SHORT"
        arrow = "🟢" if direction == "LONG" else "🔴"
        msg = (
            f"{arrow} *ENTRY — {pair}*\n"
            f"Direction: `{direction}`\n"
            f"Z-score: `{zscore:+.3f}`\n"
            f"Hedge ratio β: `{hedge_ratio:.4f}`\n"
            f"Notional: `${notional_usd:,.0f}`\n"
            f"Sizing method: `{method}`\n"
            + (f"Exchange: `{exchange}`\n" if exchange else "")
        )
        await self._send(msg, AlertLevel.SUCCESS)

    async def send_trade_exit(
        self,
        pair: str,
        pnl_usd: float,
        pnl_pct: float,
        trade_count: int,
        reason: str = "signal",
        fees_usd: float = 0.0,
    ) -> None:
        """Alert la închiderea unei poziții cu PnL."""
        if not self.cfg.notify_exits:
            return
        if abs(pnl_usd) < self.cfg.min_pnl_notify_usd:
            return
        emoji = "💚" if pnl_usd >= 0 else "❤️"
        sign = "+" if pnl_usd >= 0 else ""
        msg = (
            f"{emoji} *EXIT — {pair}*\n"
            f"PnL: `{sign}{pnl_usd:.2f} USDT` (`{sign}{pnl_pct:.2%}`)\n"
            + (f"Fees: `{fees_usd:.3f} USDT`\n" if fees_usd else "")
            + f"Reason: `{reason}`\n"
            f"Total trades: `{trade_count}`\n"
        )
        level = AlertLevel.SUCCESS if pnl_usd >= 0 else AlertLevel.WARNING
        await self._send(msg, level)

    async def send_halt(
        self,
        reason: str,
        details: str = "",
        pair: str = "",
    ) -> None:
        """Alert critic la HALT / HARD_STOP / PAIR_DD."""
        if not self.cfg.notify_halts:
            return
        msg = (
            f"🚨 *HALT — {reason}*\n"
            + (f"Pair: `{pair}`\n" if pair else "")
            + (f"Details: `{details}`\n" if details else "")
            + f"\n_Verificați pozițiile manual pe exchange!_"
        )
        await self._send(msg, AlertLevel.CRITICAL)

    async def send_daily_summary(
        self,
        realized_pnl: float,
        trade_count: int,
        win_rate: float,
        max_dd: float,
        open_pairs: int = 0,
        capital_usd: float = 0.0,
    ) -> None:
        """Sumar zilnic de performanță."""
        if not self.cfg.notify_daily_summary:
            return
        pnl_emoji = "📈" if realized_pnl >= 0 else "📉"
        sign = "+" if realized_pnl >= 0 else ""
        cap_line = f"Capital: `${capital_usd:,.0f}`\n" if capital_usd else ""
        msg = (
            f"{pnl_emoji} *QuantLuna — Daily Summary*\n"
            f"Realized PnL: `{sign}{realized_pnl:.2f} USDT`\n"
            f"Trades: `{trade_count}`\n"
            f"Win rate: `{win_rate:.1%}`\n"
            f"Max DD: `{max_dd:.2%}`\n"
            f"Open pairs: `{open_pairs}`\n"
            + cap_line
        )
        level = AlertLevel.SUCCESS if realized_pnl >= 0 else AlertLevel.WARNING
        await self._send(msg, level)

    async def send_watchdog_alert(
        self,
        state: str,
        last_tick_age_s: float,
        pair: str = "",
    ) -> None:
        """Alert WsWatchdog STALE sau DEAD."""
        if not self.cfg.notify_watchdog:
            return
        msg = (
            f"⚡ *WsWatchdog — {state}*\n"
            + (f"Pair: `{pair}`\n" if pair else "")
            + f"Last tick: `{last_tick_age_s:.1f}s` ago\n"
            f"_Reconnect în curs..._"
        )
        level = AlertLevel.CRITICAL if state == "DEAD" else AlertLevel.WARNING
        await self._send(msg, level)

    async def send_queue_overflow(
        self,
        drops: int,
        threshold: int,
        pair: str = "",
    ) -> None:
        """Alert queue overflow → HALT iminent."""
        msg = (
            f"🚨 *Queue Overflow HALT*\n"
            + (f"Pair: `{pair}`\n" if pair else "")
            + f"Drops consecutive: `{drops}/{threshold}`\n"
            f"_Trading HALTAT automat._"
        )
        await self._send(msg, AlertLevel.CRITICAL)

    async def send_custom(
        self,
        text: str,
        level: AlertLevel = AlertLevel.INFO,
    ) -> None:
        """Trimite un mesaj custom. Util pentru notificări ad-hoc."""
        await self._send(text, level)

    # ------------------------------------------------------------------
    # Internal delivery
    # ------------------------------------------------------------------

    async def _send(self, text: str, level: AlertLevel) -> None:
        """Delivery cu rate limiting + retry exponential backoff."""
        if not self.is_configured:
            return
        full_text = f"{level.value} {text}"
        async with self._send_lock:
            now = asyncio.get_event_loop().time()
            wait = _MIN_SEND_INTERVAL_S - (now - self._last_send_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            await self._deliver_with_retry(full_text)
            self._last_send_ts = asyncio.get_event_loop().time()

    async def _deliver_with_retry(self, text: str) -> None:
        delay = self.cfg.retry_base_delay_s
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.cfg.timeout_s)
                ) as session:
                    resp = await session.post(
                        self._url,
                        json={
                            "chat_id": self.cfg.chat_id,
                            "text": text,
                            "parse_mode": self.cfg.parse_mode,
                        },
                    )
                    if resp.status == 200:
                        return
                    body = await resp.text()
                    logger.warning(
                        f"Telegram delivery HTTP {resp.status} (attempt {attempt}/{self.cfg.max_retries}): {body[:120]}"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    f"Telegram delivery failed (attempt {attempt}/{self.cfg.max_retries}): {exc}"
                )
            if attempt < self.cfg.max_retries:
                await asyncio.sleep(delay)
                delay *= 2  # exponential backoff
        logger.error(f"Telegram: toate {self.cfg.max_retries} încercările au eșuat — mesaj pierdut (non-critical)")
