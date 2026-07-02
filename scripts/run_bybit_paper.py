"""
QuantLuna — Bybit Paper Run (Sprint 21)

Identic cu run_bybit_live.py dar forcă DRY_RUN=true indiferent de .env.
Folosit pentru testare cu date reale Bybit fără risc de ordine reale.

  >>> python scripts/run_bybit_paper.py --pair BTC/ETH --interval 5 --bars 500
  >>> python scripts/run_bybit_paper.py --pair SOL/BTC --interval 15 --warmup 50
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loguru import logger


async def main(args) -> None:
    from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig
    from execution.bybit_ws_bars import BybitWsBarsAdapter

    cfg = BybitLiveRunnerConfig.from_env()
    cfg.dry_run = True  # ALWAYS paper in this script

    if args.pair:
        parts = args.pair.upper().replace("-", "/").split("/")
        if len(parts) == 2:
            cfg.symbol_y = parts[0] + "USDT" if not parts[0].endswith("USDT") else parts[0]
            cfg.symbol_x = parts[1] + "USDT" if not parts[1].endswith("USDT") else parts[1]

    if args.interval: cfg.interval     = args.interval
    if args.entry_z:  cfg.entry_zscore = args.entry_z
    if args.exit_z:   cfg.exit_zscore  = args.exit_z
    if args.warmup:   cfg.warmup_bars  = args.warmup

    logger.info(
        f"Paper run: {cfg.symbol_y}/{cfg.symbol_x} "
        f"interval={cfg.interval}m bars={args.bars}"
    )

    # Mock WS feed limited to args.bars
    ws_feed = BybitWsBarsAdapter(ws_feed=None, interval=cfg.interval)
    # Patch mock stream bar limit
    _orig_mock = ws_feed._mock_stream
    async def _limited_mock(sy, sx):
        count = 0
        async for bar in _orig_mock(sy, sx, n_bars=args.bars):
            yield bar
            count += 1
            if count >= args.bars:
                break
    ws_feed._mock_stream = _limited_mock

    runner = BybitLiveRunner(cfg=cfg, ws_feed=ws_feed)
    await runner.start()

    s = runner.status()
    print("\n" + "=" * 50)
    print("  QuantLuna Paper Run — Bybit Mock")
    print("=" * 50)
    print(f"  Pair:    {s['symbol_y']}/{s['symbol_x']}")
    print(f"  Bars:    {s['bars_processed']}")
    print(f"  Orders:  {s['orders_submitted']}")
    print(f"  Blocks:  {s['gate_blocks']}")
    print(f"  Errors:  {s['errors']}")
    print(f"  Uptime:  {s['uptime_s']}s")
    print("=" * 50 + "\n")


def cli():
    p = argparse.ArgumentParser(description="QuantLuna — Bybit Paper Run")
    p.add_argument("--pair",     type=str,   default="BTC/ETH")
    p.add_argument("--interval", type=str,   default=None)
    p.add_argument("--entry-z",  type=float, default=None, dest="entry_z")
    p.add_argument("--exit-z",   type=float, default=None, dest="exit_z")
    p.add_argument("--warmup",   type=int,   default=None)
    p.add_argument("--bars",     type=int,   default=200)
    args = p.parse_args()
    asyncio.run(main(args))


if __name__ == "__main__":
    cli()
