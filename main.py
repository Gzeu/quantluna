#!/usr/bin/env python
"""
main.py  —  QuantLuna Entry Point

Usage:
    python main.py --help
    python main.py paper --pair BTCUSDT ETHUSDT
    python main.py live  --pair BTCUSDT ETHUSDT
    python main.py live  --pair BTCUSDT ETHUSDT --yes   # skip confirmation
    python main.py backtest --pair BTCUSDT ETHUSDT --days 180
    python main.py optimize --pair BTCUSDT ETHUSDT --trials 150
    python main.py scan
    python main.py dashboard
    python main.py health --pair BTCUSDT ETHUSDT

Environment variables (override any --arg):
    QUANTLUNA_EXCHANGE      default exchange (bybit / binance / okx)
    TELEGRAM_BOT_TOKEN      Telegram bot token for notifications
    TELEGRAM_CHAT_ID        Telegram chat id
    QUANTLUNA_LOG_LEVEL     log level (DEBUG / INFO / WARNING)
"""

import argparse
import asyncio
import logging
import os
import sys

try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    _VERSION = _pkg_version("quantluna")
except Exception:
    _VERSION = "0.1.0"

_EXCHANGES = ["bybit", "binance", "okx"]


def _default_exchange() -> str:
    return os.environ.get("QUANTLUNA_EXCHANGE", "bybit")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _banner() -> None:
    print(f"""
  ___                    _   _
 / _ \ _   _  __ _ _ __ | |_| |    _   _ _ __   __ _
| | | | | | |/ _` | '_ \| __| |   | | | | '_ \ / _` |
| |_| | |_| | (_| | | | | |_| |___| |_| | | | | (_| |
 \__\_\\__,_|\__,_|_| |_|\__|______\__,_|_| |_|\__,_|

  Adaptive Kalman Filter Pairs Trading Engine  v{_VERSION}
  https://github.com/Gzeu/quantluna
""")


