#!/usr/bin/env python
"""
scripts/generate_report.py — generate simple HTML report from trade journal.
"""
from __future__ import annotations

from pathlib import Path

from core.trade_journal import TradeJournal


def main() -> None:
    journal = TradeJournal()
    rows = journal.read_all()
    total_pnl = sum(r.pnl_usdt for r in rows)
    wins = sum(1 for r in rows if r.pnl_usdt > 0)
    losses = sum(1 for r in rows if r.pnl_usdt < 0)
    n = len(rows)
    win_rate = (wins / n * 100.0) if n else 0.0

    html = f"""
    <html>
      <head><title>QuantLuna Report</title></head>
      <body>
        <h1>QuantLuna Performance Report</h1>
        <p>Total trades: {n}</p>
        <p>Wins: {wins}</p>
        <p>Losses: {losses}</p>
        <p>Win rate: {win_rate:.2f}%</p>
        <p>Total PnL: {total_pnl:.2f} USDT</p>
      </body>
    </html>
    """
    out = Path("output")
    out.mkdir(exist_ok=True)
    (out / "report.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
