"""
tests/test_telegram_notifier.py  —  TelegramNotifier unit tests

All HTTP calls are mocked — no real Telegram requests.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from notifications.telegram_notifier import TelegramNotifier, NotifierConfig, AlertLevel


@pytest.fixture
def notifier_config():
    return NotifierConfig(
        bot_token="test_token_123",
        chat_id="-100123456",
        enabled=True,
        min_pnl_notify_usd=0.0,  # notify on everything in tests
    )


@pytest.fixture
def notifier(notifier_config):
    return TelegramNotifier(notifier_config)


class TestTelegramNotifier:
    @pytest.mark.asyncio
    async def test_send_custom_calls_http(self, notifier):
        with patch.object(notifier, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = True
            await notifier.send_custom("Test message", AlertLevel.INFO)
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_disabled_does_not_call_http(self, notifier_config):
        cfg = NotifierConfig(
            bot_token="tok", chat_id="-100", enabled=False
        )
        n = TelegramNotifier(cfg)
        with patch.object(n, "_post", new_callable=AsyncMock) as mock_post:
            await n.send_custom("msg", AlertLevel.INFO)
            mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_post_failure_does_not_raise(self, notifier):
        """Network failure must never propagate to caller (fail-safe)."""
        with patch.object(notifier, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = Exception("Network error")
            # Must not raise
            await notifier.send_custom("msg", AlertLevel.CRITICAL)

    @pytest.mark.asyncio
    async def test_send_trade_entry_format(self, notifier):
        with patch.object(notifier, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = True
            await notifier.send_trade_entry(
                pair="BTCUSDT/ETHUSDT",
                direction="LONG",
                zscore=2.35,
                notional_usdt=1500.0,
                hedge_ratio=1.52,
                sizing_method="kelly",
            )
            assert mock_post.called
            call_args = mock_post.call_args[0][0]  # message text
            assert "BTCUSDT" in call_args or "BTC" in call_args.upper()

    @pytest.mark.asyncio
    async def test_send_halt_format(self, notifier):
        with patch.object(notifier, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = True
            await notifier.send_halt(reason="HARD_STOP", details="DD exceeded")
            assert mock_post.called
