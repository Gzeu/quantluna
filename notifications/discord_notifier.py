"""
QuantLuna — Discord Notifier
Sprint 29

Trimite alerte via Discord Webhook cu rich embeds.

Config via env:
  DISCORD_WEBHOOK_URL   URL webhook Discord
  DISCORD_ENABLED       true | false (default: true)

Features:
  - Rich embeds cu culoare per severitate (verde/galben/rosu)
  - Fields layout: fiecare camp payload = un field
  - Footer: timestamp + source
  - Retry 3x cu backoff
  - Rate limit: 30 req/min per webhook
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List

import httpx

from notifications.event_types import AlertEvent, EventType

logger = logging.getLogger(__name__)

_RETRY_COUNT = 3
_RETRY_DELAY = 2.0


class DiscordNotifier:
    """
    Notifier pentru Discord Webhook.
    Folosit intern de AlertDispatcher.
    """

    def __init__(
        self,
        webhook_url: str  = "",
        enabled:     bool = True,
    ) -> None:
        self.webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL", "")
        self.enabled     = enabled and os.getenv("DISCORD_ENABLED", "true").lower() == "true"
        self._client     = httpx.AsyncClient(timeout=10.0)

    async def send(self, event: AlertEvent) -> bool:
        if not self.enabled or not self.webhook_url:
            logger.debug("Discord disabled sau neconfigurat, skip.")
            return False

        payload = self._build_payload(event)
        for attempt in range(_RETRY_COUNT):
            try:
                resp = await self._client.post(self.webhook_url, json=payload)
                if resp.status_code in (200, 204):
                    return True
                if resp.status_code == 429:  # rate limit
                    retry_after = float(resp.json().get("retry_after", _RETRY_DELAY))
                    logger.warning(f"Discord rate limit, astept {retry_after}s")
                    await asyncio.sleep(retry_after)
                    continue
                logger.warning(f"Discord send failed {resp.status_code}: {resp.text[:200]}")
                return False
            except Exception as e:
                logger.error(f"Discord attempt {attempt+1} error: {e}")
                if attempt < _RETRY_COUNT - 1:
                    await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
        return False

    @staticmethod
    def _build_payload(event: AlertEvent) -> dict:
        fields = DiscordNotifier._build_fields(event)
        ts_iso = event.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")

        embed = {
            "title":       event.title,
            "color":       event.color,
            "fields":      fields,
            "footer":      {"text": f"QuantLuna • {event.source}"},
            "timestamp":   ts_iso,
        }
        return {"embeds": [embed]}

    @staticmethod
    def _build_fields(event: AlertEvent) -> List[dict]:
        p = event.payload

        def field(name: str, value: str, inline: bool = True) -> dict:
            return {"name": name, "value": str(value)[:1024], "inline": inline}

        if event.event_type == EventType.TRADE_OPEN:
            return [
                field("Pereche",   p.get("pair", "N/A")),
                field("Notional",  f"{p.get('notional_usdt', 0):.2f} USDT"),
                field("Side Y/X",  f"{p.get('side_y','')} / {p.get('side_x','')}"),
                field("Leverage",  f"{p.get('leverage', 1)}x"),
                field("Z-score",   f"{p.get('zscore', 0):.3f}"),
            ]
        elif event.event_type == EventType.TRADE_CLOSE:
            pnl = p.get("pnl_usdt", 0)
            return [
                field("Pereche",  p.get("pair", "N/A")),
                field("PnL",      f"{pnl:+.2f} USDT"),
                field("Durata",   f"{p.get('duration_h', 0):.1f}h"),
                field("Exit reason", p.get("reason", "signal")),
            ]
        elif event.event_type == EventType.DD_ALERT:
            return [
                field("Drawdown",  f"{p.get('current_dd', 0):.1%}"),
                field("Max DD",    f"{p.get('max_dd', 0):.1%}"),
                field("Equity",    f"{p.get('equity_usdt', 0):.2f} USDT"),
            ]
        elif event.event_type == EventType.SHARPE_DROP:
            return [
                field("Sharpe rolling", f"{p.get('sharpe', 0):.4f}"),
                field("Threshold",      f"{p.get('threshold', 0):.2f}"),
            ]
        elif event.event_type == EventType.HALT_CASCADE:
            return [
                field("Perechi oprite", str(p.get("n_pairs_halted", 0))),
                field("Motiv",          p.get("reason", "manual"), inline=False),
            ]
        elif event.event_type in (EventType.PAIR_START, EventType.PAIR_STOP):
            return [
                field("Pereche",  p.get("pair", "N/A")),
                field("Capital",  f"{p.get('alloc_usd', 0):.0f} USDT"),
            ]
        else:
            return [field(k, str(v)) for k, v in list(p.items())[:10]]
