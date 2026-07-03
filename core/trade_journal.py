"""
core/trade_journal.py — persistent CSV trade journal.

Keeps a lightweight append-only journal for post-trade analysis and reporting.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List


@dataclass
class TradeJournalEntry:
    ts: str
    pair: str
    side: str
    qty_y: float
    qty_x: float
    entry_zscore: float
    exit_zscore: float
    pnl_usdt: float
    duration_sec: float
    reason: str


class TradeJournal:
    def __init__(self, path: str = "data/trade_journal.csv") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_header()

    def _write_header(self) -> None:
        with self.path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(TradeJournalEntry.__annotations__.keys()))
            writer.writeheader()

    def append(self, entry: TradeJournalEntry) -> None:
        with self.path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(TradeJournalEntry.__annotations__.keys()))
            writer.writerow(asdict(entry))

    def append_simple(
        self,
        pair: str,
        side: str,
        pnl_usdt: float,
        qty_y: float = 0.0,
        qty_x: float = 0.0,
        entry_zscore: float = 0.0,
        exit_zscore: float = 0.0,
        duration_sec: float = 0.0,
        reason: str = "manual",
    ) -> None:
        self.append(
            TradeJournalEntry(
                ts=datetime.now(timezone.utc).isoformat(),
                pair=pair,
                side=side,
                qty_y=qty_y,
                qty_x=qty_x,
                entry_zscore=entry_zscore,
                exit_zscore=exit_zscore,
                pnl_usdt=pnl_usdt,
                duration_sec=duration_sec,
                reason=reason,
            )
        )

    def read_all(self) -> List[TradeJournalEntry]:
        if not self.path.exists():
            return []
        rows: List[TradeJournalEntry] = []
        with self.path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(
                    TradeJournalEntry(
                        ts=row["ts"],
                        pair=row["pair"],
                        side=row["side"],
                        qty_y=float(row["qty_y"]),
                        qty_x=float(row["qty_x"]),
                        entry_zscore=float(row["entry_zscore"]),
                        exit_zscore=float(row["exit_zscore"]),
                        pnl_usdt=float(row["pnl_usdt"]),
                        duration_sec=float(row["duration_sec"]),
                        reason=row["reason"],
                    )
                )
        return rows
