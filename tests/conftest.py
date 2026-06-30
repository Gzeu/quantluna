"""
tests/conftest.py  —  QuantLuna Test Fixtures

Shared fixtures pentru toate testele:
  - Synthetic OHLCV data (cointegrated pair)
  - Mock CCXT exchange
  - Mock WebSocket feed
  - KalmanHedgeRatio instance
  - Sample trade list
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator, List
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Synthetic cointegrated price series
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def rng() -> np.random.Generator:
    return np.random.default_rng(seed=42)


@pytest.fixture(scope="session")
def synthetic_prices(rng) -> tuple[pd.Series, pd.Series]:
    """Returns (y, x) cointegrated price series, 500 bars, 1h freq."""
    n = 500
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")

    # x = random walk
    x = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    # y = 1.5 * x + 20 + noise (cointegrated)
    y = 1.5 * x + 20.0 + rng.normal(0, 1.5, n)

    return pd.Series(y, index=idx, name="Y"), pd.Series(x, index=idx, name="X")


@pytest.fixture(scope="session")
def synthetic_ohlcv(synthetic_prices) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (ohlcv_y, ohlcv_x) DataFrames from synthetic prices."""
    def _to_ohlcv(prices: pd.Series) -> pd.DataFrame:
        rng2 = np.random.default_rng(seed=99)
        df = pd.DataFrame(index=prices.index)
        df["close"] = prices.values
        df["open"] = prices.values * (1 + rng2.normal(0, 0.001, len(prices)))
        df["high"] = df[["open", "close"]].max(axis=1) * (1 + abs(rng2.normal(0, 0.001, len(prices))))
        df["low"] = df[["open", "close"]].min(axis=1) * (1 - abs(rng2.normal(0, 0.001, len(prices))))
        df["volume"] = rng2.uniform(100, 1000, len(prices))
        return df

    y_ser, x_ser = synthetic_prices
    return _to_ohlcv(y_ser), _to_ohlcv(x_ser)


# ---------------------------------------------------------------------------
# KalmanHedgeRatio
# ---------------------------------------------------------------------------

@pytest.fixture
def kalman():
    from core.kalman_filter import KalmanHedgeRatio
    return KalmanHedgeRatio(delta=1e-4, observation_noise=1e-2, warm_up=10)


@pytest.fixture
def fitted_kalman(kalman, synthetic_prices):
    y, x = synthetic_prices
    df = kalman.fit(y, x)
    return kalman, df


# ---------------------------------------------------------------------------
# Sample trades
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_trades():
    """List of mock trade objects with pnl_net and fees."""
    trades = []
    for i in range(20):
        t = MagicMock()
        t.pnl_net = (1 if i % 3 != 0 else -1) * float(np.random.default_rng(i).uniform(10, 80))
        t.fees = float(np.random.default_rng(i + 100).uniform(0.5, 2.0))
        t.funding_paid = float(np.random.default_rng(i + 200).uniform(0, 0.5))
        trades.append(t)
    return trades


# ---------------------------------------------------------------------------
# Mock CCXT exchange
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_ccxt_exchange():
    """Mock CCXT exchange with common methods patched."""
    ex = MagicMock()
    ex.__version__ = "4.0.0"

    # load_markets returns realistic subset
    ex.load_markets.return_value = {
        "BTC/USDT:USDT": {"symbol": "BTC/USDT:USDT", "active": True},
        "ETH/USDT:USDT": {"symbol": "ETH/USDT:USDT", "active": True},
    }

    # fetch_ohlcv returns 10 bars then empty (simulates pagination end)
    ts_base = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    bars = [[ts_base + i * 3_600_000, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 500.0] for i in range(10)]
    ex.fetch_ohlcv.side_effect = [bars, []]  # first call returns data, second empty

    # fetch_balance
    ex.fetch_balance.return_value = {"USDT": {"free": 5000.0, "total": 5000.0}}

    # create_order
    ex.create_order.return_value = {"id": "order_123", "status": "filled", "average": 100.5}

    return ex


# ---------------------------------------------------------------------------
# Mock WebSocket
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_websocket():
    ws = AsyncMock()
    ws.recv = AsyncMock(return_value='{"topic":"tickers","data":{"lastPrice":"100.5"}}')
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# Temporary SQLite DB path
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test_trades.db")


@pytest.fixture
def tmp_cache_dir(tmp_path):
    return str(tmp_path / "cache")