def _confirm_live(yes: bool) -> None:
    """Requires explicit user confirmation before placing real orders."""
    if yes:
        return
    print(
        "\n\u26a0\ufe0f  LIVE MODE \u2014 REAL ORDERS WILL BE PLACED ON THE EXCHANGE!"
        "\n   Press Ctrl+C now to abort.\n"
    )
    answer = input("Type 'YES' to confirm: ").strip()
    if answer != "YES":
        print("Aborted.")
        sys.exit(0)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="quantluna",
        description="Adaptive Kalman Filter Pairs Trading Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {_VERSION}")
    ap.add_argument(
        "--log-level",
        default=os.environ.get("QUANTLUNA_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    sub = ap.add_subparsers(dest="command", required=True)

    # ---- shared pair / exchange args ----
    def _add_pair_exchange(p):
        p.add_argument("--pair", nargs=2, metavar=("SYM_Y", "SYM_X"), required=True)
        p.add_argument("--exchange", default=_default_exchange(), choices=_EXCHANGES)

    # paper
    pp = sub.add_parser("paper", help="Paper trading (simulated fills)")
    _add_pair_exchange(pp)
    pp.add_argument("--capital", type=float, default=10_000.0)
    pp.add_argument("--slippage", type=float, default=0.0005)
    pp.add_argument("--latency", type=float, default=0.05)
    pp.add_argument("--params", default=None, help="Path to params JSON/YAML")
    pp.add_argument("--telegram-token", dest="telegram_token",
                    default=os.environ.get("TELEGRAM_BOT_TOKEN"))
    pp.add_argument("--telegram-chat", dest="telegram_chat",
                    default=os.environ.get("TELEGRAM_CHAT_ID"))
    pp.add_argument("--health-check", dest="health_check", action="store_true")

    # live
    lp = sub.add_parser("live", help="Live trading")
    _add_pair_exchange(lp)
    lp.add_argument("--mode", default="live", choices=["live", "paper"])
    lp.add_argument("--yes", action="store_true", help="Skip live-mode confirmation")
    lp.add_argument("--checkpoint", default="checkpoints/live_state.pkl")
    lp.add_argument("--params", default=None)
    lp.add_argument("--skip-orphan-scan", dest="skip_orphan_scan", action="store_true")

    # backtest
    bp = sub.add_parser("backtest", help="Historical backtest")
    _add_pair_exchange(bp)
    bp.add_argument("--days", type=int, default=90)
    bp.add_argument("--timeframe", default="1h")
    bp.add_argument("--capital", type=float, default=10_000.0)
    bp.add_argument("--params", default=None)

    # optimize
    op = sub.add_parser("optimize", help="Optuna parameter optimisation")
    _add_pair_exchange(op)
    op.add_argument("--trials", type=int, default=100)
    op.add_argument("--jobs", type=int, default=1)
    op.add_argument("--objective", default="sharpe")
    op.add_argument("--output", default="results/best_params.json")
    op.add_argument("--storage", default=None, help="Optuna storage URL")

    # scan
    sp = sub.add_parser("scan", help="Scan for cointegrated pairs")
    sp.add_argument("--exchange", default=_default_exchange(), choices=_EXCHANGES)
    sp.add_argument("--top", type=int, default=20)
    sp.add_argument("--days", type=int, default=30)
    sp.add_argument("--output", default=None)

    # dashboard
    dp = sub.add_parser("dashboard", help="Launch monitoring dashboard")
    dp.add_argument("--host", default="0.0.0.0")
    dp.add_argument("--port", type=int, default=8080)
    dp.add_argument("--dev", action="store_true", help="Enable uvicorn --reload")

    # health
    hp = sub.add_parser("health", help="Check connectivity and configuration")
    _add_pair_exchange(hp)
    hp.add_argument("--api-key", dest="api_key", default=None)
    hp.add_argument("--api-secret", dest="api_secret", default=None)

    return ap.parse_args()


# ------------------------------------------------------------------ #
# Async command handlers — direct dispatch, tracebacks preserved
# ------------------------------------------------------------------ #

async def _cmd_paper(args) -> None:
    from scripts.run_paper import main as run_paper
    await run_paper(
        pair=args.pair,
        exchange=args.exchange,
        capital=args.capital,
        slippage=args.slippage,
        latency=args.latency,
        params_file=getattr(args, "params", None),
        telegram_token=getattr(args, "telegram_token", None),
        telegram_chat=getattr(args, "telegram_chat", None),
        health_check=getattr(args, "health_check", False),
    )


async def _cmd_live(args) -> None:
    if args.mode == "live":
        _confirm_live(args.yes)
    from scripts.run_live import main as run_live
    await run_live(
        pair=args.pair,
        exchange=args.exchange,
        mode=args.mode,
        checkpoint=args.checkpoint,
        params_file=getattr(args, "params", None),
        skip_orphan_scan=getattr(args, "skip_orphan_scan", False),
    )


async def _cmd_backtest(args) -> None:
    from scripts.run_backtest import main as run_backtest
    await run_backtest(
        pair=args.pair,
        exchange=args.exchange,
        days=args.days,
        timeframe=args.timeframe,
        capital=args.capital,
        params_file=getattr(args, "params", None),
    )


async def _cmd_optimize(args) -> None:
    from scripts.optimize_params import main as run_optimize
    await run_optimize(
        pair=args.pair,
        exchange=args.exchange,
        trials=args.trials,
        jobs=args.jobs,
        objective=args.objective,
        output=args.output,
        storage=getattr(args, "storage", None),
    )


async def _cmd_scan(args) -> None:
    from scripts.scan_pairs import main as run_scan
    await run_scan(
        exchange=args.exchange,
        top=args.top,
        days=args.days,
        output=getattr(args, "output", None),
    )


async def _cmd_health(args) -> None:
    from execution.health_check import HealthCheck, HealthConfig
    check = HealthCheck(HealthConfig(
        exchange=args.exchange,
        sym_y=args.pair[0],
        sym_x=args.pair[1],
        api_key=getattr(args, "api_key", None),
        api_secret=getattr(args, "api_secret", None),
    ))
    report = await check.run()
    report.print_report()
    sys.exit(0 if report.all_passed else 1)


def _cmd_dashboard(args) -> None:
    import uvicorn
    uvicorn.run(
        "dashboard.server:app",
        host=args.host,
        port=args.port,
        reload=args.dev,
    )


_ASYNC_COMMANDS = {
    "paper":    _cmd_paper,
    "live":     _cmd_live,
    "backtest": _cmd_backtest,
    "optimize": _cmd_optimize,
    "scan":     _cmd_scan,
    "health":   _cmd_health,
}


def main() -> None:
    _banner()
    args = parse_args()
    _setup_logging(args.log_level)

    if args.command in _ASYNC_COMMANDS:
        asyncio.run(_ASYNC_COMMANDS[args.command](args))
    elif args.command == "dashboard":
        _cmd_dashboard(args)
    else:
        raise ValueError(f"Unknown command: {args.command!r}")


if __name__ == "__main__":
    main()
