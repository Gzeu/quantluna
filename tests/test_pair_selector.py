"""
Tests for strategy/pair_selector.py v2 -- Sprint 3
Covers: composite_score, scan, get_top_n, rescan_stale, filtering.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from strategy.pair_selector import PairSelector, PairScore
from config.settings import CointegrationConfig


# --- Helpers ------------------------------------------------------------------

def _prices(symbols, n=500, seed=0, beta=1.2):
    """Generate cointegrated-like price DataFrame."""
    rng = np.random.default_rng(seed)
    out = {}
    base = 30_000 + np.cumsum(rng.normal(0, 50, n))
    for i, sym in enumerate(symbols):
        noise = rng.normal(0, 200, n)
        out[sym] = base * (1.0 + 0.05 * i) + 5000 * i + noise
    return pd.DataFrame(out)


def _mock_coint_result(is_cointegrated=True, pval=0.01, hl=24.0, hurst=0.35):
    r = MagicMock()
    r.is_cointegrated = is_cointegrated
    r.adf_pvalue      = pval
    r.eg_pvalue       = pval
    r.half_life_hours = hl
    r.hurst_exponent  = hurst
    r.static_beta     = 1.2
    r.spread_std      = 500.0
    r.verdict         = "cointegrated" if is_cointegrated else "not_cointegrated"
    return r


# --- composite_score ----------------------------------------------------------

class TestCompositeScore:
    def _sel(self):
        return PairSelector(["A", "B"], optimal_hl_hours=24.0, min_corr=0.6)

    def test_score_in_unit_interval(self):
        sel = self._sel()
        s = sel.composite_score(0.01, 24.0, 0.35, 500.0, 1.2, 0.9)
        assert 0.0 <= s <= 1.0

    def test_perfect_conditions_high_score(self):
        sel = self._sel()
        s = sel.composite_score(0.001, 24.0, 0.1, 100.0, 1.2, 1.0)
        assert s > 0.5

    def test_bad_conditions_low_score(self):
        sel = self._sel()
        s = sel.composite_score(0.99, 200.0, 0.49, 5000.0, 1.2, 0.6)
        assert s < 0.5

    def test_lower_pvalue_raises_score(self):
        sel = self._sel()
        s1 = sel.composite_score(0.01, 24.0, 0.3, 500.0, 1.2, 0.8)
        s2 = sel.composite_score(0.04, 24.0, 0.3, 500.0, 1.2, 0.8)
        assert s1 > s2

    def test_optimal_half_life_maximises_hl_score(self):
        sel = self._sel()
        s_opt = sel.composite_score(0.01, 24.0, 0.3, 500.0, 1.2, 0.8)
        s_far = sel.composite_score(0.01, 150.0, 0.3, 500.0, 1.2, 0.8)
        assert s_opt > s_far

    def test_nan_half_life_returns_valid_score(self):
        sel = self._sel()
        s = sel.composite_score(0.01, float("nan"), 0.3, 500.0, 1.2, 0.8)
        assert 0.0 <= s <= 1.0

    def test_low_hurst_raises_score(self):
        sel = self._sel()
        s_low  = sel.composite_score(0.01, 24.0, 0.1, 500.0, 1.2, 0.8)
        s_high = sel.composite_score(0.01, 24.0, 0.48, 500.0, 1.2, 0.8)
        assert s_low > s_high


# --- scan with mocked CointegrationTest ---------------------------------------

class TestScan:
    def _make_selector(self, universe):
        return PairSelector(universe, min_corr=0.0)  # disable corr pre-filter

    def test_scan_returns_dataframe(self):
        prices = _prices(["BTC", "ETH", "BNB"])
        sel = self._make_selector(["BTC", "ETH", "BNB"])
        with patch.object(sel._test, "run", return_value=_mock_coint_result()):
            df = sel.scan(prices, log_prices=False)
        assert isinstance(df, pd.DataFrame)
        assert "pair" in df.columns
        assert "composite_score" in df.columns

    def test_scan_sorted_descending(self):
        prices = _prices(["BTC", "ETH", "BNB"])
        sel = self._make_selector(["BTC", "ETH", "BNB"])
        with patch.object(sel._test, "run", return_value=_mock_coint_result()):
            df = sel.scan(prices, log_prices=False)
        assert df["composite_score"].is_monotonic_decreasing or len(df) <= 1

    def test_scan_missing_symbol_skipped(self):
        prices = _prices(["BTC", "ETH"])  # BNB missing
        sel = self._make_selector(["BTC", "ETH", "BNB"])
        with patch.object(sel._test, "run", return_value=_mock_coint_result()):
            df = sel.scan(prices, log_prices=False)
        pairs_tested = df["pair"].tolist()
        assert all("BNB" not in p for p in pairs_tested)

    def test_scan_empty_universe_returns_empty_df(self):
        prices = pd.DataFrame()
        sel = PairSelector([], min_corr=0.0)
        df = sel.scan(prices, log_prices=False)
        assert df.empty

    def test_non_cointegrated_pairs_score_zero(self):
        prices = _prices(["BTC", "ETH"])
        sel = self._make_selector(["BTC", "ETH"])
        with patch.object(sel._test, "run", return_value=_mock_coint_result(is_cointegrated=False)):
            df = sel.scan(prices, log_prices=False)
        if not df.empty:
            assert (df[df["is_cointegrated"] == False]["composite_score"] == 0.0).all()


# --- get_top_n ----------------------------------------------------------------

class TestGetTopN:
    def test_get_top_n_returns_list(self):
        prices = _prices(["BTC", "ETH", "BNB", "SOL"])
        sel = PairSelector(["BTC", "ETH", "BNB", "SOL"], min_corr=0.0)
        with patch.object(sel._test, "run", return_value=_mock_coint_result()):
            top = sel.get_top_n(prices, n=3, log_prices=False)
        assert isinstance(top, list)
        assert len(top) <= 3

    def test_get_top_n_items_are_pair_scores(self):
        prices = _prices(["BTC", "ETH"])
        sel = PairSelector(["BTC", "ETH"], min_corr=0.0)
        with patch.object(sel._test, "run", return_value=_mock_coint_result()):
            top = sel.get_top_n(prices, n=5, log_prices=False)
        for item in top:
            assert isinstance(item, PairScore)

    def test_get_top_n_empty_if_no_cointegrated(self):
        prices = _prices(["BTC", "ETH"])
        sel = PairSelector(["BTC", "ETH"], min_corr=0.0)
        with patch.object(sel._test, "run", return_value=_mock_coint_result(is_cointegrated=False)):
            top = sel.get_top_n(prices, n=3, log_prices=False)
        assert top == []


# --- rescan_stale -------------------------------------------------------------

class TestRescanStale:
    def test_rescan_no_stale_returns_empty(self):
        prices = _prices(["BTC", "ETH"])
        sel = PairSelector(["BTC", "ETH"], min_corr=0.0, staleness_hours=48.0)
        # Populate cache with fresh entries
        with patch.object(sel._test, "run", return_value=_mock_coint_result()):
            sel.scan(prices, log_prices=False)
        # Immediately rescan: nothing stale
        result = sel.rescan_stale(prices, log_prices=False)
        assert result.empty

    def test_rescan_stale_entries_are_retested(self):
        prices = _prices(["BTC", "ETH"])
        sel = PairSelector(["BTC", "ETH"], min_corr=0.0, staleness_hours=0.0)
        with patch.object(sel._test, "run", return_value=_mock_coint_result()) as mock_run:
            sel.scan(prices, log_prices=False)
            n_after_first_scan = mock_run.call_count
            # Force staleness
            for ps in sel._cache.values():
                ps.last_scanned = time.time() - 7200
            sel.rescan_stale(prices, log_prices=False)
            assert mock_run.call_count > n_after_first_scan
