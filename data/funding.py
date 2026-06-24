"""
QuantLuna — FundingRateFetcher v2
Fetch current + historical funding rates from Binance/Bybit perpetuals.
Funding impact: cost = rate * leverage * (position_hours / 8)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from loguru import logger

try:
    import ccxt.async_support as ccxt_async
except ImportError:  # pragma: no cover
    ccxt_async = None

CACHE_DIR = Path("./data/cache")
HOURS_PER_FUNDING = 8.0   # standard funding interval


def annualized_funding_cost(rate: float, leverage: float = 1.0) -> float:
    """
    Convert per-interval funding rate to annualised cost fraction.
    Funding paid 3x/day -> 3 * 365 = 1095 intervals/year.
    """
    return rate * leverage * (365 * 24 / HOURS_PER_FUNDING)


class FundingRateFetcher:
    """
    Fetch and cache funding rate data for perpetual futures.

    Parameters
    ----------
    exchange_id : ccxt exchange id ('binance', 'bybit')
    """

    def __init__(self, exchange_id: str = "binance") -> None:
        self.exchange_id = exchange_id
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_current(self, symbol: str) -> float:
        """Return current funding rate for *symbol* (e.g. 'BTC/USDT:USDT')."""
        if ccxt_async is None:
            return 0.0

        exchange = None
        try:
            exchange = self._make_exchange()
            info = await exchange.fetch_funding_rate(symbol)
            rate = float(info.get("fundingRate", 0.0))
            logger.debug(f"Funding {symbol}: {rate*100:.4f}%")
            return rate
        except Exception as exc:
            logger.warning(f"fetch_current failed {symbol}: {exc}")
            return 0.0
        finally:
            if exchange:
                await exchange.close()

    async def fetch_current_multiple(self, symbols: List[str]) -> Dict[str, float]:
        """Return dict symbol -> current funding rate (concurrent)."""
        tasks = [self.fetch_current(s) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: Dict[str, float] = {}
        for sym, res in zip(symbols, results):
            out[sym] = float(res) if not isinstance(res, Exception) else 0.0
        return out

    async def fetch_history(
        self,
        symbol: str,
        since: Optional[int] = None,
        limit: int = 500,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch historical funding rates.

        Returns DataFrame:
          index   : DatetimeTZDtype UTC
          columns : fundingRate, annualized_cost (at leverage=1)
        """
        safe = symbol.replace("/", "_").replace(":", "_")
        path = CACHE_DIR / f"funding_{self.exchange_id}_{safe}.parquet"

        if use_cache and path.exists():
            df = pd.read_parquet(path)
            logger.debug(f"Funding cache hit: {symbol} ({len(df)} rows)")
            return df

        if ccxt_async is None:
            return pd.DataFrame(columns=["fundingRate", "annualized_cost"])

        exchange = None
        try:
            exchange = self._make_exchange()
            raw = await exchange.fetch_funding_rate_history(symbol, since=since, limit=limit)
            df = pd.DataFrame(raw)[["timestamp", "fundingRate"]]
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.set_index("timestamp").sort_index()
            df["fundingRate"] = df["fundingRate"].astype(float)
            df["annualized_cost"] = df["fundingRate"].apply(annualized_funding_cost)
            if use_cache:
                df.to_parquet(path)
            logger.info(f"Funding history {symbol}: {len(df)} intervals")
            return df
        except Exception as exc:
            logger.warning(f"fetch_history failed {symbol}: {exc}")
            return pd.DataFrame(columns=["fundingRate", "annualized_cost"])
        finally:
            if exchange:
                await exchange.close()

    def compute_drag(
        self,
        funding_series: pd.Series,
        leverage: float,
        freq_hours: float = 1.0,
    ) -> pd.Series:
        """
        Map per-8h funding rates onto a OHLCV bar series.

        Parameters
        ----------
        funding_series : Series of per-interval funding rates (8h cadence)
        leverage       : position leverage
        freq_hours     : OHLCV bar size in hours

        Returns
        -------
        Series of per-bar funding cost (same length as funding_series after
        resampling to freq_hours cadence via forward-fill).
        """
        cost_per_bar = funding_series * leverage * (freq_hours / HOURS_PER_FUNDING)
        return cost_per_bar

    def should_reduce_size(
        self,
        funding_series: pd.Series,
        threshold_annual: float = 0.05,
        window: int = 3,
    ) -> bool:
        """
        Return True if recent funding cost (annualised, leverage=1) exceeds threshold.
        Uses mean of last *window* intervals.
        """
        if funding_series.empty:
            return False
        recent = funding_series.tail(window).mean()
        annual = annualized_funding_cost(abs(recent))
        if annual > threshold_annual:
            logger.warning(
                f"Funding drag {annual*100:.2f}%/yr > threshold "
                f"{threshold_annual*100:.0f}% -> reduce size"
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _make_exchange(self):  # type: ignore[return]
        exchange_cls = getattr(ccxt_async, self.exchange_id)
        return exchange_cls({"options": {"defaultType": "future"}})
