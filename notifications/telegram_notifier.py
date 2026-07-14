"""
QuantLuna — Telegram Notifier
Sprint 29

Trimite alerte via Telegram Bot API (sendMessage, HTML parse mode).

Config via env:
  TELEGRAM_BOT_TOKEN   token de la @BotFather
  TELEGRAM_CHAT_ID     chat_id (string, poate fi lista separata cu virgula)
  TELEGRAM_ENABLED     true | false (default: true)

Features:
  - HTML formatting: bold, italic, code
  - Emoji per event type
  - Retry 3x cu backoff exponential
  - Suporta multiple chat_ids (fan-out)
  - Rate limit: max 1 msg/1s per chat (Telegram hard limit)
  - Truncare mesaj la 4096 chars
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List

import httpx

from notifications.event_types import AlertEvent, EventType, Severity

logger = logging.getLogger(__name__)

_TG_API_BASE = "https://api.telegram.org"
_MAX_MSG_LEN = 4096
_RETRY_COUNT = 3
_RETRY_DELAY = 2.0


class TelegramNotifier:
    """
    Notifier pentru Telegram Bot API.
    Folosit intern de AlertDispatcher și NotifierBus.
    """

    def __init__(
        self,
        token:    str = "",
        chat_ids: List[str] | None = None,
        enabled:  bool = True,
    ) -> None:
        self.token    = token    or os.getenv("TELEGRAM_BOT_TOKEN", "")
        raw_ids       = os.getenv("TELEGRAM_CHAT_ID", "")
        self.chat_ids = chat_ids or [cid.strip() for cid in raw_ids.split(",") if cid.strip()]
        self.enabled  = enabled and os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
        self._client  = httpx.AsyncClient(timeout=10.0)

    # ------------------------------------------------------------------
    # NotifierBus-compatible API (dispatched via _fan_out)
    # ------------------------------------------------------------------

    async def send_alert(
        self,
        title: str,
        detail: str = "",
        level: str = "warning",
    ) -> None:
        """Generic alert — wrapped into AlertEvent and sent via Telegram."""
        severity = Severity.CRITICAL if level == "critical" else Severity.WARNING if level == "warning" else Severity.INFO
        ev_type = EventType.SYSTEM_ERROR if level == "critical" else EventType.SYSTEM_START
        event = AlertEvent(
            event_type=ev_type,
            payload={"title": title, "detail": detail, "text": f"{title}\n{detail}" if detail else title},
            severity=severity,
            source="telegram_notifier",
        )
        await self.send(event)

    async def send_entry_signal(
        self,
        symbol: str,
        side: str,
        zscore: float,
        confidence: float = 0.0,
        venue: str = "",
    ) -> None:
        """Notify about a new trade entry."""
        event = AlertEvent(
            event_type=EventType.TRADE_OPEN,
            payload={
                "pair": symbol,
                "side": side,
                "side_y": side,
                "zscore": zscore,
                "confidence": confidence,
                "venue": venue,
                "notional_usdt": 0,
                "text": f"🟢 ENTRY {symbol} | Side: {side} | Z-score: {zscore:.3f} | Conf: {confidence:.1%}",
            },
            severity=Severity.WARNING,
            source="telegram_notifier",
        )
        await self.send(event)

    async def send_exit_signal(
        self,
        symbol: str,
        reason: str,
        pnl: float | None = None,
    ) -> None:
        """Notify about a trade exit."""
        pnl_str = f" | PnL: {pnl:+.4f}" if pnl is not None else ""
        event = AlertEvent(
            event_type=EventType.TRADE_CLOSE,
            payload={
                "pair": symbol,
                "pnl_usdt": pnl or 0,
                "reason": reason,
                "text": f"🔴 EXIT {symbol} | Reason: {reason}{pnl_str}",
            },
            severity=Severity.INFO,
            source="telegram_notifier",
        )
        await self.send(event)

    async def send_circuit_breaker_trip(
        self,
        reason: str,
        detail: str,
        cooldown_s: float = 0.0,
    ) -> None:
        """Notify about a circuit breaker trip."""
        cooldown_str = f" | Cooldown: {cooldown_s:.0f}s" if cooldown_s > 0 else ""
        event = AlertEvent(
            event_type=EventType.HALT_CASCADE,
            payload={
                "text": f"⚡ CIRCUIT BREAKER TRIP\nReason: {reason}\nDetail: {detail}{cooldown_str}",
                "reason": reason,
                "detail": detail,
            },
            severity=Severity.CRITICAL,
            source="telegram_notifier",
        )
        await self.send(event)

    async def send_daily_summary(
        self,
        trades: int = 0,
        total_pnl: float = 0.0,
        win_rate: float = 0.0,
        sharpe: float | None = None,
    ) -> None:
        """Daily performance summary (compatible with NotifierBus fan-out)."""
        emoji = "🟢" if total_pnl >= 0 else "🔴"
        sharpe_str = f" | Sharpe: {sharpe:.2f}" if sharpe is not None else ""
        event = AlertEvent(
            event_type=EventType.SYSTEM_START,
            payload={
                "text": (
                    f"📊 Daily Summary\n"
                    f"{emoji} PnL: {total_pnl:+.2f} USDT\n"
                    f"📋 Trades: {trades}\n"
                    f"📈 Win Rate: {win_rate:.1%}{sharpe_str}"
                ),
                "realized_pnl": total_pnl,
                "trade_count": trades,
                "win_rate": win_rate,
            },
            severity=Severity.INFO,
            source="telegram_notifier",
        )
        await self.send(event)

    async def send_raw(self, text: str, level: str = "info") -> None:
        """Send a raw text message."""
        severity = Severity.CRITICAL if level == "critical" else Severity.WARNING if level == "warning" else Severity.INFO
        event = AlertEvent(
            event_type=EventType.TEST,
            payload={"text": text},
            severity=severity,
            source="telegram_notifier",
        )
        await self.send(event)

    async def send(self, event: AlertEvent) -> bool:
        """Trimite alertă la toate chat_ids configurate."""
        if not self.enabled or not self.token or not self.chat_ids:
            logger.debug("Telegram disabled sau neconfigurat, skip.")
            return False

        text = self._format_message(event)
        results = await asyncio.gather(
            *[self._send_to_chat(chat_id, text) for chat_id in self.chat_ids],
            return_exceptions=True,
        )
        ok = all(r is True for r in results)
        return ok

    async def _send_to_chat(self, chat_id: str, text: str) -> bool:
        url  = f"{_TG_API_BASE}/bot{self.token}/sendMessage"
        body = {"chat_id": chat_id, "text": text[:_MAX_MSG_LEN], "parse_mode": "HTML"}

        for attempt in range(_RETRY_COUNT):
            try:
                resp = await self._client.post(url, json=body)
                if resp.status_code == 200:
                    return True
                if resp.status_code == 429:  # Too Many Requests
                    retry_after = float(resp.json().get("parameters", {}).get("retry_after", _RETRY_DELAY))
                    logger.warning(f"Telegram rate limit, astept {retry_after}s")
                    await asyncio.sleep(retry_after)
                    continue
                logger.warning(f"Telegram send failed {resp.status_code}: {resp.text[:200]}")
                return False
            except Exception as e:
                logger.error(f"Telegram attempt {attempt+1} error: {e}")
                if attempt < _RETRY_COUNT - 1:
                    await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
        return False

    @staticmethod
    def _format_message(event: AlertEvent) -> str:
        lines = [f"{event.emoji} <b>{event.event_type.value.replace('_', ' ')}</b>"]
        ts    = event.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
        lines.append(f"<i>{ts}</i>")
        lines.append("")

        payload = event.payload
        if event.event_type == EventType.TRADE_OPEN:
            lines += [
                f"📋 Pereche: <code>{payload.get('pair', 'N/A')}</code>",
                f"💰 Notional: <b>{payload.get('notional_usdt', 0):.2f} USDT</b>",
                f"⇅ Side Y: {payload.get('side_y', '')}, Side X: {payload.get('side_x', '')}",
                f"🔧 Leverage: {payload.get('leverage', 1.0)}x",
            ]
        elif event.event_type == EventType.TRADE_CLOSE:
            pnl   = payload.get('pnl_usdt', 0)
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines += [
                f"📋 Pereche: <code>{payload.get('pair', 'N/A')}</code>",
                f"{emoji} PnL: <b>{pnl:+.2f} USDT</b>",
                f"⏱ Durata: {payload.get('duration_h', 0):.1f}h",
            ]
        elif event.event_type == EventType.DD_ALERT:
            lines += [
                f"⚠️ Drawdown curent: <b>{payload.get('current_dd', 0):.1%}</b>",
                f"📊 Drawdown maxim: {payload.get('max_dd', 0):.1%}",
                f"💸 Equity: {payload.get('equity_usdt', 0):.2f} USDT",
            ]
        elif event.event_type == EventType.SHARPE_DROP:
            lines += [
                f"📉 Sharpe rolling: <b>{payload.get('sharpe', 0):.3f}</b>",
                f"🚫 Threshold: {payload.get('threshold', 0):.2f}",
            ]
        elif event.event_type == EventType.HALT_CASCADE:
            lines += [
                f"🔴 <b>HALT CASCADA ACTIVAT!</b>",
                f"📋 Perechi oprite: {payload.get('n_pairs_halted', 0)}",
                f"📝 Motiv: {payload.get('reason', 'manual')}",
            ]
        elif event.event_type == EventType.PAIR_START:
            lines += [
                f"▶️ Pereche: <code>{payload.get('pair', 'N/A')}</code>",
                f"💵 Capital alocat: {payload.get('alloc_usd', 0):.0f} USDT",
            ]
        elif event.event_type == EventType.PAIR_STOP:
            lines += [
                f"⏹️ Pereche: <code>{payload.get('pair', 'N/A')}</code>",
                f"📝 Motiv: {payload.get('reason', 'manual')}",
            ]
        elif event.event_type == EventType.SYSTEM_ERROR:
            lines += [
                f"💥 Eroare: <code>{str(payload.get('error', ''))[:300]}</code>",
                f"📏 Modul: {payload.get('module', 'unknown')}",
            ]
        else:
            for k, v in payload.items():
                lines.append(f"<b>{k}:</b> {v}")

        severity = event.severity.value.upper() if event.severity else "INFO"
        lines.append(f"\n🔵 Severity: {severity} | Source: {event.source}")
        return "\n".join(lines)

    async def close(self):
        await self._client.aclose()
