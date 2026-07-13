"""
QuantLuna — Position Store
Sprint 30

Persists open positions using the existing AbstractStore interface.
Supports memory, SQLite, and Redis backends.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from core.store import AbstractStore, MemoryStore, SQLiteStore, RedisStore, _BACKEND, _DB_PATH

logger = logging.getLogger(__name__)


class PositionStore:
    """
    Persists open positions for paper trading engine.

    Uses the same backend as JobStore/SelectorStore (QUANTLUNA_STORE_BACKEND env var).
    """

    def __init__(self, backend: Optional[str] = None) -> None:
        b = (backend or _BACKEND).lower()
        if b == "redis":
            self._store: AbstractStore = RedisStore(prefix="ql_positions")
        elif b == "sqlite":
            self._store = SQLiteStore(table="positions", db_path=_DB_PATH)
        else:
            # Default to SQLite for persistence across restarts
            self._store = SQLiteStore(table="positions", db_path=_DB_PATH)

    def save_positions(self, positions: Dict[str, Any]) -> None:
        """Save all open positions."""
        serializable = {}
        for symbol, pos in positions.items():
            if hasattr(pos, "to_dict"):
                serializable[symbol] = pos.to_dict()
            else:
                serializable[symbol] = pos
        self._store.set("open_positions", serializable)
        logger.debug(f"PositionStore: saved {len(serializable)} positions")

    def load_positions(self) -> Dict[str, Any]:
        """Load previously saved positions. Returns empty dict if none."""
        data = self._store.get("open_positions")
        if data is None:
            return {}
        logger.info(f"PositionStore: loaded {len(data)} positions from storage")
        return data

    def clear(self) -> None:
        """Delete all saved positions."""
        self._store.delete("open_positions")
        logger.info("PositionStore: cleared all positions")

    def save_bybit_positions(self, positions: list[dict]) -> None:
        """
        Save positions fetched from Bybit API.

        Args:
            positions: list of dicts from get_open_positions()
        """
        serializable = {}
        for pos in positions:
            symbol = pos.get("symbol", "")
            if not symbol:
                continue
            serializable[symbol] = {
                "symbol":        symbol,
                "side":          pos.get("side", ""),
                "size":          float(pos.get("size", 0)),
                "entry_price":   float(pos.get("entryPrice", 0)),
                "unrealised_pnl": float(pos.get("unrealisedPnl", 0)),
                "leverage":      float(pos.get("leverage", 1)),
            }
        self._store.set("bybit_positions", serializable)
        logger.info(f"PositionStore: saved {len(serializable)} Bybit positions")

    def load_bybit_positions(self) -> list[dict]:
        """
        Load previously saved Bybit positions.

        Returns:
            list of dicts compatible with get_open_positions() format
        """
        data = self._store.get("bybit_positions")
        if data is None:
            return []
        positions = []
        for symbol, pos_dict in data.items():
            positions.append({
                "symbol":        symbol,
                "side":          pos_dict.get("side", ""),
                "size":          float(pos_dict.get("size", 0)),
                "entryPrice":    float(pos_dict.get("entry_price", 0)),
                "unrealisedPnl": float(pos_dict.get("unrealised_pnl", 0)),
                "leverage":      float(pos_dict.get("leverage", 1)),
            })
        logger.info(f"PositionStore: loaded {len(positions)} Bybit positions")
        return positions
