"""
QuantLuna — Bybit Live Run (Sprint 21)

Entry point pentru trading LIVE real pe Bybit.

  >>> python scripts/run_bybit_live.py --pair BTC/ETH --interval 5 --dry-run
  >>> python scripts/run_bybit_live.py --pair BTC/ETH --interval 5  # REAL ORDERS

Variabile .env necesare:
  BYBIT_API_KEY
  BYBIT_API_SECRET
  DRY_RUN=false  (dacă vrei ordine reale)
  SLACK_WEBHOOK_URL  (optional)
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID  (optional)

S A F E T Y:
  - Default: DRY_RUN=true — nu trimite ordine reale
  - --dry-run flag suprascrie orice setare din .env
  - CircuitBreaker activ: max 3 pierderi consecutive sau 5% drawdown
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loguru import logger


def setup_logging(debug: bool = False) -> None:
    logger.remove()
    level = "DEBUG" if debug else "INFO"
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    )
    logger.add(
        "logs/bybit_live_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",
        retention="14 days",
        compression="gz",
    )


async def main(args) -> None:
    from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig

    # Load config from env, then override with CLI flags
    cfg = BybitLiveRunnerConfig.from_env()

    # Parse pair (e.g. "BTC/ETH" -> "BTCUSDT", "ETHUSDT")
    if args.pair:
        parts = args.pair.upper().replace("-", "/").split("/")
        if len(parts) == 2:
            cfg.symbol_y = parts[0] + "USDT" if not parts[0].endswith("USDT") else parts[0]
            cfg.symbol_x = parts[1] + "USDT" if not parts[1].endswith("USDT") else parts[1]

    if args.interval:    cfg.interval      = args.interval
    if args.entry_z:     cfg.entry_zscore  = args.entry_z
    if args.exit_z:      cfg.exit_zscore   = args.exit_z
    if args.qty:         cfg.base_qty      = args.qty
    if args.warmup:      cfg.warmup_bars   = args.warmup
    if args.dry_run:     cfg.dry_run       = True
    if args.live:        cfg.dry_run       = False

    logger.info(
        f"Starting BybitLiveRunner: "
        f"{cfg.symbol_y}/{cfg.symbol_x} "
        f"interval={cfg.interval}m "
        f"dry_run={cfg.dry_run} "
        f"entry_z={cfg.entry_zscore} exit_z={cfg.exit_zscore}"
    )

    # Safety confirmation for live mode
    if not cfg.dry_run:
        print("\n" + "!" * 60)
        print("  WARNING: LIVE MODE ENABLED - REAL ORDERS WILL BE PLACED")
        print(f"  Pair:  {cfg.symbol_y} / {cfg.symbol_x}")
        print(f"  Qty:   {cfg.base_qty}")
        print(f"  Entry: z > {cfg.entry_zscore}")
        print("!" * 60)
        if not args.yes:
            answer = input("  Confirm live trading? [yes/no]: ")
            if answer.strip().lower() != "yes":
                print("  Aborted.")
                return

    # Build WS feed
    ws_feed = None
    try:
        from execution.bybit_ws_bars import BybitWsBarsAdapter
        try:
            from execution.bybit_ws_feed import BybitWsFeed
            ws_raw = BybitWsFeed(
                api_key=os.getenv("BYBIT_API_KEY", ""),
                api_secret=os.getenv("BYBIT_API_SECRET", ""),
                testnet=cfg.dry_run,
            )
            ws_feed = BybitWsBarsAdapter(ws_feed=ws_raw, interval=cfg.interval)
            logger.info("run_bybit_live: BybitWsFeed + BybitWsBarsAdapter ready")
        except Exception as exc:
            logger.warning(f"run_bybit_live: BybitWsFeed not available ({exc}), using mock stream")
            ws_feed = BybitWsBarsAdapter(ws_feed=None, interval=cfg.interval)
    except Exception as exc:
        logger.warning(f"run_bybit_live: ws adapter not available: {exc}")

    runner = BybitLiveRunner(cfg=cfg, ws_feed=ws_feed)

    # Graceful SIGINT / SIGTERM
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(runner.stop()))

    await runner.start()
    logger.info(f"run_bybit_live: done | {runner.status()}")


def cli() -> None:
    p = argparse.ArgumentParser(
        description="QuantLuna — Bybit Live Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_bybit_live.py --pair BTC/ETH --dry-run
  python scripts/run_bybit_live.py --pair BTC/ETH --interval 15 --live --yes
  DRY_RUN=false python scripts/run_bybit_live.py --pair SOL/BTC
"""
    )
    p.add_argument("--pair",     type=str,   default="BTC/ETH",  help="Trading pair (e.g. BTC/ETH)")
    p.add_argument("--interval", type=str,   default=None,       help="Kline interval: 1,3,5,15,60,D")
    p.add_argument("--entry-z",  type=float, default=None,       dest="entry_z")
    p.add_argument("--exit-z",   type=float, default=None,       dest="exit_z")
    p.add_argument("--qty",      type=float, default=None,       help="Base order qty")
    p.add_argument("--warmup",   type=int,   default=None,       help="Warmup bars before trading")
    p.add_argument("--dry-run",  action="store_true",            help="Paper mode (no real orders)")
    p.add_argument("--live",     action="store_true",            help="Live mode (real orders)")
    p.add_argument("--yes",      action="store_true",            help="Skip live mode confirmation")
    p.add_argument("--debug",    action="store_true",            help="Debug logging")
    args = p.parse_args()
    setup_logging(args.debug)
    asyncio.run(main(args))


if __name__ == "__main__":
    cli()
