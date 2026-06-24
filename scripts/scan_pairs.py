"""
QuantLuna — Pair Scanner CLI

Scans a universe of crypto assets and ranks cointegrated pairs.

Usage:
  python scripts/scan_pairs.py --exchange binance --days 90
"""
import asyncio
import click
import numpy as np
import pandas as pd
from loguru import logger
from rich.console import Console
from rich.table import Table

from data.loader import DataLoader
from strategy.pair_selector import PairSelector


DEFAULT_UNIVERSE = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT",
    "SOL/USDT:USDT", "AVAX/USDT:USDT", "MATIC/USDT:USDT",
    "LINK/USDT:USDT", "DOT/USDT:USDT", "ADA/USDT:USDT",
]


@click.command()
@click.option("--exchange", default="binance")
@click.option("--timeframe", default="1h")
@click.option("--days", default=90, type=int)
@click.option("--top", default=10, type=int, help="Show top N pairs")
def main(exchange, timeframe, days, top):
    logger.info(f"Scanning pairs on {exchange} [{timeframe}] {days}d history")

    loader = DataLoader(exchange_id=exchange, timeframe=timeframe)
    limit = days * 24

    prices = asyncio.run(loader.fetch_multiple(DEFAULT_UNIVERSE, limit=limit))
    if prices.empty:
        logger.error("No data fetched")
        return

    symbols = [c for c in prices.columns]
    selector = PairSelector(universe=symbols)
    results = selector.scan(prices, log_prices=True)

    if results.empty:
        logger.warning("No cointegrated pairs found")
        return

    console = Console()
    table = Table(title=f"QuantLuna — Top Cointegrated Pairs ({exchange})")
    for col in ["pair", "adf_pvalue", "eg_pvalue", "half_life_hours", "hurst", "static_beta"]:
        table.add_column(col, style="cyan" if col == "pair" else "white")

    for _, row in results.head(top).iterrows():
        table.add_row(
            str(row["pair"]),
            f"{row['adf_pvalue']:.4f}",
            f"{row['eg_pvalue']:.4f}",
            f"{row['half_life_hours']:.1f}h" if pd.notna(row['half_life_hours']) else "N/A",
            f"{row['hurst']:.3f}" if pd.notna(row['hurst']) else "N/A",
            f"{row['static_beta']:.4f}",
        )
    console.print(table)


if __name__ == "__main__":
    main()
