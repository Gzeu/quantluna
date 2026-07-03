#!/usr/bin/env python
"""
main.py  —  QuantLuna Entry Point

Starting point clar pentru oricine clonează repo-ul.

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
import subprocess
import sys
from pathlib import Path

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).parent))

# ------------------------------------------------------------------ #
# Version
# ------------------------------------------------------------------ #
_VERSION = "0.1.0"  # keep in sync with pyproject.toml

_EXCHANGES = ["bybit", "binance", "okx"]


def _default_exchange() -> str:
    """Reads QUANTLUNA_EXCHANGE env var, falls back to 'bybit'."""
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


# ------------------------------------------------------------------ #
# Live trade confirmation (safety gate)
# ------------------------------------------------------------------ #

def _confirm_live(yes: bool) -> None:
    """
    Requires explicit confirmation before placing real orders.
    Skipped when --yes flag is passed (useful for automated / cron deployments).
    """
    if yes:
        return
    print(
        "\n⚠️  LIVE MODE — REAL ORDERS WILL BE PLACED ON THE EXCHANGE!"
        "\n   Press Ctrl+C now to abort.\n"
    )
    answer = input("Type 'YES' to confirm: ").strip()
    if answer != "YES":
        print("Aborted.")
        sys.exit(0)


# ------------------------------------------------------------------ #
# Argument parser
# ------------------------------------------------------------------ #

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="quantluna",
        description="QuantLuna — Adaptive Kalman Filter Pairs Trading Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py paper --pair BTCUSDT ETHUSDT --capital 10000
  python main.py live  --pair BTCUSDT ETHUSDT --yes
  python main.py backtest --pair BTCUSDT ETHUSDT --days 365
  python main.py optimize --pair BTCUSDT ETHUSDT --trials 200 --jobs 4
  python main.py scan --exchange bybit --top 20
  python main.py dashboard
  python main.py dashboard --dev
  python main.py health --pair BTCUSDT ETHUSDT --exchange bybit

Environment variables:
  QUANTLUNA_EXCHANGE    default exchange  (bybit / binance / okx)
  TELEGRAM_BOT_TOKEN    Telegram bot token
  TELEGRAM_CHAT_ID      Telegram chat id
  QUANTLUNA_LOG_LEVEL   DEBUG / INFO / WARNING  (default: INFO)
"""
    )
    p.add_argument("--version", action="version", version=f"QuantLuna {_VERSION}")
    p.add_argument(
        "--log-level",
        default=os.environ.get("QUANTLUNA_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (env: QUANTLUNA_LOG_LEVEL)",
    )

    sub = p.add_subparsers(dest="command", required=True)

    # ---- paper ---------------------------------------------------- #
    paper = sub.add_parser("paper", help="Run paper trader (safe, no real orders)")
    paper.add_argument("--pair", nargs=2, default=["BTCUSDT", "ETHUSDT"], metavar=("Y", "X"))
    paper.add_argument("--exchange", default=_default_exchange(),
                       choices=_EXCHANGES, help="Exchange (env: QUANTLUNA_EXCHANGE)")
    paper.add_argument("--capital", type=float, default=10_000.0)
    paper.add_argument("--slippage", type=float, default=0.0005,
                       help="Slippage pct for simulation (default 0.05%%)")
    paper.add_argument("--latency", type=float, default=30.0,
                       help="Fill latency simulation in ms")
    paper.add_argument("--params", default=None, help="JSON file from optimizer")
    paper.add_argument(
        "--telegram-token",
        default=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        help="Telegram bot token (env: TELEGRAM_BOT_TOKEN)",
    )
    paper.add_argument(
        "--telegram-chat",
        default=os.environ.get("TELEGRAM_CHAT_ID", ""),
        help="Telegram chat id (env: TELEGRAM_CHAT_ID)",
    )
    paper.add_argument("--health-check", action="store_true")

    # ---- live ----------------------------------------------------- #
    live = sub.add_parser("live", help="Run live trader (REAL orders)")
    live.add_argument("--pair", nargs=2, default=["BTCUSDT", "ETHUSDT"], metavar=("Y", "X"))
    live.add_argument("--exchange", default=_default_exchange(),
                      choices=_EXCHANGES, help="Exchange (env: QUANTLUNA_EXCHANGE)")
    live.add_argument("--params", default=None, help="JSON params file from optimizer")
    live.add_argument("--mode", default="live", choices=["live", "testnet"],
                      help="'testnet' to use exchange sandbox")
    live.add_argument("--checkpoint", default="position_checkpoint.db",
                      help="Path to SQLite checkpoint file")
    live.add_argument("--skip-orphan-scan", action="store_true",
                      help="Skip orphan position scan at startup")
    live.add_argument("--yes", "-y", action="store_true",
                      help="Skip interactive confirmation (for automation / cron)")

    # ---- backtest ------------------------------------------------- #
    bt = sub.add_parser("backtest", help="Run historical backtest")
    bt.add_argument("--pair", nargs=2, default=["BTCUSDT", "ETHUSDT"], metavar=("Y", "X"))
    bt.add_argument("--exchange", default=_default_exchange(),
                    choices=_EXCHANGES, help="Exchange (env: QUANTLUNA_EXCHANGE)")
    bt.add_argument("--days", type=int, default=365)
    bt.add_argument("--timeframe", default="1h",
                    choices=["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
                    help="OHLCV timeframe")
    bt.add_argument("--capital", type=float, default=10_000.0)
    bt.add_argument("--params", default=None, help="JSON params file from optimizer")

    # ---- optimize ------------------------------------------------- #
    opt = sub.add_parser("optimize", help="Run Optuna hyperparameter optimization")
    opt.add_argument("--pair", nargs=2, default=["BTCUSDT", "ETHUSDT"], metavar=("Y", "X"))
    opt.add_argument("--exchange", default=_default_exchange(), choices=_EXCHANGES)
    opt.add_argument("--trials", type=int, default=150)
    opt.add_argument("--jobs", type=int, default=1)
    opt.add_argument("--objective", default="sharpe",
                     choices=["sharpe", "sortino", "calmar", "profit_factor"])
    opt.add_argument("--output", default="best_params.json")
    opt.add_argument("--storage", default=None,
                     help="Optuna storage URL (e.g. sqlite:///study.db)")

    # ---- scan ----------------------------------------------------- #
    scan = sub.add_parser("scan", help="Scan exchange for cointegrated pairs")
    scan.add_argument("--exchange", default=_default_exchange(), choices=_EXCHANGES)
    scan.add_argument("--top", type=int, default=20,
                      help="Number of top-volume symbols to consider")
    scan.add_argument("--days", type=int, default=60,
                      help="Lookback days for cointegration test")
    scan.add_argument("--output", default=None,
                      help="Optional JSON file to save ranked pairs")

    # ---- dashboard ------------------------------------------------ #
    dash = sub.add_parser("dashboard", help="Start monitoring dashboard (http://localhost:8000)")
    dash.add_argument("--port", type=int, default=8000)
    dash.add_argument("--host", default="0.0.0.0")
    dash.add_argument("--dev", action="store_true",
                      help="Enable auto-reload (development only — do NOT use in production)")

    # ---- health --------------------------------------------------- #
    health = sub.add_parser("health", help="Run pre-flight health check")
    health.add_argument("--pair", nargs=2, default=["BTCUSDT", "ETHUSDT"], metavar=("Y", "X"))
    health.add_argument("--exchange", default=_default_exchange(), choices=_EXCHANGES)
    health.add_argument("--api-key", default="", help="API key (or use .env)")
    health.add_argument("--api-secret", default="", help="API secret (or use .env)")

    return p.parse_args()


# ------------------------------------------------------------------ #
# Async command handlers
# ------------------------------------------------------------------ #

async def cmd_health(args) -> None:
    from execution.health_check import HealthCheck, HealthConfig
    check = HealthCheck(HealthConfig(
        exchange=args.exchange,
        sym_y=args.pair[0],
        sym_x=args.pair[1],
        api_key=args.api_key,
        api_secret=args.api_secret,
    ))
    report = await check.run()
    report.print_report()
    sys.exit(0 if report.all_passed else 1)


# ------------------------------------------------------------------ #
# Subprocess helpers
# ------------------------------------------------------------------ #

def _run_script(cmd: list[str]) -> None:
    """Run a subprocess command; re-raises CalledProcessError on failure."""
    logging.getLogger("quantluna").debug("Running: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


# ------------------------------------------------------------------ #
# Entry point
# ------------------------------------------------------------------ #

def main() -> None:
    _banner()
    args = parse_args()
    _setup_logging(args.log_level)

    if args.command == "paper":
        cmd = [
            sys.executable, "scripts/run_paper.py",
            "--pair", *args.pair,
            "--exchange", args.exchange,
            "--capital", str(args.capital),
            "--slippage", str(args.slippage),
            "--latency", str(args.latency),
        ]
        if args.params:
            cmd += ["--params", args.params]
        # Telegram: prefer CLI arg, fall back is already baked in by default= above
        if args.telegram_token:
            cmd += ["--telegram-token", args.telegram_token]
        if args.telegram_chat:
            cmd += ["--telegram-chat", args.telegram_chat]
        if args.health_check:
            cmd += ["--health-check"]
        _run_script(cmd)

    elif args.command == "live":
        if args.mode == "live":
            _confirm_live(args.yes)
        cmd = [
            sys.executable, "scripts/run_live.py",
            "--pair", *args.pair,
            "--exchange", args.exchange,
            "--mode", args.mode,
            "--checkpoint", args.checkpoint,
        ]
        if args.params:
            cmd += ["--params", args.params]
        if args.skip_orphan_scan:
            cmd += ["--skip-orphan-scan"]
        _run_script(cmd)

    elif args.command == "backtest":
        cmd = [
            sys.executable, "scripts/run_backtest.py",
            "--pair", *args.pair,
            "--exchange", args.exchange,
            "--days", str(args.days),
            "--timeframe", args.timeframe,
            "--capital", str(args.capital),
        ]
        if args.params:
            cmd += ["--params", args.params]
        _run_script(cmd)

    elif args.command == "optimize":
        cmd = [
            sys.executable, "scripts/optimize_params.py",
            "--pair", *args.pair,
            "--exchange", args.exchange,
            "--trials", str(args.trials),
            "--jobs", str(args.jobs),
            "--objective", args.objective,
            "--output", args.output,
        ]
        if args.storage:
            cmd += ["--storage", args.storage]
        _run_script(cmd)

    elif args.command == "scan":
        cmd = [
            sys.executable, "scripts/scan_pairs.py",
            "--exchange", args.exchange,
            "--top", str(args.top),
            "--days", str(args.days),
        ]
        if args.output:
            cmd += ["--output", args.output]
        _run_script(cmd)

    elif args.command == "dashboard":
        cmd = [
            sys.executable, "-m", "uvicorn",
            "dashboard.server:app",
            "--host", args.host,
            "--port", str(args.port),
        ]
        if args.dev:
            cmd.append("--reload")
        _run_script(cmd)

    elif args.command == "health":
        asyncio.run(cmd_health(args))


if __name__ == "__main__":
    main()
