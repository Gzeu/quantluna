#!/usr/bin/env python
"""
scripts/optimize_params.py  —  QuantLuna Hyperparameter Optimization CLI

Sprint 12

Usage:
    python scripts/optimize_params.py \\
        --pair BTCUSDT ETHUSDT \\
        --exchange bybit \\
        --timeframe 1h \\
        --days 365 \\
        --trials 200 \\
        --objective sharpe \\
        --output best_params.json

    # Cu Optuna storage (SQLite) pentru resume:
    python scripts/optimize_params.py \\
        --pair BTCUSDT ETHUSDT \\
        --storage sqlite:///optuna.db \\
        --trials 500

    # Parallel cu 4 jobs:
    python scripts/optimize_params.py \\
        --pair BTCUSDT ETHUSDT \\
        --jobs 4
"""

import argparse
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
logger = logging.getLogger("optimize")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="QuantLuna — Optuna Hyperparameter Optimization",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--pair", nargs=2, default=["BTCUSDT", "ETHUSDT"], metavar=("SYM_Y", "SYM_X"))
    p.add_argument("--exchange", default="bybit")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--days", type=int, default=365, help="Days of history to use")
    p.add_argument("--trials", type=int, default=150, help="Number of Optuna trials")
    p.add_argument("--jobs", type=int, default=1, help="Parallel jobs (-1 = all CPUs)")
    p.add_argument(
        "--objective",
        default="sharpe",
        choices=["sharpe", "sortino", "calmar", "profit_factor"],
    )
    p.add_argument("--train-ratio", type=float, default=0.70)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--storage", default=None, help="Optuna storage URL (e.g. sqlite:///optuna.db)")
    p.add_argument("--study-name", default="quantluna_opt")
    p.add_argument("--output", default="best_params.json", help="Output JSON file")
    p.add_argument("--no-cache", action="store_true", help="Force re-download (skip cache)")
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument("--min-trades", type=int, default=10)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    sym_y, sym_x = args.pair

    logger.info(f"QuantLuna Optimizer | pair={sym_y}/{sym_x} | exchange={args.exchange}")
    logger.info(f"Trials={args.trials} | jobs={args.jobs} | objective={args.objective}")

    # Load data
    from data.market_data_cache import MarketDataCache
    cache = MarketDataCache()

    logger.info(f"Loading {sym_y} data...")
    ohlcv_y = (
        cache._download(sym_y, args.exchange, args.timeframe, days=args.days)
        if args.no_cache
        else cache.load(sym_y, args.exchange, args.timeframe, days=args.days)
    )
    logger.info(f"Loading {sym_x} data...")
    ohlcv_x = (
        cache._download(sym_x, args.exchange, args.timeframe, days=args.days)
        if args.no_cache
        else cache.load(sym_x, args.exchange, args.timeframe, days=args.days)
    )

    # Align on common index
    common = ohlcv_y.index.intersection(ohlcv_x.index)
    ohlcv_y = ohlcv_y.loc[common]
    ohlcv_x = ohlcv_x.loc[common]
    logger.info(f"Aligned: {len(common)} common bars ({ohlcv_y.index[0].date()} — {ohlcv_y.index[-1].date()})")

    # Run optimization
    from strategy.optimizer import QuantLunaOptimizer, OptimizerConfig, SearchSpace

    cfg = OptimizerConfig(
        n_trials=args.trials,
        n_jobs=args.jobs,
        objective=args.objective,
        train_ratio=args.train_ratio,
        seed=args.seed,
        storage_url=args.storage,
        study_name=args.study_name,
        bar_freq=args.timeframe,
        capital_usdt=args.capital,
        min_trades=args.min_trades,
    )
    optimizer = QuantLunaOptimizer(cfg)
    result = optimizer.optimize(ohlcv_y, ohlcv_x)

    # Save
    result.save_json(args.output)
    logger.info(f"Best params saved to {args.output}")

    # Print patch for LiveConfig
    logger.info("\n--- LiveConfig patch ---")
    for k, v in result.params.items():
        logger.info(f"  {k} = {v}")
    logger.info(f"  # Test Sharpe: {result.sharpe_test:.3f}")
    logger.info(f"  # Test Win Rate: {result.win_rate_test:.2%}")
    logger.info(f"  # Test Max DD: {result.max_dd_test:.2%}")
    logger.info("--- end patch ---")


if __name__ == "__main__":
    main()
