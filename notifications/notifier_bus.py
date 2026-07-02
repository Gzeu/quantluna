"""
QuantLuna — NotifierBus
Sprint 26

Fan-out notifier: trimite simultan pe Telegram + Discord.
Single call din LiveTrader / Optimizer — zero duplicare de cod.

Usage:
    from notifications.notifier_bus import NotifierBus, build_bus_from_env

    # Construire automata din env vars:
    bus = build_bus_from_env()

    # In LiveTrader (async context):
    await bus.trade_entry(pair="BTC/ETH", side_y="buy", zscore=-2.3,
                          notional_usd=100.0, hedge_ratio=0.05,
                          active_strategy="BollingerBands", regime="ranging")
    await bus.stop_loss(pair="BTC/ETH", loss_usd=-3.5, loss_pct=-0.035,
                        trigger_price=29800.0, regime="breakout")
    await bus.regime_change(pair="BTC/ETH", old_regime="ranging",
                            new_regime="trending", active_strategy="ZScoreMomentum")
    await bus.optimizer_result(n_folds=5, avg_sharpe=1.23,
                               best_params={...}, regime_params={...})

Env vars required:
  Telegram: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  Discord:  DISCORD_WEBHOOK_URL
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from notifications.discord_notifier import DiscordConfig, DiscordNotifier
from notifications.telegram_notifier import NotifierConfig, TelegramNotifier

logger = logging.getLogger(__name__)


class NotifierBus:
    """
    Fan-out to Telegram + Discord simultaneously.
    Each channel is optional — unconfigured channels are silently skipped.
    All methods are async and non-blocking (errors are caught internally).
    """

    def __init__(
        self,
        telegram: Optional[TelegramNotifier] = None,
        discord:  Optional[DiscordNotifier]  = None,
    ) -> None:
        self._telegram = telegram
        self._discord  = discord

    async def _fan_out(self, *coros) -> None:
        """Run all coroutines concurrently, suppress individual errors."""
        results = await asyncio.gather(*coros, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"NotifierBus delivery error: {r}")

    # ------------------------------------------------------------------
    # Trade events
    # ------------------------------------------------------------------

    async def trade_entry(
        self,
        pair:             str,
        side_y:           str,
        zscore:           float,
        notional_usd:     float,
        hedge_ratio:      float,
        method:           str = "kelly",
        active_strategy:  str = "",
        regime:           str = "",
    ) -> None:
        coros = []
        if self._telegram:
            coros.append(self._telegram.send_trade_entry(
                pair=pair, side_y=side_y, zscore=zscore,
                notional_usd=notional_usd, hedge_ratio=hedge_ratio, method=method,
            ))
        if self._discord:
            coros.append(self._discord.send_trade_entry(
                pair=pair, side_y=side_y, zscore=zscore,
                notional_usd=notional_usd, hedge_ratio=hedge_ratio, method=method,
                active_strategy=active_strategy, regime=regime,
            ))
        if coros:
            await self._fan_out(*coros)

    async def trade_exit(
        self,
        pair:        str,
        pnl_usd:     float,
        pnl_pct:     float,
        trade_count: int,
        reason:      str   = "signal",
        fees_usd:    float = 0.0,
    ) -> None:
        coros = []
        if self._telegram:
            coros.append(self._telegram.send_trade_exit(
                pair=pair, pnl_usd=pnl_usd, pnl_pct=pnl_pct,
                trade_count=trade_count, reason=reason, fees_usd=fees_usd,
            ))
        if self._discord:
            coros.append(self._discord.send_trade_exit(
                pair=pair, pnl_usd=pnl_usd, pnl_pct=pnl_pct,
                trade_count=trade_count, reason=reason, fees_usd=fees_usd,
            ))
        if coros:
            await self._fan_out(*coros)

    async def stop_loss(
        self,
        pair:          str,
        loss_usd:      float,
        loss_pct:      float,
        trigger_price: float = 0.0,
        regime:        str   = "",
    ) -> None:
        coros = []
        if self._telegram:
            coros.append(self._telegram.send_halt(
                reason="STOP_LOSS",
                details=f"Loss: {loss_usd:.2f} USDT ({loss_pct:.2%})",
                pair=pair,
            ))
        if self._discord:
            coros.append(self._discord.send_stop_loss(
                pair=pair, loss_usd=loss_usd, loss_pct=loss_pct,
                trigger_price=trigger_price, regime=regime,
            ))
        if coros:
            await self._fan_out(*coros)

    async def halt(
        self,
        reason:  str,
        details: str = "",
        pair:    str = "",
    ) -> None:
        coros = []
        if self._telegram:
            coros.append(self._telegram.send_halt(reason=reason, details=details, pair=pair))
        if self._discord:
            coros.append(self._discord.send_halt(reason=reason, details=details, pair=pair))
        if coros:
            await self._fan_out(*coros)

    async def regime_change(
        self,
        pair:            str,
        old_regime:      str,
        new_regime:      str,
        active_strategy: str = "",
        bars_in_regime:  int = 0,
    ) -> None:
        coros = []
        if self._discord:
            coros.append(self._discord.send_regime_change(
                pair=pair, old_regime=old_regime, new_regime=new_regime,
                active_strategy=active_strategy, bars_in_regime=bars_in_regime,
            ))
        # Telegram regime change — use custom message (lightweight)
        if self._telegram:
            coros.append(self._telegram.send_custom(
                f"*Regime Change — {pair}*\n"
                f"`{old_regime}` → `{new_regime}`\n"
                + (f"Strategie: `{active_strategy}`" if active_strategy else "")
            ))
        if coros:
            await self._fan_out(*coros)

    async def daily_summary(
        self,
        realized_pnl: float,
        trade_count:  int,
        win_rate:     float,
        max_dd:       float,
        open_pairs:   int   = 0,
        capital_usd:  float = 0.0,
    ) -> None:
        coros = []
        if self._telegram:
            coros.append(self._telegram.send_daily_summary(
                realized_pnl=realized_pnl, trade_count=trade_count,
                win_rate=win_rate, max_dd=max_dd,
                open_pairs=open_pairs, capital_usd=capital_usd,
            ))
        if self._discord:
            coros.append(self._discord.send_daily_summary(
                realized_pnl=realized_pnl, trade_count=trade_count,
                win_rate=win_rate, max_dd=max_dd,
                open_pairs=open_pairs, capital_usd=capital_usd,
            ))
        if coros:
            await self._fan_out(*coros)

    async def optimizer_result(
        self,
        n_folds:       int,
        avg_sharpe:    float,
        best_params:   Dict[str, Any],
        regime_params: Dict[str, Dict[str, Any]],
    ) -> None:
        coros = []
        if self._discord:
            coros.append(self._discord.send_optimizer_result(
                n_folds=n_folds, avg_sharpe=avg_sharpe,
                best_params=best_params, regime_params=regime_params,
            ))
        if self._telegram:
            param_str = ", ".join(f"{k}={v}" for k, v in best_params.items())
            coros.append(self._telegram.send_custom(
                f"*WalkForward Done*\nFolds: `{n_folds}` | Sharpe: `{avg_sharpe:.3f}`\nBest: `{param_str}`"
            ))
        if coros:
            await self._fan_out(*coros)


def build_bus_from_env() -> NotifierBus:
    """
    Build NotifierBus from environment variables.
    Returns a bus with whichever channels are configured.
    Unconfigured channels are None (silently skipped).
    """
    telegram = None
    discord  = None

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat  = os.getenv("TELEGRAM_CHAT_ID",   "")
    if tg_token and tg_chat:
        telegram = TelegramNotifier(NotifierConfig(
            bot_token=tg_token, chat_id=tg_chat,
        ))
        logger.info("NotifierBus: Telegram configurata")
    else:
        logger.info("NotifierBus: Telegram neconfigurat (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID lipsa)")

    dsc_url = os.getenv("DISCORD_WEBHOOK_URL", "")
    if dsc_url:
        discord = DiscordNotifier(DiscordConfig(webhook_url=dsc_url))
        logger.info("NotifierBus: Discord configurat")
    else:
        logger.info("NotifierBus: Discord neconfigurat (DISCORD_WEBHOOK_URL lipsa)")

    return NotifierBus(telegram=telegram, discord=discord)
