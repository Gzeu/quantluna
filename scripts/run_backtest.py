"""
scripts/run_backtest.py  —  Historical backtest entry point.

Can be run standalone::

    python scripts/run_backtest.py --pair BTCUSDT ETHUSDT --days 90

Or called from main.py dispatch::

    from scripts.run_backtest import main
    await main(pair=["BTCUSDT", "ETHUSDT"], exchange="bybit", days=90)

The heavy computation (run_single / run_walk_forward) is synchronous and
blocks during execution. This is intentional — backtests are CPU-bound and
the event loop does not provide benefit here. The async wrapper is present
only for interface consistency with the main.py dispatch pattern.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from config.settings import QuantLunaConfig, BacktestConfig
from backtest.engine import BacktestEngine

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="run_backtest")
    ap.add_argument("--pair", nargs=2, metavar=("SYM_Y", "SYM_X"), required=True)
    ap.add_argument("--exchange", default=os.environ.get("QUANTLUNA_EXCHANGE", "bybit"))
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument("--params", default=None)
    ap.add_argument("--data-dir", dest="data_dir", default="data/historical")
    ap.add_argument("--output", default="results/backtest_result.json")
    ap.add_argument("--walk-forward", dest="walk_forward", action="store_true")
    ap.add_argument("--n-splits", dest="n_splits", type=int, default=5)
    ap.add_argument("--verbose", "-v", action="store_true")
    return ap.parse_args()


def build_config(args) -> BacktestConfig:
    cfg = BacktestConfig()
    cfg.sym_y      = args.pair[0]
    cfg.sym_x      = args.pair[1]
    cfg.exchange   = args.exchange
    cfg.days       = args.days
    cfg.timeframe  = args.timeframe
    cfg.capital    = args.capital
    cfg.data_dir   = args.data_dir
    return cfg


def run_single(cfg: BacktestConfig, data_dir: Path) -> dict[str, Any]:
    engine = BacktestEngine(cfg, data_dir=data_dir)
    return engine.run()


def run_walk_forward(cfg: BacktestConfig, data_dir: Path, n_splits: int) -> dict[str, Any]:
    from backtest.walk_forward import WalkForwardValidator
    wf = WalkForwardValidator(cfg, data_dir=data_dir, n_splits=n_splits)
    return wf.run()


def _synthetic_result(cfg: BacktestConfig) -> dict[str, Any]:
    """Fallback when data is unavailable (CI / dry-run)."""
    return {
        "pair":     f"{cfg.sym_y}/{cfg.sym_x}",
        "days":     cfg.days,
        "capital":  cfg.capital,
        "pnl":      0.0,
        "sharpe":   0.0,
        "trades":   0,
        "source":   "synthetic",
    }


def print_result(result: dict[str, Any]) -> None:
    print("\n=== Backtest Result ===")
    for k, v in result.items():
        print(f"  {k:20s}: {v}")


async def main(
    pair: list[str] | None = None,
    exchange: str | None = None,
    days: int | None = None,
    timeframe: str | None = None,
    capital: float | None = None,
    params_file: str | None = None,
    data_dir: str = "data/historical",
    output: str = "results/backtest_result.json",
    walk_forward: bool = False,
    n_splits: int = 5,
    **_,
) -> None:
    """
    Async wrapper for backward compatibility with main.py dispatch.
    Heavy computation remains synchronous.
    """
    if pair is None:
        args = parse_args()
        pair         = args.pair
        exchange     = args.exchange
        days         = args.days
        timeframe    = args.timeframe
        capital      = args.capital
        params_file  = args.params
        data_dir     = args.data_dir
        output       = args.output
        walk_forward = args.walk_forward
        n_splits     = args.n_splits
        if args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)

    class _Args:
        pass
    args_obj = _Args()
    args_obj.pair       = pair
    args_obj.exchange   = exchange or "bybit"
    args_obj.days       = days or 90
    args_obj.timeframe  = timeframe or "1h"
    args_obj.capital    = capital or 10_000.0
    args_obj.data_dir   = data_dir
    cfg = build_config(args_obj)

    if params_file:
        import json
        with open(params_file) as f:
            overrides = json.load(f).get("params", {})
        for k, v in overrides.items():
            setattr(cfg, k, v)

    dpath = Path(data_dir)
    try:
        if walk_forward:
            result = run_walk_forward(cfg, dpath, n_splits)
        else:
            result = run_single(cfg, dpath)
    except Exception as exc:
        logger.warning("Backtest failed (%s), using synthetic result", exc)
        result = _synthetic_result(cfg)

    print_result(result)

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    import json
    with open(out, "w") as f:
        json.dump(result, f, indent=2, default=str)
    logger.info("Result saved → %s", out)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
