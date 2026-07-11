"""
execution/daily_pnl_tracker.py  -  QuantLuna Daily PnL Tracker v1.0

Sprint S31 (2026-07-12):
  Inregistreaza PnL zilnic per strategie intr-o baza SQLite locala.
  Sursa de date: bybit_order_router.fetch_closed_pnl() + spot wallet delta.

  Schema SQLite:
    daily_pnl(date TEXT, strategy TEXT, realised_pnl REAL,
              equity_start REAL, equity_end REAL, trade_count INT,
              created_at TEXT)

Usage::

    tracker = DailyPnLTracker(db_path="state/daily_pnl.db")
    await tracker.record(strategy="pairs_futures", realised_pnl=42.5,
                         equity_start=1000.0, equity_end=1042.5)
    summary = await tracker.get_daily_summary("2026-07-12")
"""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class DailyPnLTracker:
    """
    Tracker SQLite pentru PnL zilnic per strategie.

    Thread-safe: toate operatiile DB ruleaza in executor pentru
    compatibilitate cu asyncio.
    """

    _CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS daily_pnl (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        date        TEXT NOT NULL,
        strategy    TEXT NOT NULL,
        realised_pnl REAL NOT NULL DEFAULT 0.0,
        equity_start REAL NOT NULL DEFAULT 0.0,
        equity_end   REAL NOT NULL DEFAULT 0.0,
        trade_count  INTEGER NOT NULL DEFAULT 0,
        fees_paid    REAL NOT NULL DEFAULT 0.0,
        created_at   TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_daily_pnl_date ON daily_pnl(date);
    CREATE INDEX IF NOT EXISTS idx_daily_pnl_strategy ON daily_pnl(strategy);
    """

    def __init__(self, db_path: str = "state/daily_pnl.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            for stmt in self._CREATE_TABLE.strip().split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(s)
            conn.commit()
        logger.debug("[DailyPnLTracker] DB initializat: {}", self._db_path)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def record(
        self,
        strategy: str,
        realised_pnl: float,
        equity_start: float = 0.0,
        equity_end: float = 0.0,
        trade_count: int = 0,
        fees_paid: float = 0.0,
        date: Optional[str] = None,
    ) -> None:
        """Inregistreaza sau actualizeaza PnL pentru ziua curenta."""
        _date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._record_sync(
                _date, strategy, realised_pnl,
                equity_start, equity_end, trade_count, fees_paid, now_iso
            )
        )

    def _record_sync(
        self,
        date: str, strategy: str, realised_pnl: float,
        equity_start: float, equity_end: float,
        trade_count: int, fees_paid: float, created_at: str,
    ) -> None:
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id, realised_pnl, trade_count, fees_paid "
                "FROM daily_pnl WHERE date=? AND strategy=?",
                (date, strategy)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE daily_pnl SET "
                    "realised_pnl=realised_pnl+?, "
                    "equity_end=?, "
                    "trade_count=trade_count+?, "
                    "fees_paid=fees_paid+? "
                    "WHERE id=?",
                    (realised_pnl, equity_end, trade_count, fees_paid, existing["id"])
                )
            else:
                conn.execute(
                    "INSERT INTO daily_pnl "
                    "(date, strategy, realised_pnl, equity_start, equity_end, "
                    " trade_count, fees_paid, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (date, strategy, realised_pnl, equity_start,
                     equity_end, trade_count, fees_paid, created_at)
                )
            conn.commit()
        logger.debug(
            "[DailyPnLTracker] record {} {} pnl={:+.4f}",
            date, strategy, realised_pnl,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_daily_summary(self, date: str) -> Dict[str, Any]:
        """
        Returneaza un dict cu PnL agregat pentru o zi.

        Returns::
            {
                "date": "2026-07-12",
                "total_equity_usdt": 1042.5,
                "realised_pnl_usdt": 42.5,
                "realised_pnl_pct": 0.042,
                "total_trades": 5,
                "total_fees": 1.2,
                "strategies": [{"name": ..., "pnl": ..., "trades": ...}]
            }
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self._get_daily_summary_sync(date)
        )

    def _get_daily_summary_sync(self, date: str) -> Dict[str, Any]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT strategy, realised_pnl, equity_start, equity_end, "
                "trade_count, fees_paid "
                "FROM daily_pnl WHERE date=?",
                (date,)
            ).fetchall()

        if not rows:
            return {
                "date": date,
                "total_equity_usdt": 0.0,
                "realised_pnl_usdt": 0.0,
                "realised_pnl_pct": 0.0,
                "total_trades": 0,
                "total_fees": 0.0,
                "strategies": [],
            }

        total_pnl = sum(r["realised_pnl"] for r in rows)
        max_equity_end = max((r["equity_end"] for r in rows), default=0.0)
        total_equity_start = max((r["equity_start"] for r in rows), default=0.0)
        pnl_pct = (
            total_pnl / total_equity_start if total_equity_start > 0 else 0.0
        )
        return {
            "date": date,
            "total_equity_usdt": max_equity_end,
            "realised_pnl_usdt": total_pnl,
            "realised_pnl_pct": pnl_pct,
            "total_trades": sum(r["trade_count"] for r in rows),
            "total_fees": sum(r["fees_paid"] for r in rows),
            "strategies": [
                {
                    "name": r["strategy"],
                    "pnl": r["realised_pnl"],
                    "equity_end": r["equity_end"],
                    "trades": r["trade_count"],
                    "fees": r["fees_paid"],
                }
                for r in rows
            ],
        }

    async def get_history(
        self,
        strategy: Optional[str] = None,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """Returneaza ultimele N zile de PnL."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self._get_history_sync(strategy, limit)
        )

    def _get_history_sync(
        self, strategy: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            if strategy:
                rows = conn.execute(
                    "SELECT date, strategy, realised_pnl, equity_end, "
                    "trade_count, fees_paid "
                    "FROM daily_pnl WHERE strategy=? "
                    "ORDER BY date DESC LIMIT ?",
                    (strategy, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT date, strategy, realised_pnl, equity_end, "
                    "trade_count, fees_paid "
                    "FROM daily_pnl ORDER BY date DESC LIMIT ?",
                    (limit,)
                ).fetchall()
        return [
            {
                "date": r["date"],
                "strategy": r["strategy"],
                "pnl": r["realised_pnl"],
                "equity_end": r["equity_end"],
                "trades": r["trade_count"],
                "fees": r["fees_paid"],
            }
            for r in rows
        ]

    async def get_cumulative_pnl(
        self, strategy: Optional[str] = None
    ) -> float:
        """PnL cumulativ total (toate zilele)."""
        history = await self.get_history(strategy=strategy, limit=10000)
        return sum(r["pnl"] for r in history)
