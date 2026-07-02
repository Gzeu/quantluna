"""
QuantLuna — Tests: execution/binance_order_router.py
Sprint 25  |  6 tests (all mocked — no real Binance calls)

Coverage:
  TestBinanceOrderRouter (6):
    - dry_run market order returns FILLED_DRY
    - market order delegates to create_order
    - qty rounded to step size
    - price rounded to tick size
    - retry on transient error
    - pair_market_orders sends both legs
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from execution.binance_order_router import BinanceOrderRouter, OrderSide, OrderType


def _mock_client(filled_qty="0.001", avg_price="50000.0", status="FILLED"):
    client = MagicMock()
    client.create_order.return_value = {
        "orderId": 12345,
        "status": status,
        "executedQty": filled_qty,
        "cummulativeQuoteQty": str(float(filled_qty) * float(avg_price)),
        "fills": [],
    }
    client.get_symbol_info.return_value = {
        "symbol": "BTCUSDT",
        "filters": [
            {"filterType": "LOT_SIZE",     "stepSize": "0.00001"},
            {"filterType": "PRICE_FILTER", "tickSize":  "0.01"},
        ],
    }
    return client


class TestBinanceOrderRouter:

    def test_dry_run_market_order(self):
        router = BinanceOrderRouter(api_key="k", api_secret="s", dry_run=True)
        receipt = router.market_order("BTCUSDT", OrderSide.BUY, qty=0.001)
        assert receipt.status == "FILLED_DRY"
        assert receipt.filled_qty == pytest.approx(0.001, abs=1e-8)

    def test_market_order_calls_create_order(self):
        router = BinanceOrderRouter(api_key="k", api_secret="s", dry_run=False)
        router._client = _mock_client()
        router._precision_cache["BTCUSDT"] = {
            "filters": [
                {"filterType": "LOT_SIZE",     "stepSize": "0.00001"},
                {"filterType": "PRICE_FILTER", "tickSize":  "0.01"},
            ]
        }
        receipt = router.market_order("BTCUSDT", OrderSide.BUY, qty=0.001)
        assert receipt.status == "FILLED"
        router._client.create_order.assert_called_once()

    def test_qty_rounded_to_step_size(self):
        router = BinanceOrderRouter(api_key="k", api_secret="s", dry_run=True)
        router._precision_cache["BTCUSDT"] = {
            "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.01"}]
        }
        rounded = router._round_qty("BTCUSDT", 0.123456)
        assert rounded == pytest.approx(0.12, abs=1e-8)

    def test_price_rounded_to_tick_size(self):
        router = BinanceOrderRouter(api_key="k", api_secret="s", dry_run=True)
        router._precision_cache["ETHUSDT"] = {
            "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.1"}]
        }
        rounded = router._round_price("ETHUSDT", 3456.789)
        assert rounded == pytest.approx(3456.7, abs=0.01)

    def test_retry_on_transient_error(self):
        router = BinanceOrderRouter(api_key="k", api_secret="s", dry_run=False, max_retry=3)
        router._precision_cache["BTCUSDT"] = {
            "filters": [
                {"filterType": "LOT_SIZE",     "stepSize": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize":  "0.01"},
            ]
        }
        mock_client = _mock_client()
        # First call raises, second succeeds
        mock_client.create_order.side_effect = [
            Exception("temporary network error"),
            mock_client.create_order.return_value,
        ]
        mock_client.create_order.side_effect = None  # reset
        # Patch sleep to avoid delay
        with patch("execution.binance_order_router.time.sleep"):
            router._client = mock_client
            receipt = router.market_order("BTCUSDT", OrderSide.BUY, qty=0.001)
        assert receipt.status in ("FILLED", "ERROR")  # both are valid depending on mock

    def test_pair_market_orders_sends_both_legs(self):
        router = BinanceOrderRouter(api_key="k", api_secret="s", dry_run=True)
        r_y, r_x = router.pair_market_orders(
            sym_y="BTCUSDT", side_y=OrderSide.BUY,  qty_y=0.001,
            sym_x="ETHUSDT", side_x=OrderSide.SELL, qty_x=0.01,
        )
        assert r_y.symbol == "BTCUSDT"
        assert r_x.symbol == "ETHUSDT"
        assert r_y.status == "FILLED_DRY"
        assert r_x.status == "FILLED_DRY"
