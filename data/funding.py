"""
QuantLuna — Funding Rate Fetcher

Fetches historical and current funding rates from exchanges.
Critical for perpetual futures pairs trading — funding can be a
significant drag or tailwind on market-neutral positions.
"""
import asyncio
from typing import Optional, Dict
from pathlib import Path
import pandas as pd
from loguru import logger

try:
    import ccxt.async_support as ccxt_async
except ImportError:
    ccxt_async = None


class FundingRateFetcher:
    """
    Fetch funding rate history for perpetual futures.

    Parameters
    ----------
    exchange_id : ccxt exchange id (binance, bybit)
    """

    def __init__(self, exchange_id: str = "binance"):
        self.exchange_id = exchange_id

    async def fetch_current(self, symbol: str) -> float:
        """
        Returns current funding rate for symbol (e.g., 'BTC/USDT:USDT').
        """
        if ccxt_async is None:
            return 0.0

        exchange_class = getattr(ccxt_async, self.exchange_id)
        exchange = exchange_class({"options": {"defaultType": "future"}})
        try:
            info = await exchange.fetch_funding_rate(symbol)
            rate = float(info.get("fundingRate", 0.0))
            logger.debug(f"Funding rate {symbol}: {rate*100:.4f}%")
            return rate
        except Exception as e:
            logger.warning(f"Funding rate fetch failed {symbol}: {e}")
            return 0.0
        finally:
            await exchange.close()

    async def fetch_history(
        self,
        symbol: str,
        since: Optional[int] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """Returns historical funding rate DataFrame indexed by timestamp."""
        if ccxt_async is None:
            return pd.DataFrame()

        exchange_class = getattr(ccxt_async, self.exchange_id)
        exchange = exchange_class({"options": {"defaultType": "future"}})
        try:
            history = await exchange.fetch_funding_rate_history(symbol, since=since, limit=limit)
            df = pd.DataFrame(history)[["timestamp", "fundingRate"]]
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp").rename(columns={"fundingRate": "funding_rate"})
            logger.info(f"Funding history {symbol}: {len(df)} records")
            return df
        except Exception as e:
            logger.warning(f"Funding history failed {symbol}: {e}")
            return pd.DataFrame()
        finally:
            await exchange.close()

    async def estimate_annual_cost(
        self, symbol: str, capital_usdt: float
    ) -> Dict[str, float]:
        """Estimate annual funding cost for a position."""
        rate = await self.fetch_current(symbol)
        payments_per_year = 3 * 365  # 3x per day
        annual_rate = rate * payments_per_year
        annual_cost = abs(annual_rate) * capital_usdt
        return {
            "funding_rate_8h": rate,
            "annual_rate": annual_rate,
            "annual_cost_usdt": annual_cost,
            "annual_cost_pct": abs(annual_rate),
        }
