"""
tests/conftest.py  —  Shared pytest fixtures for QuantLuna test suite

Fixtures disponibile global (fără import):
  rng              — numpy Generator cu seed fix 42 (reproductibil)
  sample_prices    — DataFrame cu coloanele close_y / close_x (500 bare 1h)
  cointegrated     — dict {y: Series, x: Series} — pereche cointegrated
  mock_ccxt        — Mock exchange CCXT cu fetch_ohlcv + create_order + fetch_balance
  mock_ws          — Mock WebSocket async cu send/recv/close
  sample_trades    — list[dict] cu 20 trade-uri sintetice
  strategy_config  — StrategyConfig default pentru teste
  coint_config     — CointegrationConfig default pentru teste
"""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ===========================================================================
# Base fixtures
# ===========================================================================

@pytest.fixture(scope="session")
def rng() -> np.random.Generator:
    """Seeded NumPy Generator — same seed = reproductible tests."""
    return np.random.default_rng(42)


@pytest.fixture
def sample_prices(rng) -> pd.DataFrame:
    """
    500 hourly bars cu două serii cointegrate.
    col close_y ≈ 1.5 * close_x + 10 + noise
    """
    n = 500
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    x = 100 + np.cumsum(rng.normal(0, 0.3, n))
    y = 1.5 * x + 10 + rng.normal(0, 0.5, n)
    return pd.DataFrame({"close_y": y, "close_x": x}, index=idx)


@pytest.fixture
def cointegrated(sample_prices) -> dict:
    """Dict cu y/x Series separate — convenabil pentru Engle-Granger."""
    return {
        "y": sample_prices["close_y"],
        "x": sample_prices["close_x"],
    }


# ===========================================================================
# Config fixtures
# ===========================================================================

@pytest.fixture
def strategy_config():
    """StrategyConfig cu valori default, sym BTCUSDT/ETHUSDT."""
    try:
        from config.strategy_config import StrategyConfig
        return StrategyConfig()
    except ImportError:
        return None


@pytest.fixture
def coint_config():
    """CointegrationConfig cu valori default."""
    try:
        from config.cointegration_config import CointegrationConfig
        return CointegrationConfig()
    except ImportError:
        return None


# ===========================================================================
# Mock CCXT Exchange
# ===========================================================================

@pytest.fixture
def mock_ccxt(rng):
    """
    Mock CCXT exchange cu:
      - fetch_ohlcv: returnează OHLCV list[list]
      - create_order: returnează order dict
      - fetch_balance: returnează balanță USDT
      - fetch_ticker: returnează ticker cu bid/ask
      - cancel_order: returnează success
    """
    exchange = MagicMock()
    exchange.id = "bybit"
    exchange.name = "Bybit"

    # OHLCV: [timestamp_ms, open, high, low, close, volume]
    def _ohlcv(symbol, timeframe="1h", since=None, limit=500, params=None):
        n = limit or 500
        prices = 100 + np.cumsum(rng.normal(0, 0.5, n))
        ts_start = 1_700_000_000_000
        return [
            [ts_start + i * 3_600_000, p - 0.5, p + 1, p - 1, p, 1000 + i]
            for i, p in enumerate(prices)
        ]

    exchange.fetch_ohlcv.side_effect = _ohlcv

    exchange.create_order.return_value = {
        "id": "mock_order_001",
        "symbol": "BTCUSDT",
        "type": "market",
        "side": "buy",
        "amount": 0.01,
        "filled": 0.01,
        "price": 50_000.0,
        "status": "closed",
        "timestamp": 1_700_000_000_000,
    }

    exchange.fetch_balance.return_value = {
        "USDT": {"free": 10_000.0, "used": 0.0, "total": 10_000.0},
        "BTC":  {"free": 0.01,    "used": 0.0, "total": 0.01},
        "ETH":  {"free": 0.5,     "used": 0.0, "total": 0.5},
    }

    exchange.fetch_ticker.return_value = {
        "symbol": "BTCUSDT",
        "bid": 49_990.0,
        "ask": 50_010.0,
        "last": 50_000.0,
        "timestamp": 1_700_000_000_000,
    }

    exchange.cancel_order.return_value = {"id": "mock_order_001", "status": "canceled"}
    exchange.load_markets.return_value = {}
    exchange.has = {"fetchOHLCV": True, "createOrder": True, "cancelOrder": True}

    return exchange


# ===========================================================================
# Mock WebSocket
# ===========================================================================

@pytest.fixture
def mock_ws():
    """
    Mock async WebSocket.
    Trimite 3 mesaje de preț sintetice, apoi ridică StopAsyncIteration.
    """
    import json

    messages = [
        json.dumps({"topic": "kline.1h.BTCUSDT", "data": [{"close": "50000", "ts": 1700000000000}]}),
        json.dumps({"topic": "kline.1h.ETHUSDT", "data": [{"close": "3000",  "ts": 1700000000000}]}),
        json.dumps({"topic": "kline.1h.BTCUSDT", "data": [{"close": "50100", "ts": 1700003600000}]}),
    ]
    idx = 0

    ws = AsyncMock()
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=False)

    async def _recv():
        nonlocal idx
        if idx >= len(messages):
            raise StopAsyncIteration
        msg = messages[idx]
        idx += 1
        return msg

    ws.recv = _recv
    ws.send = AsyncMock(return_value=None)
    ws.close = AsyncMock(return_value=None)
    ws.open = True
    return ws


# ===========================================================================
# Trade data
# ===========================================================================

@pytest.fixture
def sample_trades(rng) -> list:
    """20 trade-uri sintetice cu toate câmpurile necesare pentru analytics."""
    trades = []
    base_ts = pd.Timestamp("2025-01-01 00:00:00", tz="UTC")
    equity = 10_000.0

    for i in range(20):
        pnl = float(rng.normal(15, 80))  # întamplător pozitiv/negativ
        notional = float(rng.uniform(200, 800))
        equity += pnl
        direction = "long" if rng.random() > 0.5 else "short"
        entry_ts = base_ts + pd.Timedelta(hours=i * 12)
        exit_ts  = entry_ts + pd.Timedelta(hours=rng.integers(2, 48))

        trades.append({
            "entry_ts":      entry_ts.isoformat(),
            "exit_ts":       exit_ts.isoformat(),
            "pair":          "BTCUSDT/ETHUSDT",
            "direction":     direction,
            "notional_usdt": notional,
            "pnl_net":       round(pnl, 4),
            "pnl_pct":       round(pnl / notional, 6),
            "equity":        round(equity, 2),
            "reason":        "zscore_exit" if abs(pnl) < 50 else "stop_loss",
        })
    return trades
