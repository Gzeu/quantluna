#!/usr/bin/env python3
"""
scripts/run_backtest.py  —  QuantLuna Backtest Runner

Rulează un backtest cu parametrii din:
  - StrategyConfig default
  - JSON exportat de optimize_params.py (--params-file)
  - Argumente CLI directe (override)

Usage:
    # Default params:
    python scripts/run_backtest.py --pair BTCUSDT ETHUSDT

    # Cu best params din optimizer:
    python scripts/run_backtest.py \\
        --pair BTCUSDT ETHUSDT \\
        --params-file data/best_params.json

    # Override individual param:
    python scripts/run_backtest.py \\
        --params-file data/best_params.json \\
        --zscore-entry 2.2 \\
        --capital 20000

    # Walk-forward validation:
    python scripts/run_backtest.py \\
        --pair BTCUSDT ETHUSDT \\
        --params-file data/best_params.json \\
        --walk-forward --n-splits 5
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backtest")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="QuantLuna Backtest Runner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--pair", nargs=2, metavar=("SYM_Y", "SYM_X"),
                   default=["BTCUSDT", "ETHUSDT"])
    p.add_argument("--params-file", type=str, default=None,
                   help="JSON file from optimize_params.py --export-best")
    p.add_argument("--data-dir", type=str, default="data/")
    p.add_argument("--capital", type=float, default=None,
                   help="Override capital_usdt")
    p.add_argument("--bar-freq", type=str, default="1h")
    p.add_argument("--zscore-entry", type=float, default=None)
    p.add_argument("--zscore-exit", type=float, default=None)
    p.add_argument("--delta", type=float, default=None)
    p.add_argument("--walk-forward", action="store_true",
                   help="Run walk-forward validation instead of single backtest")
    p.add_argument("--n-splits", type=int, default=5,
                   help="Number of walk-forward splits")
    p.add_argument("--output", type=str, default="data/backtest_result.json",
                   help="Path to save result JSON")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def build_config(args: argparse.Namespace):
    """Build StrategyConfig from file + CLI overrides."""
    from config.strategy_config import StrategyConfig

    if args.params_file:
        cfg = StrategyConfig.from_optimizer_json(args.params_file)
        logger.info(f"Loaded params from {args.params_file}")
    else:
        cfg = StrategyConfig()
        logger.info("Using default StrategyConfig")

    # CLI overrides take highest priority
    sym_y, sym_x = args.pair
    overrides = {
        "sym_y": sym_y,
        "sym_x": sym_x,
        "bar_freq": args.bar_freq,
    }
    if args.capital      is not None: overrides["capital_usdt"]  = args.capital
    if args.zscore_entry is not None: overrides["zscore_entry"]  = args.zscore_entry
    if args.zscore_exit  is not None: overrides["zscore_exit"]   = args.zscore_exit
    if args.delta        is not None: overrides["delta"]         = args.delta

    import dataclasses
    cfg = dataclasses.replace(cfg, **overrides)
    logger.info(cfg.summary())
    return cfg


def run_single(cfg, data_dir: Path) -> dict:
    """Single backtest run."""
    try:
        from backtest.engine import BacktestEngine
        result = BacktestEngine(cfg).run(
            data_dir=data_dir,
            sym_y=cfg.sym_y,
            sym_x=cfg.sym_x,
        )
        return result
    except ImportError:
        logger.warning("BacktestEngine not found — returning synthetic result")
        return _synthetic_result(cfg)


def run_walk_forward(cfg, data_dir: Path, n_splits: int) -> dict:
    """Walk-forward validation."""
    try:
        from backtest.walk_forward import WalkForwardValidator
        wf = WalkForwardValidator(cfg, n_splits=n_splits)
        return wf.run(data_dir=data_dir)
    except ImportError:
        logger.warning("WalkForwardValidator not found — returning synthetic result")
        return {"walk_forward": True, "n_splits": n_splits, "sharpe": 1.0, "note": "synthetic"}


def _synthetic_result(cfg) -> dict:
    """Minimal synthetic result for dry-run testing."""
    return {
        "pair": f"{cfg.sym_y}/{cfg.sym_x}",
        "sharpe": 1.23,
        "sharpe_ratio": 1.23,
        "total_return": 0.147,
        "max_drawdown": -0.08,
        "n_trades": 42,
        "win_rate": 0.57,
        "note": "synthetic_dry_run",
    }


def print_result(result: dict) -> None:
    print("\n" + "=" * 50)
    print("  Backtest Result")
    print("=" * 50)
    for k, v in result.items():
        if isinstance(v, float):
            print(f"  {k:25s} {v:.4f}")
        else:
            print(f"  {k:25s} {v}")
    print("=" * 50 + "\n")


def main() -> None:
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cfg = build_config(args)
    data_dir = Path(args.data_dir)

    if args.walk_forward:
        logger.info(f"Running walk-forward validation ({args.n_splits} splits)")
        result = run_walk_forward(cfg, data_dir, args.n_splits)
    else:
        logger.info("Running single backtest")
        result = run_single(cfg, data_dir)

    print_result(result)

    # Save result
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(result, f, indent=2, default=str)
    logger.info(f"Result saved → {output}")


if __name__ == "__main__":
    main()
