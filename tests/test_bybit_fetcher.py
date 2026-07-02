"""
QuantLuna — Tests: data/bybit_fetcher.py
Sprint 28  |  8 tests (mocked requests — fara apeluri reale Bybit)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.bybit_fetcher import BybitHistoricalFetcher


def _bybit_bars(n: int = 10, start_ms: int = 1_700_000_000_000) -> list:
    """Bybit format: [startTime, open, high, low, close, volume, turnover] newest-first."""
    bars = []
    for i in range(n - 1, -1, -1):  # newest first
        t = start_ms + i * 3_600_000
        bars.append([str(t), "100.0", "101.0", "99.0", "100.5", "1000.0", "100500.0"])
    return bars


def _mock_resp(bars: list, ret_code: int = 0):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "retCode": ret_code,
        "result": {"list": bars},
    }
    return resp


@pytest.fixture
def tmp_fetcher(tmp_path):
    return BybitHistoricalFetcher(cache_dir=str(tmp_path), ttl_hours=24.0, category="linear")


class TestBybitHistoricalFetcher:

    def test_download_returns_dataframe(self, tmp_fetcher):
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [_mock_resp(_bybit_bars(50)), _mock_resp([])]
            df = tmp_fetcher.fetch("BTCUSDT", "60", start="2024-01-01", end="2024-01-03")
        assert isinstance(df, pd.DataFrame)
        assert "close" in df.columns
        assert "open_time" in df.columns

    def test_float_columns_are_float(self, tmp_fetcher):
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [_mock_resp(_bybit_bars(5)), _mock_resp([])]
            df = tmp_fetcher.fetch("BTCUSDT", "60")
        assert df["close"].dtype == float
        assert df["volume"].dtype == float

    def test_alias_interval_1h(self, tmp_fetcher):
        """'1h' alias trebuie tradus in '60' intern."""
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [_mock_resp(_bybit_bars(5)), _mock_resp([])]
            df = tmp_fetcher.fetch("BTCUSDT", "1h")
        assert not df.empty
        # Verific ca a fost apelat cu interval=60
        call_params = mock_get.call_args[1]["params"]
        assert call_params["interval"] == "60"

    def test_cache_hit_no_request(self, tmp_fetcher):
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [_mock_resp(_bybit_bars(10)), _mock_resp([])]
            tmp_fetcher.fetch("BTCUSDT", "60")
        with patch("requests.get") as mock_get2:
            tmp_fetcher.fetch("BTCUSDT", "60")
            assert mock_get2.call_count == 0

    def test_force_refresh_bypasses_cache(self, tmp_fetcher):
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(_bybit_bars(10)), _mock_resp([]),
                _mock_resp(_bybit_bars(10)), _mock_resp([]),
            ]
            tmp_fetcher.fetch("BTCUSDT", "60")
            tmp_fetcher.fetch("BTCUSDT", "60", force_refresh=True)
            assert mock_get.call_count >= 2

    def test_meta_file_has_exchange_bybit(self, tmp_fetcher, tmp_path):
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [_mock_resp(_bybit_bars(5)), _mock_resp([])]
            tmp_fetcher.fetch("BTCUSDT", "60")
        metas = list(tmp_path.glob("*.meta.json"))
        assert len(metas) == 1
        with open(metas[0]) as f:
            meta = json.load(f)
        assert meta["exchange"] == "bybit"
        assert meta["symbol"]   == "BTCUSDT"

    def test_empty_response_returns_empty_df(self, tmp_fetcher):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_resp([])
            df = tmp_fetcher.fetch("BTCUSDT", "60")
        assert df.empty

    def test_bybit_api_error_raises(self, tmp_fetcher):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_resp([], ret_code=10001)
            with pytest.raises(RuntimeError, match="Bybit API error"):
                tmp_fetcher.fetch("BTCUSDT", "60")
