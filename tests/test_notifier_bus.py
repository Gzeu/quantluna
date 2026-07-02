"""
QuantLuna — Tests: notifications/notifier_bus.py
Sprint 26  |  4 tests
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from notifications.discord_notifier import DiscordConfig, DiscordNotifier
from notifications.notifier_bus import NotifierBus, build_bus_from_env
from notifications.telegram_notifier import NotifierConfig, TelegramNotifier


def _bus_with_mocks():
    tg = TelegramNotifier(NotifierConfig(bot_token="t", chat_id="c", enabled=True))
    dc = DiscordNotifier(DiscordConfig(webhook_url="https://discord.com/api/webhooks/x/y", enabled=True))
    tg.send_halt           = AsyncMock()
    tg.send_trade_entry    = AsyncMock()
    tg.send_trade_exit     = AsyncMock()
    tg.send_custom         = AsyncMock()
    tg.send_daily_summary  = AsyncMock()
    dc.send_stop_loss      = AsyncMock()
    dc.send_trade_entry    = AsyncMock()
    dc.send_trade_exit     = AsyncMock()
    dc.send_regime_change  = AsyncMock()
    dc.send_daily_summary  = AsyncMock()
    return NotifierBus(telegram=tg, discord=dc)


class TestNotifierBus:

    def test_trade_entry_fans_out_to_both(self):
        bus = _bus_with_mocks()
        asyncio.run(bus.trade_entry(
            pair="BTC/ETH", side_y="buy", zscore=-2.0,
            notional_usd=100.0, hedge_ratio=0.05,
            active_strategy="BB", regime="ranging",
        ))
        bus._telegram.send_trade_entry.assert_called_once()
        bus._discord.send_trade_entry.assert_called_once()

    def test_stop_loss_calls_both_channels(self):
        bus = _bus_with_mocks()
        asyncio.run(bus.stop_loss(
            pair="BTC/ETH", loss_usd=-3.0, loss_pct=-0.03,
            trigger_price=29500.0, regime="breakout",
        ))
        bus._telegram.send_halt.assert_called_once()
        bus._discord.send_stop_loss.assert_called_once()

    def test_bus_with_no_channels_no_error(self):
        bus = NotifierBus()  # no channels configured
        asyncio.run(bus.trade_entry(
            pair="X/Y", side_y="buy", zscore=0.0,
            notional_usd=0.0, hedge_ratio=0.0,
        ))

    def test_build_bus_from_env_returns_bus(self):
        bus = build_bus_from_env()  # likely unconfigured in test env
        assert isinstance(bus, NotifierBus)
