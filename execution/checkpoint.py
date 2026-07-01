"""
execution/checkpoint.py  —  QuantLuna Position State Checkpoint

Problema rezolvată:
  Dacă botul crash-ează sau serverul se reporneşte cu o poziție deschisă,
  la restart nu ştim: există poziție? pe ce parte? la ce qty?
  Fără checkpoint → risc de poziție dublă sau neacoperită.

Soluție:
  Scriem starea poziției în SQLite la fiecare schimbare (OPEN/CLOSE).
  La startup, LiveTrader apelează checkpoint.load() şi dacă găseşte
  o poziție deschisă — o reconciliază cu exchange-ul înainte de a porni.

Usage:
    from execution.checkpoint import PositionCheckpoint
    cp = PositionCheckpoint("position_checkpoint.db")
    cp.save_open(sym_y, sym_x, side_y, qty_y, qty_x, entry_price_y, entry_price_x,
                 zscore, hedge_ratio, notional)
    cp.save_closed()
    state = cp.load()  # None dacă nu e poziție deschisă
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    sym_y: str
    sym_x: str
    side_y: str        # 'buy' sau 'sell'
    side_x: str
    qty_y: float
    qty_x: float
    entry_price_y: float
    entry_price_x: float
    entry_zscore: float
    hedge_ratio: float
    notional_usdt: float
    opened_at: float   # unix timestamp
    meta: dict         # date auxiliare libere


class PositionCheckpoint:
    """
    Persistă starea poziției deschise în SQLite WAL.
    Thread-safe (fiecare apel deschide o nouă conexiune).
    """

    def __init__(self, db_path: str = "position_checkpoint.db") -> None:
        self._path = str(Path(db_path))
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS position (
                    id          INTEGER PRIMARY KEY CHECK (id = 1),
                    is_open     INTEGER NOT NULL DEFAULT 0,
                    payload     TEXT,
                    updated_at  REAL
                )
            """)
            conn.execute(
                "INSERT OR IGNORE INTO position (id, is_open) VALUES (1, 0)"
            )
            conn.commit()

    def save_open(
        self,
        sym_y: str, sym_x: str,
        side_y: str, side_x: str,
        qty_y: float, qty_x: float,
        entry_price_y: float, entry_price_x: float,
        entry_zscore: float, hedge_ratio: float,
        notional_usdt: float,
        meta: Optional[dict] = None,
    ) -> None:
        """Apelat imediat după confirmare fill de intrare."""
        state = PositionState(
            sym_y=sym_y, sym_x=sym_x,
            side_y=side_y, side_x=side_x,
            qty_y=qty_y, qty_x=qty_x,
            entry_price_y=entry_price_y,
            entry_price_x=entry_price_x,
            entry_zscore=entry_zscore,
            hedge_ratio=hedge_ratio,
            notional_usdt=notional_usdt,
            opened_at=time.time(),
            meta=meta or {},
        )
        payload = json.dumps(asdict(state))
        try:
            with sqlite3.connect(self._path) as conn:
                conn.execute(
                    "UPDATE position SET is_open=1, payload=?, updated_at=? WHERE id=1",
                    (payload, time.time()),
                )
                conn.commit()
            logger.info(f"[Checkpoint] OPEN saved: {sym_y}/{sym_x} {side_y} qty={qty_y:.4f}")
        except Exception as exc:
            logger.error(f"[Checkpoint] save_open failed: {exc}")

    def save_closed(self) -> None:
        """Apelat după confirmare fill de ieşire."""
        try:
            with sqlite3.connect(self._path) as conn:
                conn.execute(
                    "UPDATE position SET is_open=0, payload=NULL, updated_at=? WHERE id=1",
                    (time.time(),),
                )
                conn.commit()
            logger.info("[Checkpoint] CLOSED saved")
        except Exception as exc:
            logger.error(f"[Checkpoint] save_closed failed: {exc}")

    def load(self) -> Optional[PositionState]:
        """
        Returnează PositionState dacă există poziție deschisă la ultima închidere,
        None altfel. Apelat la startup.
        """
        try:
            with sqlite3.connect(self._path) as conn:
                row = conn.execute(
                    "SELECT is_open, payload FROM position WHERE id=1"
                ).fetchone()
            if not row or not row[0] or not row[1]:
                return None
            data = json.loads(row[1])
            return PositionState(**data)
        except Exception as exc:
            logger.error(f"[Checkpoint] load failed: {exc}")
            return None

    def has_open_position(self) -> bool:
        state = self.load()
        return state is not None
