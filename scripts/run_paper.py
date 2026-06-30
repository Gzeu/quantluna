#!/usr/bin/env python
"""
scripts/run_paper.py  —  QuantLuna Paper Trading Runner

Sprint 12 — CLI dedicat pentru paper trading cu toate opțiunile Sprint 11:

Usage:
    python scripts/run_paper.py \\
        --pair BTCUSDT ETHUSDT \\
        --exchange bybit \\
        --capital 10000 \\
        --slippage 0.001 \\
        --latency 50 \\
        --telegram-token YOUR_TOKEN \\
        --telegram-chat YOUR_CHAT_ID

    # Cu params din optimizer:
    python scripts/run_paper.py \\
        --pair BTCUSDT ETHUSDT \\
        --params best_params.json
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("paper")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="QuantLuna Paper Trader",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--pair", nargs=2, default=["BTCUSDT", "ETHUSDT"], metavar=("SYM_Y", "SYM_X"))
    p.add_argument("--exchange", default="bybit")
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument("--slippage", type=float, default=0.0005, help="Slippage pct (0.0005 = 0.05%)")
    p.add_argument("--latency", type=float, default=30.0, help="Fill latency simulation in ms")
    p.add_argument("--warmup", type=int, default=30)
    p.add_argument("--zscore-entry", type=float, default=2.0)
    p.add_argument("--zscore-exit", type=float, default=0.5)
    p.add_argument("--delta", type=float, default=1e-4)
    p.add_argument("--kelly", type=float, default=0.25)
    p.add_argument("--max-dd", type=float, default=0.10)
    p.add_argument("--params", default=None, help="JSON file with optimizer output (overrides params)")
    p.add_argument("--telegram-token", default="", help="Telegram bot token")
    p.add_argument("--telegram-chat", default="", help="Telegram chat ID")
    p.add_argument("--db", default="paper_trades.db")
    p.add_argument("--health-check", action="store_true", help="Run health check before starting")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    sym_y, sym_x = args.pair

    # Load params from optimizer if provided
    params = {}
    if args.params:
        with open(args.params) as f:
            data = json.load(f)
            params = data.get("params", {})
        logger.info(f"Loaded params from {args.params}: {params}")

    delta = params.get("delta", args.delta)
    zscore_entry = params.get("zscore_entry", args.zscore_entry)
    zscore_exit = params.get("zscore_exit", args.zscore_exit)
    kelly_fraction = params.get("kelly_fraction", args.kelly)
    min_warmup_bars = params.get("min_warmup_bars", args.warmup)

    logger.info(
        f"PaperTrader | pair={sym_y}/{sym_x} | exchange={args.exchange} "
        f"| capital={args.capital:.0f} | slippage={args.slippage:.4%} "
        f"| latency={args.latency:.0f}ms"
    )

    # Optional health check
    if args.health_check:
        from execution.health_check import HealthCheck, HealthConfig
        check = HealthCheck(HealthConfig(
            exchange=args.exchange,
            sym_y=sym_y,
            sym_x=sym_x,
        ))
        report = await check.run()
        report.print_report()
        if not report.all_passed:
            logger.error("Health check failed — aborting paper trader")
            sys.exit(1)

    # Build signal generator
    from core.kalman_filter import KalmanFilter
    from strategy.signal import SignalGenerator

    kf = KalmanFilter(delta=delta)
    signal_gen = SignalGenerator(
        kalman=kf,
        zscore_entry=zscore_entry,
        zscore_exit=zscore_exit,
    )

    # Notifier
    notifier_cfg = None
    if args.telegram_token and args.telegram_chat:
        from notifications.telegram_notifier import NotifierConfig
        notifier_cfg = NotifierConfig(
            bot_token=args.telegram_token,
            chat_id=args.telegram_chat,
        )
        logger.info("Telegram notifications enabled")

    # Risk
    from risk import PortfolioAllocator, AllocatorConfig
    from risk.kelly import KellyConfig
    from risk.drawdown_controller import DDConfig

    allocator = PortfolioAllocator(AllocatorConfig(
        capital_usd=args.capital,
        kelly=KellyConfig(kelly_fraction=kelly_fraction),
        drawdown=DDConfig(portfolio_hard_dd=args.max_dd),
    ))

    # Paper trader
    from execution.paper_trader import PaperTrader, PaperConfig

    trader = PaperTrader(
        config=PaperConfig(
            sym_y=sym_y,
            sym_x=sym_x,
            exchange=args.exchange,
            capital_usdt=args.capital,
            slippage_pct=args.slippage,
            latency_ms=args.latency,
            min_warmup_bars=int(min_warmup_bars),
            notifier_config=notifier_cfg,
            trade_db_path=args.db,
        ),
        signal_gen=signal_gen,
        allocator=allocator,
    )

    logger.info("Starting paper trader... (Ctrl+C to stop)")
    try:
        await trader.run()
    except KeyboardInterrupt:
        logger.info("\nStopped by user")
        summary = trader.summary()
        logger.info(f"\n=== Paper Trading Summary ===")
        for k, v in summary.items():
            if isinstance(v, float):
                logger.info(f"  {k}: {v:.4f}")
            else:
                logger.info(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
