"""
Module: tests/test_live_ws.py
Sprint: 31 — R (Live Trading Integration)
Description:
    8 pytest tests for BybitPrivateWS and LiveExecutionBridge
    using mocked WebSocket connections.
"""

from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from execution.bybit_private_ws import BybitPrivateWS
from execution.live_execution_bridge import LiveExecutionBridge, EventType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ws_client() -> BybitPrivateWS:
    return BybitPrivateWS(api_key="test_key", api_secret="test_secret", testnet=True)


@pytest.fixture
def bridge(ws_client: BybitPrivateWS) -> LiveExecutionBridge:
    return LiveExecutionBridge(ws=ws_client)


# ---------------------------------------------------------------------------
# Tests — BybitPrivateWS
# ---------------------------------------------------------------------------

class TestBybitPrivateWS:
    def test_url_testnet(self) -> None:
        ws = BybitPrivateWS("k", "s", testnet=True)
        assert "testnet" in ws.url

    def test_url_mainnet(self) -> None:
        ws = BybitPrivateWS("k", "s", testnet=False)
        assert "testnet" not in ws.url

    def test_auth_payload_structure(self) -> None:
        ws = BybitPrivateWS("mykey", "mysecret")
        payload = ws._build_auth_payload()
        assert payload["op"] == "auth"
        assert payload["args"][0] == "mykey"
        assert isinstance(payload["args"][1], int)  # expires
        assert isinstance(payload["args"][2], str)  # signature

    def test_handler_registration(self) -> None:
        ws = BybitPrivateWS("k", "s")
        handler = AsyncMock()
        ws.on_order(handler)
        ws.on_position(handler)
        assert handler in ws._handlers["order"]
        assert handler in ws._handlers["position"]

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self) -> None:
        ws = BybitPrivateWS("k", "s")
        ws._running = True
        ws._ws = AsyncMock()
        await ws.stop()
        assert ws._running is False
        assert ws.connected is False


# ---------------------------------------------------------------------------
# Tests — LiveExecutionBridge
# ---------------------------------------------------------------------------

class TestLiveExecutionBridge:
    @pytest.mark.asyncio
    async def test_start_registers_handlers(self) -> None:
        ws = MagicMock()
        bridge = LiveExecutionBridge(ws=ws)
        await bridge.start()
        ws.on_order.assert_called_once()
        ws.on_position.assert_called_once()
        ws.on_execution.assert_called_once()
        ws.on_wallet.assert_called_once()

    @pytest.mark.asyncio
    async def test_order_fill_event(self) -> None:
        ws = MagicMock()
        bridge = LiveExecutionBridge(ws=ws)
        await bridge.start()
        msg = {
            "topic": "order",
            "data": [{"orderId": "123", "symbol": "BTCUSDT", "side": "Buy",
                      "qty": "0.01", "avgPrice": "50000", "orderStatus": "Filled"}],
        }
        await bridge._handle_order(msg)
        assert not bridge._queue.empty()
        event = bridge._queue.get_nowait()
        assert event.event_type == EventType.ORDER_FILL
        assert event.payload["symbol"] == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_wallet_equity_update(self) -> None:
        ws = MagicMock()
        bridge = LiveExecutionBridge(ws=ws)
        await bridge.start()
        msg = {
            "topic": "wallet",
            "data": [{"coin": [{"coin": "USDT", "equity": "12345.5",
                                  "walletBalance": "12000", "availableToWithdraw": "11000"}]}],
        }
        await bridge._handle_wallet(msg)
        event = bridge._queue.get_nowait()
        assert event.event_type == EventType.EQUITY_UPDATE
        assert event.payload["equity"] == pytest.approx(12345.5)
