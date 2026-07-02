"""
QuantLuna — Paper Run Harness (Sprint 19)

Runează un paper trading end-to-end cu date sintetice sau istorice.
Folosit pentru validarea loop-ului de integrare înainte de live trading.

Usage:
    python scripts/paper_run_harness.py --bars 200 --entry-z 2.0 --exit-z 0.5
    python scripts/paper_run_harness.py --bars 500 --seed 42
"""
from __future__ import annotations

import argparse
import asyncio
import random
import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loguru import logger


def generate_synthetic_bars(
    n: int = 200,
    seed: int = 42,
    spread_mean: float = 0.0,
    spread_std: float = 1.0,
    half_life_bars: int = 20,
):
    """Generate synthetic OU spread bars."""
    random.seed(seed)
    import math

    theta = 1.0 - math.exp(-math.log(2) / half_life_bars)
    spread = 0.0
    base_price_x = 100.0
    bars = []

    from execution.integration_loop import BarData

    for _ in range(n):
        spread = spread + theta * (spread_mean - spread) + spread_std * random.gauss(0, 1)
        price_x = base_price_x + random.gauss(0, 0.5)
        price_y = price_x + spread
        bars.append(BarData(
            symbol_y="BTCUSDT",
            symbol_x="ETHUSDT",
            price_y=price_y,
            price_x=price_x,
        ))
    return bars


async def run_harness(args):
    from execution.integration_loop import IntegrationLoop, IntegrationLoopConfig
    from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
    from strategy.regime_filter import RegimeFilter
    from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

    logger.info(f"Paper run harness: {args.bars} bars, entry_z={args.entry_z}, exit_z={args.exit_z}")

    # Components
    cb = CircuitBreaker(CircuitBreakerConfig(
        max_consecutive_losses=5,
        max_drawdown_pct=10.0,
        cooldown_seconds=300,
    ))
    sm = SpreadMonitor(SpreadMonitorConfig(
        min_bars=20,
        zscore_control_limit=4.0,
        max_half_life_hours=96.0,
        stuck_bars_threshold=50,
    ))
    rf = RegimeFilter(circuit_breaker=cb, spread_monitor=sm)

    cfg = IntegrationLoopConfig(
        symbol_y="BTCUSDT",
        symbol_x="ETHUSDT",
        venue="paper",
        entry_zscore=args.entry_z,
        exit_zscore=args.exit_z,
        base_qty=0.001,
        dry_run=True,
        bar_interval_s=0.0,
        max_bars=args.bars,
    )

    loop = IntegrationLoop(
        cfg=cfg,
        spread_monitor=sm,
        regime_filter=rf,
    )

    bars = generate_synthetic_bars(n=args.bars, seed=args.seed)
    results = await loop.run_synthetic(bars)

    # Summary
    total     = len(results)
    orders    = sum(r.order_submitted for r in results)
    blocked   = sum(not r.gate_allowed for r in results)
    unhealthy = sum(not r.spread_healthy for r in results)
    avg_ms    = sum(r.duration_ms for r in results) / max(total, 1)
    zscores   = [r.zscore for r in results]
    z_max     = max(abs(z) for z in zscores) if zscores else 0.0

    print("\n" + "=" * 50)
    print("  QuantLuna Paper Run — Summary")
    print("=" * 50)
    print(f"  Bars processed  : {total}")
    print(f"  Orders submitted: {orders}")
    print(f"  Gate blocked    : {blocked} bars")
    print(f"  Spread unhealthy: {unhealthy} bars")
    print(f"  Max |z-score|   : {z_max:.3f}")
    print(f"  Avg cycle time  : {avg_ms:.3f} ms")
    print(f"  CB is_open      : {cb.is_open}")
    print("=" * 50 + "\n")

    return results


def main():
    parser = argparse.ArgumentParser(description="QuantLuna paper run harness")
    parser.add_argument("--bars",    type=int,   default=200,  help="Number of bars")
    parser.add_argument("--entry-z", type=float, default=2.0,  help="Entry z-score threshold")
    parser.add_argument("--exit-z",  type=float, default=0.5,  help="Exit z-score threshold")
    parser.add_argument("--seed",    type=int,   default=42,   help="Random seed")
    args = parser.parse_args()
    asyncio.run(run_harness(args))


if __name__ == "__main__":
    main()
