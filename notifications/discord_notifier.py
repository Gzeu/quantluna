"""
QuantLuna — DiscordNotifier
Sprint 26

Discord webhook notifier cu embed-uri color-coded.
API identic cu TelegramNotifier — ambele sunt orchestrate de NotifierBus.

Features:
  - Embed-uri Discord (title, color, fields, footer)
  - Color coding: verde (profit/entry), roşu (loss/halt), galben (warning)
  - Rate limiting: 5 req / 2s (Discord webhook limit)
  - Retry exponential backoff (3 încercări)
  - Non-blocking: erori de delivery logate, nu propagate
  - Async-first cu aiohttp

Env vars:
  DISCORD_WEBHOOK_URL   URL-ul webhook-ului Discord
  DISCORD_ENABLED       true | false (default: true)

Usage:
    from notifications.discord_notifier import DiscordNotifier, DiscordConfig
    notifier = DiscordNotifier(DiscordConfig(webhook_url="https://discord.com/api/webhooks/..."))
    await notifier.send_trade_entry(pair="BTC/ETH", side_y="buy", zscore=-2.3,
                                    notional_usd=100.0, hedge_ratio=0.05)
    await notifier.send_stop_loss(pair="BTC/ETH", loss_usd=-4.2, loss_pct=-0.042,
                                  trigger_price=29800.0)
    await notifier.send_regime_change(pair="BTC/ETH", old_regime="ranging",
                                      new_regime="trending", active_strategy="ZScoreMomentum")
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DISCORD_RATE_LIMIT  = 5        # max requests per window
_DISCORD_RATE_WINDOW = 2.0      # seconds
_MIN_INTERVAL        = _DISCORD_RATE_WINDOW / _DISCORD_RATE_LIMIT

# Embed colours (decimal)
_COL_GREEN  = 0x2ECC71
_COL_RED    = 0xE74C3C
_COL_YELLOW = 0xF1C40F
_COL_BLUE   = 0x3498DB
_COL_GREY   = 0x95A5A6


@dataclass
class DiscordConfig:
    webhook_url:          str  = field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_URL", ""))
    enabled:              bool = field(default_factory=lambda: os.getenv("DISCORD_ENABLED", "true").lower() == "true")
    timeout_s:            float = 8.0
    max_retries:          int   = 3
    retry_base_delay_s:   float = 1.0
    username:             str   = "QuantLuna"
    avatar_url:           str   = ""
    notify_entries:       bool  = True
    notify_exits:         bool  = True
    notify_halts:         bool  = True
    notify_stop_loss:     bool  = True
    notify_regime_change: bool  = True
    notify_daily_summary: bool  = True
    notify_optimizer:     bool  = True
    min_pnl_notify_usd:   float = 0.0


class DiscordNotifier:
    """
    Discord webhook notifier pentru QuantLuna.
    Thread/async-safe, fail-safe (erorile nu afectează trading loop-ul).
    """

    def __init__(self, config: Optional[DiscordConfig] = None) -> None:
        self.cfg = config or DiscordConfig()
        self._lock        = asyncio.Lock()
        self._last_send_ts: float = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(self.cfg.webhook_url and self.cfg.enabled)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_trade_entry(
        self,
        pair:         str,
        side_y:       str,
        zscore:       float,
        notional_usd: float,
        hedge_ratio:  float,
        method:       str = "kelly",
        active_strategy: str = "",
        regime:       str = "",
    ) -> None:
        if not self.cfg.notify_entries:
            return
        direction = "LONG" if side_y.lower() == "buy" else "SHORT"
        colour    = _COL_GREEN if direction == "LONG" else _COL_RED
        fields    = [
            {"name": "Direction",       "value": f"`{direction}`",           "inline": True},
            {"name": "Z-score",         "value": f"`{zscore:+.3f}`",         "inline": True},
            {"name": "Notional",        "value": f"`${notional_usd:,.0f}`",  "inline": True},
            {"name": "Hedge ratio β",   "value": f"`{hedge_ratio:.4f}`",    "inline": True},
            {"name": "Sizing",          "value": f"`{method}`",              "inline": True},
        ]
        if active_strategy:
            fields.append({"name": "Strategy", "value": f"`{active_strategy}`", "inline": True})
        if regime:
            fields.append({"name": "Regime",   "value": f"`{regime}`",          "inline": True})
        await self._send_embed(
            title=f"{'\U0001f7e2' if direction == 'LONG' else '\U0001f534'} ENTRY — {pair}",
            colour=colour, fields=fields,
        )

    async def send_trade_exit(
        self,
        pair:        str,
        pnl_usd:     float,
        pnl_pct:     float,
        trade_count: int,
        reason:      str   = "signal",
        fees_usd:    float = 0.0,
    ) -> None:
        if not self.cfg.notify_exits:
            return
        if abs(pnl_usd) < self.cfg.min_pnl_notify_usd:
            return
        colour = _COL_GREEN if pnl_usd >= 0 else _COL_RED
        sign   = "+" if pnl_usd >= 0 else ""
        fields = [
            {"name": "PnL",    "value": f"`{sign}{pnl_usd:.2f} USDT ({sign}{pnl_pct:.2%})`", "inline": False},
            {"name": "Reason", "value": f"`{reason}`",    "inline": True},
            {"name": "Trades", "value": f"`{trade_count}`", "inline": True},
        ]
        if fees_usd:
            fields.append({"name": "Fees", "value": f"`{fees_usd:.3f} USDT`", "inline": True})
        await self._send_embed(
            title=f"{'\U0001f49a' if pnl_usd >= 0 else '\u2764\ufe0f'} EXIT — {pair}",
            colour=colour, fields=fields,
        )

    async def send_stop_loss(
        self,
        pair:          str,
        loss_usd:      float,
        loss_pct:      float,
        trigger_price: float = 0.0,
        regime:        str   = "",
    ) -> None:
        if not self.cfg.notify_stop_loss:
            return
        fields = [
            {"name": "Loss",  "value": f"`{loss_usd:.2f} USDT ({loss_pct:.2%})`", "inline": False},
        ]
        if trigger_price:
            fields.append({"name": "Trigger price", "value": f"`{trigger_price:.4f}`", "inline": True})
        if regime:
            fields.append({"name": "Regime", "value": f"`{regime}`", "inline": True})
        await self._send_embed(
            title=f"\U0001f6d1 STOP-LOSS — {pair}",
            colour=_COL_RED, fields=fields,
        )

    async def send_halt(
        self,
        reason:  str,
        details: str = "",
        pair:    str = "",
    ) -> None:
        if not self.cfg.notify_halts:
            return
        fields: List[Dict] = [{"name": "Reason", "value": f"`{reason}`", "inline": False}]
        if pair:    fields.append({"name": "Pair",    "value": f"`{pair}`",    "inline": True})
        if details: fields.append({"name": "Details", "value": f"`{details}`", "inline": False})
        await self._send_embed(
            title="\U0001f6a8 HALT — Trading Oprit",
            colour=_COL_RED, fields=fields,
            footer="Verificati pozitiile manual pe exchange!",
        )

    async def send_regime_change(
        self,
        pair:             str,
        old_regime:       str,
        new_regime:       str,
        active_strategy:  str = "",
        bars_in_regime:   int = 0,
    ) -> None:
        if not self.cfg.notify_regime_change:
            return
        fields = [
            {"name": "Regim anterior", "value": f"`{old_regime}`",  "inline": True},
            {"name": "Regim nou",      "value": f"`{new_regime}`",  "inline": True},
        ]
        if active_strategy:
            fields.append({"name": "Strategie activa", "value": f"`{active_strategy}`", "inline": True})
        if bars_in_regime:
            fields.append({"name": "Bare in regim", "value": f"`{bars_in_regime}`", "inline": True})
        await self._send_embed(
            title=f"\U0001f4ca Regime Change — {pair}",
            colour=_COL_BLUE, fields=fields,
        )

    async def send_daily_summary(
        self,
        realized_pnl: float,
        trade_count:  int,
        win_rate:     float,
        max_dd:       float,
        open_pairs:   int   = 0,
        capital_usd:  float = 0.0,
    ) -> None:
        if not self.cfg.notify_daily_summary:
            return
        colour = _COL_GREEN if realized_pnl >= 0 else _COL_RED
        sign   = "+" if realized_pnl >= 0 else ""
        fields = [
            {"name": "Realized PnL", "value": f"`{sign}{realized_pnl:.2f} USDT`", "inline": True},
            {"name": "Trades",       "value": f"`{trade_count}`",                  "inline": True},
            {"name": "Win rate",     "value": f"`{win_rate:.1%}`",                 "inline": True},
            {"name": "Max DD",       "value": f"`{max_dd:.2%}`",                   "inline": True},
            {"name": "Open pairs",   "value": f"`{open_pairs}`",                   "inline": True},
        ]
        if capital_usd:
            fields.append({"name": "Capital", "value": f"`${capital_usd:,.0f}`", "inline": True})
        await self._send_embed(
            title=f"{'\U0001f4c8' if realized_pnl >= 0 else '\U0001f4c9'} QuantLuna — Daily Summary",
            colour=colour, fields=fields,
        )

    async def send_optimizer_result(
        self,
        n_folds:       int,
        avg_sharpe:    float,
        best_params:   Dict[str, Any],
        regime_params: Dict[str, Dict[str, Any]],
    ) -> None:
        if not self.cfg.notify_optimizer:
            return
        param_str = "  ".join(f"`{k}={v}`" for k, v in best_params.items())
        regime_lines = "\n".join(f"**{r}**: " + " ".join(f"`{k}={v}`" for k, v in p.items())
                                  for r, p in regime_params.items())
        fields = [
            {"name": "Folds",        "value": f"`{n_folds}`",          "inline": True},
            {"name": "Avg Sharpe",   "value": f"`{avg_sharpe:.3f}`",   "inline": True},
            {"name": "Best params",  "value": param_str or "—",       "inline": False},
            {"name": "Per regim",    "value": regime_lines or "—",     "inline": False},
        ]
        await self._send_embed(
            title="\U0001f9ea WalkForward Optimizer — Result",
            colour=_COL_BLUE, fields=fields,
        )

    async def send_custom(
        self,
        text:   str,
        colour: int = _COL_GREY,
        title:  str = "QuantLuna",
    ) -> None:
        await self._send_embed(title=title, colour=colour, fields=[
            {"name": "Message", "value": text, "inline": False}
        ])

    # ------------------------------------------------------------------
    # Internal delivery
    # ------------------------------------------------------------------

    async def _send_embed(
        self,
        title:  str,
        colour: int,
        fields: List[Dict],
        footer: str = "QuantLuna Trading Engine",
    ) -> None:
        if not self.is_configured:
            return
        payload = {
            "username":   self.cfg.username,
            "embeds": [{
                "title":  title,
                "color":  colour,
                "fields": fields,
                "footer": {"text": footer},
            }],
        }
        if self.cfg.avatar_url:
            payload["avatar_url"] = self.cfg.avatar_url
        async with self._lock:
            now  = asyncio.get_event_loop().time()
            wait = _MIN_INTERVAL - (now - self._last_send_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            await self._deliver_with_retry(payload)
            self._last_send_ts = asyncio.get_event_loop().time()

    async def _deliver_with_retry(self, payload: Dict) -> None:
        import aiohttp
        delay = self.cfg.retry_base_delay_s
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.cfg.timeout_s)
                ) as session:
                    resp = await session.post(self.cfg.webhook_url, json=payload)
                    if resp.status in (200, 204):
                        return
                    body = await resp.text()
                    # 429 = rate limited — backoff longer
                    if resp.status == 429:
                        retry_after = 2.0
                        try:
                            import json
                            data = json.loads(body)
                            retry_after = float(data.get("retry_after", 2.0)) / 1000
                        except Exception:
                            pass
                        logger.warning(f"Discord rate limited, retry after {retry_after}s")
                        await asyncio.sleep(retry_after)
                        continue
                    logger.warning(f"Discord HTTP {resp.status} (attempt {attempt}): {body[:120]}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Discord delivery failed (attempt {attempt}/{self.cfg.max_retries}): {exc}")
            if attempt < self.cfg.max_retries:
                await asyncio.sleep(delay)
                delay *= 2
        logger.error(f"Discord: toate {self.cfg.max_retries} încercările au eșuat — mesaj pierdut (non-critical)")
