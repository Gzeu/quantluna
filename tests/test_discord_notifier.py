"""
QuantLuna — Tests: notifications/discord_notifier.py
Sprint 26  |  6 tests (mocked aiohttp — fără webhook real)
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from notifications.discord_notifier import DiscordConfig, DiscordNotifier


def _make_notifier(enabled: bool = True) -> DiscordNotifier:
    return DiscordNotifier(DiscordConfig(
        webhook_url="https://discord.com/api/webhooks/test/token" if enabled else "",
        enabled=enabled,
        max_retries=1,
        retry_base_delay_s=0.0,
    ))


class TestDiscordNotifier:

    def test_not_configured_skips_delivery(self):
        notifier = _make_notifier(enabled=False)
        assert not notifier.is_configured
        # Should not raise
        asyncio.run(notifier.send_trade_entry(
            pair="BTC/ETH", side_y="buy", zscore=-2.1,
            notional_usd=100.0, hedge_ratio=0.05,
        ))

    def test_trade_entry_long(self):
        notifier = _make_notifier()
        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_resp = AsyncMock()
            mock_resp.status = 204
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                post=AsyncMock(return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_resp),
                    __aexit__=AsyncMock(return_value=False),
                ))
            ))
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            asyncio.run(notifier.send_trade_entry(
                pair="BTC/ETH", side_y="buy", zscore=-2.3,
                notional_usd=100.0, hedge_ratio=0.05,
                active_strategy="BollingerBands", regime="ranging",
            ))

    def test_stop_loss_fields(self):
        notifier = _make_notifier()
        with patch.object(notifier, "_deliver_with_retry", new=AsyncMock()) as mock_deliver:
            asyncio.run(notifier.send_stop_loss(
                pair="BTC/ETH", loss_usd=-3.5, loss_pct=-0.035,
                trigger_price=29800.0, regime="breakout",
            ))
            mock_deliver.assert_called_once()
            payload = mock_deliver.call_args[0][0]
            assert "STOP-LOSS" in payload["embeds"][0]["title"]

    def test_regime_change_embed_colour_blue(self):
        notifier = _make_notifier()
        with patch.object(notifier, "_deliver_with_retry", new=AsyncMock()) as mock_deliver:
            asyncio.run(notifier.send_regime_change(
                pair="BTC/ETH", old_regime="ranging", new_regime="trending",
                active_strategy="ZScoreMomentum",
            ))
            payload = mock_deliver.call_args[0][0]
            assert payload["embeds"][0]["color"] == 0x3498DB

    def test_daily_summary_green_on_profit(self):
        notifier = _make_notifier()
        with patch.object(notifier, "_deliver_with_retry", new=AsyncMock()) as mock_deliver:
            asyncio.run(notifier.send_daily_summary(
                realized_pnl=42.0, trade_count=7, win_rate=0.71, max_dd=0.04
            ))
            payload = mock_deliver.call_args[0][0]
            assert payload["embeds"][0]["color"] == 0x2ECC71

    def test_daily_summary_red_on_loss(self):
        notifier = _make_notifier()
        with patch.object(notifier, "_deliver_with_retry", new=AsyncMock()) as mock_deliver:
            asyncio.run(notifier.send_daily_summary(
                realized_pnl=-10.0, trade_count=3, win_rate=0.33, max_dd=0.08
            ))
            payload = mock_deliver.call_args[0][0]
            assert payload["embeds"][0]["color"] == 0xE74C3C
