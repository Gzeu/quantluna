"""
execution/checkpoint_manager.py — QuantLuna Checkpoint Manager

Salveaza si restaureaza starea pozitiei active intre restart-uri.
Foloseste SQLite (acelasi fisier ca position_checkpoint.db).

Flux:
  save(adopted)  — apelat dupa fiecare trade executat
  load()         — apelat in Phase 0.5 inainte de reconciliere REST
  clear()        — apelat dupa inchiderea completa a pozitiei
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from execution.position_reconciler import AdoptedPosition

_SCHEMA = """
CREATE TABLE IF NOT EXISTS position_checkpoint (
    id          INTEGER PRIMARY KEY,
    saved_at    REAL NOT NULL,
    symbol_y    TEXT NOT NULL,
    symbol_x    TEXT NOT NULL,
    payload     TEXT NOT NULL
);
"""


class CheckpointManager:
    """
    Persistence simpla pentru starea pozitiei active.

    Parametri
    ---------
    path : str | Path — calea catre fisierul SQLite (default: position_checkpoint.db)
    """

    def __init__(self, path: str = "position_checkpoint.db") -> None:
        self._path = Path(path)
        self._init_db()

    def _init_db(self) -> None:
        try:
            conn = sqlite3.connect(str(self._path))
            conn.execute(_SCHEMA)
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning(f"CheckpointManager: init DB failed: {exc}")

    def save(self, position: AdoptedPosition) -> None:
        """Salveaza pozitia curenta. Suprascrie intrarea anterioara."""
        try:
            payload = json.dumps({
                "symbol_y":       position.symbol_y,
                "symbol_x":       position.symbol_x,
                "y_side":         position.y_side,
                "x_side":         position.x_side,
                "y_qty":          position.y_qty,
                "x_qty":          position.x_qty,
                "y_entry_price":  position.y_entry_price,
                "x_entry_price":  position.x_entry_price,
                "unrealised_pnl": position.unrealised_pnl,
                "source":         "checkpoint",
            })
            conn = sqlite3.connect(str(self._path))
            conn.execute("DELETE FROM position_checkpoint")
            conn.execute(
                "INSERT INTO position_checkpoint (saved_at, symbol_y, symbol_x, payload) "
                "VALUES (?, ?, ?, ?)",
                (time.time(), position.symbol_y, position.symbol_x, payload),
            )
            conn.commit()
            conn.close()
            logger.debug(f"CheckpointManager: salvat {position.symbol_y}/{position.symbol_x}")
        except Exception as exc:
            logger.warning(f"CheckpointManager: save() failed: {exc}")

    def load(self) -> Optional[AdoptedPosition]:
        """Restaureaza ultima pozitie salvata sau None daca nu exista."""
        try:
            conn = sqlite3.connect(str(self._path))
            row = conn.execute(
                "SELECT payload, saved_at FROM position_checkpoint ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            if row is None:
                return None
            data = json.loads(row[0])
            saved_at = row[1]
            age_hours = (time.time() - saved_at) / 3600
            if age_hours > 24:
                logger.warning(
                    f"CheckpointManager: checkpoint vechi ({age_hours:.1f}h) — ignorat"
                )
                return None
            pos = AdoptedPosition(**data)
            logger.info(
                f"CheckpointManager: restaurat {pos} "
                f"(salvat acum {age_hours:.1f}h)"
            )
            return pos
        except Exception as exc:
            logger.warning(f"CheckpointManager: load() failed: {exc}")
            return None

    def clear(self) -> None:
        """Sterge checkpoint dupa inchiderea completa a pozitiei."""
        try:
            conn = sqlite3.connect(str(self._path))
            conn.execute("DELETE FROM position_checkpoint")
            conn.commit()
            conn.close()
            logger.info("CheckpointManager: checkpoint sters (pozitie inchisa)")
        except Exception as exc:
            logger.warning(f"CheckpointManager: clear() failed: {exc}")

    @property
    def path(self) -> Path:
        return self._path
