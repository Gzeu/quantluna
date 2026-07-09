"""
scripts/scan_pairs.py  —  Cointegration pair scanner.

Can be run standalone::

    python scripts/scan_pairs.py --exchange bybit --top 20

Or called from main.py dispatch::

    from scripts.scan_pairs import main
    await main(exchange="bybit", top=20, days=30)

Previously this called asyncio.run(loader.fetch_multiple(...)) internally,
which would deadlock when called from inside an already-running event loop
(i.e. when dispatched from main.py's asyncio.run()). Fixed by awaiting
directly instead.
"""
from __future__ import annotations

import argparse
import logging
import os

from rich.console import Console
from rich.table import Table

from data.loader import DataLoader
from core.pair_selector import PairSelector

logger = logging.getLogger(__name__)

DEFAULT_UNIVERSE = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "BNB/USDT:USDT", "XRP/USDT:USDT", "ADA/USDT:USDT",
    "AVAX/USDT:USDT", "DOT/USDT:USDT", "MATIC/USDT:USDT", "LINK/USDT:USDT",
]


async def main(
    exchange: str | None = None,
    timeframe: str = "1h",
    days: int | None = None,
    top: int | None = None,
    output: str | None = None,
    **_,
) -> None:
    """
    Pair scanner entry point callable from main.py dispatch.
    Awaits DataLoader directly instead of calling asyncio.run() to avoid
    re-entrant event-loop deadlock when dispatched from main.py.
    """
    if exchange is None:
        ap = argparse.ArgumentParser(prog="scan_pairs")
        ap.add_argument("--exchange", default=os.environ.get("QUANTLUNA_EXCHANGE", "bybit"))
        ap.add_argument("--timeframe", default="1h")
        ap.add_argument("--days", type=int, default=30)
        ap.add_argument("--top", type=int, default=20)
        ap.add_argument("--output", default=None)
        args = ap.parse_args()
        exchange  = args.exchange
        timeframe = args.timeframe
        days      = args.days
        top       = args.top
        output    = args.output

    days = days or 30
    top  = top  or 20

    logger.info("Scanning pairs on %s [%s] %dd history", exchange, timeframe, days)

    loader = DataLoader(exchange_id=exchange, timeframe=timeframe)
    limit  = days * 24

    # await directly — no asyncio.run() to avoid deadlock inside running loop
    prices = await loader.fetch_multiple(DEFAULT_UNIVERSE, limit=limit)
    if prices.empty:
        logger.error("No data fetched")
        return

    symbols  = list(prices.columns)
    selector = PairSelector(universe=symbols)
    results  = selector.scan(prices, log_prices=True)

    if results.empty:
        logger.warning("No cointegrated pairs found")
        return

    console = Console()
    table   = Table(title=f"QuantLuna — Top Cointegrated Pairs ({exchange})")
    for col in ["pair", "adf_pvalue", "eg_pvalue", "half_life_hours", "hurst", "static_beta"]:
        table.add_column(col, style="cyan" if col == "pair" else "white")

    for _, row in results.head(top).iterrows():
        table.add_row(*[str(round(row[c], 4)) if isinstance(row[c], float) else str(row[c])
                        for c in ["pair", "adf_pvalue", "eg_pvalue",
                                  "half_life_hours", "hurst", "static_beta"]])

    console.print(table)

    if output:
        results.head(top).to_csv(output, index=False)
        logger.info("Saved scan results → %s", output)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
