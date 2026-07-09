"""
scripts/run_paper.py  —  Paper trading entry point.

Can be run standalone::

    python scripts/run_paper.py --pair BTCUSDT ETHUSDT --capital 10000

Or called programmatically from main.py dispatch::

    from scripts.run_paper import main
    await main(pair=["BTCUSDT", "ETHUSDT"], exchange="bybit", capital=10_000)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from config.settings import QuantLunaConfig
from execution.paper_engine import PaperEngine
from execution.health_check import HealthCheck, HealthConfig

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="run_paper")
    ap.add_argument("--pair", nargs=2, metavar=("SYM_Y", "SYM_X"), required=True)
    ap.add_argument("--exchange", default=os.environ.get("QUANTLUNA_EXCHANGE", "bybit"))
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--latency", type=float, default=50.0)
    ap.add_argument("--params", default=None, help="Path to params JSON")
    ap.add_argument("--delta", type=float, default=1e-4)
    ap.add_argument("--zscore-entry", dest="zscore_entry", type=float, default=2.0)
    ap.add_argument("--zscore-exit", dest="zscore_exit", type=float, default=0.5)
    ap.add_argument("--kelly", type=float, default=0.25)
    ap.add_argument("--warmup", type=int, default=100)
    ap.add_argument("--telegram-token", dest="telegram_token",
                    default=os.environ.get("TELEGRAM_BOT_TOKEN"))
    ap.add_argument("--telegram-chat", dest="telegram_chat",
                    default=os.environ.get("TELEGRAM_CHAT_ID"))
    ap.add_argument("--health-check", dest="health_check", action="store_true")
    return ap.parse_args()


async def main(
    pair: list[str] | None = None,
    exchange: str | None = None,
    capital: float | None = None,
    slippage: float | None = None,
    latency: float | None = None,
    params_file: str | None = None,
    telegram_token: str | None = None,
    telegram_chat: str | None = None,
    health_check: bool = False,
    **_,
) -> None:
    """
    Paper-trade entry point callable from main.py dispatch.
    When called with no kwargs (standalone), falls back to parse_args().
    """
    if pair is None:
        # Standalone invocation — read from CLI
        args = parse_args()
        pair          = args.pair
        exchange      = args.exchange
        capital       = args.capital
        slippage      = args.slippage
        latency       = args.latency
        params_file   = args.params
        telegram_token = args.telegram_token
        telegram_chat  = args.telegram_chat
        health_check   = args.health_check
        delta         = args.delta
        zscore_entry  = args.zscore_entry
        zscore_exit   = args.zscore_exit
        kelly         = args.kelly
        warmup        = args.warmup
    else:
        delta        = 1e-4
        zscore_entry = 2.0
        zscore_exit  = 0.5
        kelly        = 0.25
        warmup       = 100

    sym_y, sym_x = pair

    overrides: dict = {}
    if params_file:
        with open(params_file) as f:
            data = __import__("json").load(f)
            overrides = data.get("params", {})
        logger.info("Loaded params from %s: %s", params_file, overrides)

    delta        = overrides.get("delta",        delta)
    zscore_entry = overrides.get("zscore_entry", zscore_entry)
    zscore_exit  = overrides.get("zscore_exit",  zscore_exit)
    kelly        = overrides.get("kelly_fraction", kelly)
    warmup       = overrides.get("min_warmup_bars", warmup)

    cfg = QuantLunaConfig()
    cfg.execution.exchange = exchange or cfg.execution.exchange
    cfg.execution.slippage = slippage if slippage is not None else cfg.execution.slippage
    cfg.execution.latency_ms = int(latency) if latency is not None else cfg.execution.latency_ms
    if telegram_token:
        cfg.notifications.telegram_token = telegram_token
    if telegram_chat:
        cfg.notifications.telegram_chat_id = telegram_chat

    logger.info(
        "PaperTrader | pair=%s/%s | exchange=%s | capital=%.0f",
        sym_y, sym_x, cfg.execution.exchange, capital or 10_000.0,
    )

    if health_check:
        hc = HealthCheck(HealthConfig(
            exchange=cfg.execution.exchange, sym_y=sym_y, sym_x=sym_x,
        ))
        report = await hc.run()
        report.print_report()
        if not report.all_passed:
            raise RuntimeError("Health check failed — aborting paper run")

    engine = PaperEngine(
        sym_y=sym_y, sym_x=sym_x,
        capital_usdt=capital or 10_000.0,
        config=cfg,
        delta=delta,
        zscore_entry=zscore_entry,
        zscore_exit=zscore_exit,
        kelly_fraction=kelly,
        min_warmup_bars=warmup,
    )
    await engine.run()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
