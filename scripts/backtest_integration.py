"""
QuantLuna — Backtest Integration (Sprint 20)

Rulează IntegrationLoop pe date istorice reale (sau sintetice cu seed).
Foloseşte KalmanAdapter + VolRegimeAdapter + SpreadMonitor + RegimeFilter +
CircuitBreaker — exact stack-ul care va rula în live.

Usage:
    python scripts/backtest_integration.py --bars 1000 --entry-z 2.0
    python scripts/backtest_integration.py --csv data/BTCUSDT_ETHUSDT_1h.csv

  CSV format: timestamp,close_y,close_x  (sau orice cu coloanele close_y, close_x)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import random
import sys
import math
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loguru import logger


def load_csv_bars(path: str) -> List:
    """Load BarData from CSV with columns: timestamp,close_y,close_x"""
    from execution.integration_loop import BarData
    bars = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                bars.append(BarData(
                    symbol_y="BTCUSDT",
                    symbol_x="ETHUSDT",
                    price_y=float(row.get("close_y", row.get("price_y", 0))),
                    price_x=float(row.get("close_x", row.get("price_x", 0))),
                    timestamp=float(row.get("timestamp", 0)),
                ))
            except (ValueError, KeyError):
                continue
    logger.info(f"Loaded {len(bars)} bars from {path}")
    return bars


def generate_ou_bars(n: int, seed: int, entry_z: float) -> List:
    """Generate Ornstein-Uhlenbeck synthetic bars."""
    from execution.integration_loop import BarData
    random.seed(seed)
    theta = 0.05
    mu = 0.0
    sigma = 1.0
    spread = 0.0
    base = 100.0
    bars = []
    for i in range(n):
        spread += theta * (mu - spread) + sigma * random.gauss(0, 1)
        px = base + random.gauss(0, 0.5)
        py = px + spread
        bars.append(BarData("BTCUSDT", "ETHUSDT", py, px))
    return bars


async def run(args):
    from execution.integration_loop import IntegrationLoop, IntegrationLoopConfig
    from core.kalman_adapter import KalmanAdapter
    from core.vol_regime_adapter import VolRegimeAdapter
    from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
    from strategy.regime_filter import RegimeFilter
    from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

    # Load bars
    if args.csv:
        bars = load_csv_bars(args.csv)
    else:
        bars = generate_ou_bars(args.bars, args.seed, args.entry_z)

    if not bars:
        logger.error("No bars loaded. Exiting.")
        return

    logger.info(f"Backtest: {len(bars)} bars | entry_z={args.entry_z} exit_z={args.exit_z}")

    # Stack
    kf  = KalmanAdapter(window=args.window)
    vr  = VolRegimeAdapter(ewma_span=20)
    cb  = CircuitBreaker(CircuitBreakerConfig(
        max_consecutive_losses=5,
        max_drawdown_pct=10.0,
        cooldown_seconds=0,       # no cooldown in backtest
    ))
    sm  = SpreadMonitor(SpreadMonitorConfig(
        min_bars=args.window,
        zscore_control_limit=4.5,
        max_half_life_hours=120.0,
        stuck_bars_threshold=60,
    ))
    rf  = RegimeFilter(circuit_breaker=cb, vol_regime=vr, spread_monitor=sm)

    cfg = IntegrationLoopConfig(
        symbol_y="BTCUSDT",
        symbol_x="ETHUSDT",
        venue="backtest",
        entry_zscore=args.entry_z,
        exit_zscore=args.exit_z,
        base_qty=0.001,
        dry_run=True,
        bar_interval_s=0.0,
    )

    loop = IntegrationLoop(
        cfg=cfg,
        kalman=kf,
        spread_monitor=sm,
        regime_filter=rf,
    )

    results = await loop.run_synthetic(bars)

    # Stats
    total      = len(results)
    entries    = sum(r.order_submitted for r in results)
    blocked    = sum(not r.gate_allowed for r in results)
    unhealthy  = sum(not r.spread_healthy for r in results)
    avg_ms     = sum(r.duration_ms for r in results) / max(total, 1)
    z_vals     = [abs(r.zscore) for r in results]
    z_max      = max(z_vals) if z_vals else 0.0
    z_mean     = sum(z_vals) / len(z_vals) if z_vals else 0.0

    print("\n" + "=" * 56)
    print("  QuantLuna Backtest Integration — Summary")
    print("=" * 56)
    print(f"  Bars:            {total}")
    print(f"  Entries/exits:   {entries}")
    print(f"  Gate blocked:    {blocked}")
    print(f"  Spread unhealthy:{unhealthy}")
    print(f"  Max |z|:         {z_max:.3f}")
    print(f"  Mean |z|:        {z_mean:.3f}")
    print(f"  Avg cycle:       {avg_ms:.3f} ms")
    print(f"  CB is_open:      {cb.is_open}")
    print(f"  Vol regime:      {vr.current_regime.value}")
    print("=" * 56 + "\n")

    return results


def main():
    p = argparse.ArgumentParser(description="QuantLuna backtest integration")
    p.add_argument("--bars",    type=int,   default=1000)
    p.add_argument("--entry-z", type=float, default=2.0,  dest="entry_z")
    p.add_argument("--exit-z",  type=float, default=0.5,  dest="exit_z")
    p.add_argument("--window",  type=int,   default=100)
    p.add_argument("--seed",    type=int,   default=42)
    p.add_argument("--csv",     type=str,   default="",   help="Path to CSV file")
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
