#!/usr/bin/env python
"""
main.py  —  QuantLuna Entry Point

Starting point clar pentru oricine clonează repo-ul.

Usage:
    python main.py --help
    python main.py paper --pair BTCUSDT ETHUSDT
    python main.py live  --pair BTCUSDT ETHUSDT
    python main.py backtest --pair BTCUSDT ETHUSDT --days 180
    python main.py optimize --pair BTCUSDT ETHUSDT --trials 150
    python main.py dashboard
    python main.py health --pair BTCUSDT ETHUSDT
"""

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).parent))


def _banner() -> None:
    print("""
  ___                    _   _                       
 / _ \ _   _  __ _ _ __ | |_| |    _   _ _ __   __ _ 
| | | | | | |/ _` | '_ \| __| |   | | | | '_ \ / _` |
| |_| | |_| | (_| | | | | |_| |___| |_| | | | | (_| |
 \__\_\\__,_|\__,_|_| |_|\__|______\__,_|_| |_|\__,_|

  Adaptive Kalman Filter Pairs Trading Engine
  https://github.com/Gzeu/quantluna
""")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="quantluna",
        description="QuantLuna — Adaptive Kalman Filter Pairs Trading Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py paper --pair BTCUSDT ETHUSDT --capital 10000
  python main.py live  --pair BTCUSDT ETHUSDT
  python main.py backtest --pair BTCUSDT ETHUSDT --days 365
  python main.py optimize --pair BTCUSDT ETHUSDT --trials 200 --jobs 4
  python main.py dashboard
  python main.py dashboard --dev
  python main.py health --pair BTCUSDT ETHUSDT --exchange bybit
"""
    )

    sub = p.add_subparsers(dest="command", required=True)

    # paper
    paper = sub.add_parser("paper", help="Run paper trader (safe, no real orders)")
    paper.add_argument("--pair", nargs=2, default=["BTCUSDT", "ETHUSDT"], metavar=("Y", "X"))
    paper.add_argument("--exchange", default="bybit")
    paper.add_argument("--capital", type=float, default=10_000.0)
    paper.add_argument("--params", default=None, help="JSON file from optimizer")
    paper.add_argument("--telegram-token", default="")
    paper.add_argument("--telegram-chat", default="")
    paper.add_argument("--health-check", action="store_true")

    # live
    live = sub.add_parser("live", help="Run live trader (real orders)")
    live.add_argument("--pair", nargs=2, default=["BTCUSDT", "ETHUSDT"], metavar=("Y", "X"))
    live.add_argument("--exchange", default="bybit")
    live.add_argument("--params", default=None)
    live.add_argument("--mode", default="live", choices=["live", "testnet"])

    # backtest
    bt = sub.add_parser("backtest", help="Run backtest")
    bt.add_argument("--pair", nargs=2, default=["BTCUSDT", "ETHUSDT"], metavar=("Y", "X"))
    bt.add_argument("--exchange", default="bybit")
    bt.add_argument("--days", type=int, default=365)
    bt.add_argument("--timeframe", default="1h")
    bt.add_argument("--capital", type=float, default=10_000.0)

    # optimize
    opt = sub.add_parser("optimize", help="Run Optuna hyperparameter optimization")
    opt.add_argument("--pair", nargs=2, default=["BTCUSDT", "ETHUSDT"], metavar=("Y", "X"))
    opt.add_argument("--exchange", default="bybit")
    opt.add_argument("--trials", type=int, default=150)
    opt.add_argument("--jobs", type=int, default=1)
    opt.add_argument("--objective", default="sharpe", choices=["sharpe", "sortino", "calmar", "profit_factor"])
    opt.add_argument("--output", default="best_params.json")
    opt.add_argument("--storage", default=None)

    # dashboard
    dash = sub.add_parser("dashboard", help="Start monitoring dashboard (http://localhost:8000)")
    dash.add_argument("--port", type=int, default=8000)
    dash.add_argument("--host", default="0.0.0.0")
    # FIX: --reload was previously hardcoded, causing high CPU usage and unexpected
    # restarts in production. It is now opt-in via --dev flag.
    dash.add_argument("--dev", action="store_true", help="Enable auto-reload (development only, do NOT use in production)")

    # health
    health = sub.add_parser("health", help="Run pre-flight health check")
    health.add_argument("--pair", nargs=2, default=["BTCUSDT", "ETHUSDT"], metavar=("Y", "X"))
    health.add_argument("--exchange", default="bybit")
    health.add_argument("--api-key", default="")
    health.add_argument("--api-secret", default="")

    return p.parse_args()


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


def main() -> None:
    _banner()
    args = parse_args()

    if args.command == "paper":
        # FIX: --exchange is now forwarded to scripts/run_paper.py
        cmd = [
            sys.executable, "scripts/run_paper.py",
            "--pair", *args.pair,
            "--exchange", args.exchange,
        ]
        if args.capital:    cmd += ["--capital", str(args.capital)]
        if args.params:     cmd += ["--params", args.params]
        if getattr(args, "telegram_token", ""): cmd += ["--telegram-token", args.telegram_token]
        if getattr(args, "telegram_chat", ""):  cmd += ["--telegram-chat", args.telegram_chat]
        if args.health_check: cmd += ["--health-check"]
        subprocess.run(cmd, check=True)

    elif args.command == "live":
        cmd = [sys.executable, "scripts/run_live.py", "--pair", *args.pair, "--mode", args.mode]
        if args.params: cmd += ["--params", args.params]
        subprocess.run(cmd, check=True)

    elif args.command == "backtest":
        # FIX: --exchange is now forwarded to scripts/run_backtest.py
        cmd = [
            sys.executable, "scripts/run_backtest.py",
            "--pair", *args.pair,
            "--exchange", args.exchange,
            "--days", str(args.days),
            "--timeframe", args.timeframe,
            "--capital", str(args.capital),
        ]
        subprocess.run(cmd, check=True)

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
        if args.storage: cmd += ["--storage", args.storage]
        subprocess.run(cmd, check=True)

    elif args.command == "dashboard":
        cmd = [
            sys.executable, "-m", "uvicorn",
            "dashboard.server:app",
            "--host", args.host,
            "--port", str(args.port),
        ]
        # FIX: --reload is only added when --dev flag is explicitly passed
        if args.dev:
            cmd.append("--reload")
        subprocess.run(cmd, check=True)

    elif args.command == "health":
        asyncio.run(cmd_health(args))


if __name__ == "__main__":
    main()
