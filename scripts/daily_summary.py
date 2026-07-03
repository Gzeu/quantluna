#!/usr/bin/env python
"""
scripts/daily_summary.py — daily performance summary via NotifierBus.

Run via cron or scheduler at end of day:
  0 23 * * * python scripts/daily_summary.py --pair BTCUSDT ETHUSDT

Or from Makefile:
  make daily-summary
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("daily_summary")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Send daily trading summary")
    p.add_argument("--pair", nargs=2, default=["BTCUSDT", "ETHUSDT"])
    p.add_argument("--journal", default="data/trade_journal.csv")
    return p.parse_args()


async def main() -> None:
    args = parse_args()

    from core.trade_journal import TradeJournal
    from core.performance_analytics import analyze
    from notifications.notifier_bus import NotifierBus

    journal = TradeJournal(args.journal)
    rows = journal.read_all()

    # Filter today's trades
    from datetime import date
    today = date.today().isoformat()
    today_rows = [r for r in rows if r.ts.startswith(today)]

    pnl_series = [r.pnl_usdt for r in today_rows]
    metrics = analyze(pnl_series)

    pair_str = "/".join(args.pair)
    lines = [
        f"\ud83d\udcc5 *Daily Summary* — {today}",
        f"Pair: `{pair_str}`",
        "",
        f"Trades today: `{metrics.n_trades}`",
        f"Total PnL: `{metrics.total_pnl:+.2f} USDT`",
        f"Win rate: `{metrics.win_rate*100:.1f}%`",
        f"Profit factor: `{metrics.profit_factor:.2f}`",
        f"Max drawdown: `{metrics.max_drawdown:.2f} USDT`",
        f"Sharpe: `{metrics.sharpe:.2f}`",
        f"Sortino: `{metrics.sortino:.2f}`",
        f"Expectancy: `{metrics.expectancy:+.2f} USDT/trade`",
    ]
    message = "\n".join(lines)
    logger.info("Daily summary:\n%s", message)

    try:
        bus = NotifierBus()
        await bus.send_daily_summary(message)
        logger.info("Daily summary sent via NotifierBus")
    except Exception as exc:
        logger.warning("NotifierBus unavailable: %s — summary only logged", exc)


if __name__ == "__main__":
    asyncio.run(main())
