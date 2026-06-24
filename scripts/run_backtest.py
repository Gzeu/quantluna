"""
QuantLuna — Backtest CLI

Usage:
  python scripts/run_backtest.py --pair ETHUSDT BTCUSDT --exchange binance --days 180
  python scripts/run_backtest.py --pair ETHUSDT BTCUSDT --walk-forward
"""
import asyncio
import click
from loguru import logger
import pandas as pd
import numpy as np

from config.settings import QuantLunaConfig
from data.loader import DataLoader
from data.funding import FundingRateFetcher
from core.cointegration import CointegrationTest
from backtest.engine import BacktestEngine
from backtest.walk_forward import WalkForwardValidator
from backtest.analytics import PerformanceAnalytics


@click.command()
@click.option("--pair", nargs=2, required=True, help="Two symbols, e.g. ETHUSDT BTCUSDT")
@click.option("--exchange", default="binance", help="Exchange id")
@click.option("--timeframe", default="1h", help="OHLCV timeframe")
@click.option("--days", default=180, type=int, help="History days")
@click.option("--walk-forward", is_flag=True, default=False, help="Run walk-forward validation")
@click.option("--monte-carlo", is_flag=True, default=False, help="Run Monte Carlo simulation")
def main(pair, exchange, timeframe, days, walk_forward, monte_carlo):
    sym_y, sym_x = pair
    logger.info(f"QuantLuna Backtest: {sym_y} / {sym_x} on {exchange} [{timeframe}] {days}d")

    cfg = QuantLunaConfig()
    cfg.execution.exchange = exchange

    # Fetch data
    loader = DataLoader(exchange_id=exchange, timeframe=timeframe)
    limit = days * 24  # Assumes 1h

    async def fetch_all():
        sym_y_fmt = sym_y.replace("USDT", "/USDT:USDT")
        sym_x_fmt = sym_x.replace("USDT", "/USDT:USDT")
        prices = await loader.fetch_multiple([sym_y_fmt, sym_x_fmt], limit=limit)
        return prices

    prices = asyncio.run(fetch_all())
    if prices.empty or len(prices.columns) < 2:
        logger.error("Failed to fetch price data")
        return

    y = np.log(prices.iloc[:, 0])
    x = np.log(prices.iloc[:, 1])

    # Cointegration check
    logger.info("Running cointegration tests...")
    coint_test = CointegrationTest()
    result = coint_test.run(y, x)
    logger.info(f"Cointegration: {result.verdict}")

    if not result.is_cointegrated:
        logger.warning("Pair does not pass cointegration. Proceed with caution.")

    # Backtest
    if walk_forward:
        validator = WalkForwardValidator(cfg=cfg)
        wf_result = validator.run(y, x)
        if monte_carlo:
            mc = validator.monte_carlo(wf_result["oos_trades"], capital=cfg.risk.max_capital_usdt)
            logger.info(f"Monte Carlo: median_equity={mc['median_final_equity']:.0f}, P(profit)={mc['prob_profit']:.1%}")
        PerformanceAnalytics.print_report(wf_result["combined"])
    else:
        engine = BacktestEngine(cfg=cfg)
        bt_result = engine.run(y, x)
        PerformanceAnalytics.print_report(bt_result["metrics"])


if __name__ == "__main__":
    main()
