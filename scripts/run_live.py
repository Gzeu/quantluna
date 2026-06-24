"""
QuantLuna — Live Trading CLI

Usage:
  python scripts/run_live.py --pair ETHUSDT BTCUSDT --mode paper
  python scripts/run_live.py --pair ETHUSDT BTCUSDT --mode live
"""
import asyncio
import click
import os
from loguru import logger

from config.settings import QuantLunaConfig
from execution.live_trader import LiveTrader


@click.command()
@click.option("--pair", nargs=2, required=True, help="Two symbols")
@click.option("--mode", default="paper", type=click.Choice(["paper", "live"]), help="Trading mode")
@click.option("--exchange", default="binance")
def main(pair, mode, exchange):
    sym_y, sym_x = pair
    sym_y_fmt = sym_y.replace("USDT", "/USDT:USDT")
    sym_x_fmt = sym_x.replace("USDT", "/USDT:USDT")

    cfg = QuantLunaConfig()
    cfg.trading_mode = mode
    cfg.execution.exchange = exchange

    if mode == "live":
        logger.warning("LIVE MODE — Real orders will be placed!")
        confirm = input("Type 'YES' to confirm: ")
        if confirm != "YES":
            logger.info("Aborted")
            return

    trader = LiveTrader(sym_y=sym_y_fmt, sym_x=sym_x_fmt, cfg=cfg)
    asyncio.run(trader.run())


if __name__ == "__main__":
    main()
