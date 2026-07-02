"""
QuantLuna — Tests: data/historical_fetcher.py
Sprint 26  |  8 tests (mocked requests — fără apeluri reale Binance)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.historical_fetcher import BinanceHistoricalFetcher, _COLUMNS


def _fake_bars(n: int = 10, start_ms: int = 1_700_000_000_000) -> List[list]:
    bars = []
    for i in range(n):
        t = start_ms + i * 3_600_000
        bars.append([
            t, "100.0", "101.0", "99.0", "100.5", "1000.0",
            t + 3_599_999, "100500.0", 500,
            "500.0", "50000.0", "0",
        ])
    return bars


@pytest.fixture
def tmp_fetcher(tmp_path):
    return BinanceHistoricalFetcher(cache_dir=str(tmp_path), ttl_hours=24.0)


class TestHistoricalFetcher:

    def test_download_returns_dataframe(self, tmp_fetcher):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.side_effect = [_fake_bars(50), []]  # 2nd call returns empty = done
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            df = tmp_fetcher.fetch("BTCUSDT", "1h", start="2024-01-01", end="2024-01-03")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 50
        assert "close" in df.columns

    def test_float_columns_are_float(self, tmp_fetcher):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.side_effect = [_fake_bars(5), []]
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            df = tmp_fetcher.fetch("BTCUSDT", "1h")
        assert df["close"].dtype == float

    def test_cache_hit_no_request(self, tmp_fetcher):
        """Second fetch should use cache without calling requests.get."""
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.side_effect = [_fake_bars(10), []]
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            tmp_fetcher.fetch("BTCUSDT", "1h")
            first_call_count = mock_get.call_count

        with patch("requests.get") as mock_get2:
            tmp_fetcher.fetch("BTCUSDT", "1h")  # should hit cache
            assert mock_get2.call_count == 0

    def test_force_refresh_bypasses_cache(self, tmp_fetcher):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.side_effect = [_fake_bars(10), [], _fake_bars(10), []]
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            tmp_fetcher.fetch("BTCUSDT", "1h")
            tmp_fetcher.fetch("BTCUSDT", "1h", force_refresh=True)
            assert mock_get.call_count >= 2

    def test_meta_file_written(self, tmp_fetcher, tmp_path):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.side_effect = [_fake_bars(5), []]
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            tmp_fetcher.fetch("BTCUSDT", "1h")
        meta_files = list(tmp_path.glob("*.meta.json"))
        assert len(meta_files) == 1
        with open(meta_files[0]) as f:
            meta = json.load(f)
        assert meta["symbol"] == "BTCUSDT"
        assert meta["n_bars"] == 5

    def test_list_cache_returns_entries(self, tmp_fetcher, tmp_path):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.side_effect = [_fake_bars(5), []]
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            tmp_fetcher.fetch("BTCUSDT", "1h")
        items = tmp_fetcher.list_cache()
        assert len(items) == 1
        assert items[0]["symbol"] == "BTCUSDT"

    def test_delete_cache_removes_files(self, tmp_fetcher, tmp_path):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.side_effect = [_fake_bars(5), []]
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            tmp_fetcher.fetch("BTCUSDT", "1h")
        n = tmp_fetcher.delete_cache("BTCUSDT")
        assert n >= 2  # .parquet + .meta.json

    def test_empty_response_returns_empty_df(self, tmp_fetcher):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = []
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            df = tmp_fetcher.fetch("BTCUSDT", "1h")
        assert df.empty
