"""
Module: strategy/multi_strategy_engine.py
Sprint: 31 — T (Multi-Strategy Engine)
Description:
    MultiStrategyEngine: runs N registered strategies concurrently
    on each tick, manages capital allocation (equal / Sharpe-prop /
    manual), resolves signal conflicts (best-of / ignore), and emits
    STRATEGY_SIGNAL events.  Best-picker reallocates capital every 6h
    based on rolling Sharpe.

Usage:
    engine = MultiStrategyEngine()
    engine.register(MeanReversionStrategy("mr1"))
    engine.register(MomentumStrategy("mom1"))
    await engine.start()
    signals = await engine.tick({"symbol": "BTCUSDT", "z_score": 2.1, ...})
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any

from strategy.base_strategy import BaseStrategy, SignalDirection, SignalResult, StrategyMetrics

logger = logging.getLogger(__name__)

REALLOC_INTERVAL = 6 * 3600  # 6 hours in seconds


class CapitalSplitMode(str, Enum):
    EQUAL = "equal"
    SHARPE_PROP = "sharpe_prop"
    MANUAL = "manual"


class ConflictResolution(str, Enum):
    BEST_OF = "best_of"
    IGNORE = "ignore"


class MultiStrategyEngine:
    """Orchestrates multiple concurrent strategies with capital management."""

    def __init__(
        self,
        split_mode: CapitalSplitMode = CapitalSplitMode.EQUAL,
        conflict_resolution: ConflictResolution = ConflictResolution.BEST_OF,
        total_capital: float = 10_000.0,
    ) -> None:
        self._strategies: dict[str, BaseStrategy] = {}
        self._allocations: dict[str, float] = {}  # strategy_id -> fraction 0..1
        self._manual_allocations: dict[str, float] = {}
        self._split_mode = split_mode
        self._conflict_resolution = conflict_resolution
        self._total_capital = total_capital
        self._last_realloc: float = 0.0
        self._signal_history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, strategy: BaseStrategy, manual_alloc: float | None = None) -> None:
        sid = strategy.strategy_id
        self._strategies[sid] = strategy
        if manual_alloc is not None:
            self._manual_allocations[sid] = manual_alloc
        self._recompute_allocations()
        logger.info("[MSE] Registered strategy %s (total=%d)", sid, len(self._strategies))

    def unregister(self, strategy_id: str) -> None:
        self._strategies.pop(strategy_id, None)
        self._manual_allocations.pop(strategy_id, None)
        self._recompute_allocations()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._last_realloc = time.time()
        logger.info("[MSE] Started with %d strategies", len(self._strategies))

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    async def tick(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Run all active strategies on current market data snapshot."""
        if time.time() - self._last_realloc > REALLOC_INTERVAL:
            self._recompute_allocations()
            self._last_realloc = time.time()

        tasks = {
            sid: asyncio.create_task(s.generate_signal(data))
            for sid, s in self._strategies.items()
            if s.is_active()
        }
        results: dict[str, SignalResult] = {}
        for sid, task in tasks.items():
            try:
                results[sid] = await task
            except Exception as exc:  # noqa: BLE001
                logger.warning("[MSE] Strategy %s error: %s", sid, exc)

        resolved = self._resolve_conflicts(results, data.get("symbol", ""))
        events = []
        for sid, signal in resolved.items():
            alloc = self._allocations.get(sid, 0.0)
            event = {
                "event": "STRATEGY_SIGNAL",
                "strategy_id": sid,
                "symbol": signal.symbol,
                "direction": signal.direction,
                "strength": signal.strength,
                "capital_usd": self._total_capital * alloc,
                "metadata": signal.metadata,
                "ts": time.time(),
            }
            events.append(event)
            self._signal_history.append(event)
        if len(self._signal_history) > 500:
            self._signal_history = self._signal_history[-500:]
        return events

    # ------------------------------------------------------------------
    # Capital allocation
    # ------------------------------------------------------------------

    def _recompute_allocations(self) -> None:
        if not self._strategies:
            return
        sids = list(self._strategies.keys())
        if self._split_mode == CapitalSplitMode.MANUAL:
            total = sum(self._manual_allocations.values()) or 1.0
            self._allocations = {sid: self._manual_allocations.get(sid, 0) / total for sid in sids}
        elif self._split_mode == CapitalSplitMode.SHARPE_PROP:
            sharpes = {sid: max(self._strategies[sid].get_metrics().sharpe, 0.0001)
                       for sid in sids}
            total = sum(sharpes.values())
            self._allocations = {sid: v / total for sid, v in sharpes.items()}
        else:  # EQUAL
            eq = 1.0 / len(sids)
            self._allocations = {sid: eq for sid in sids}
        logger.info("[MSE] Recomputed allocations: %s", self._allocations)

    # ------------------------------------------------------------------
    # Conflict resolution
    # ------------------------------------------------------------------

    def _resolve_conflicts(
        self, results: dict[str, SignalResult], symbol: str
    ) -> dict[str, SignalResult]:
        """Detect directional conflicts on same symbol and resolve."""
        by_symbol: dict[str, list[tuple[str, SignalResult]]] = {}
        for sid, sig in results.items():
            if sig.direction in (SignalDirection.FLAT,):
                continue
            by_symbol.setdefault(sig.symbol, []).append((sid, sig))

        resolved: dict[str, SignalResult] = dict(results)

        for sym, sigs in by_symbol.items():
            directions = {sig.direction for _, sig in sigs}
            has_conflict = SignalDirection.LONG in directions and SignalDirection.SHORT in directions
            if not has_conflict:
                continue
            if self._conflict_resolution == ConflictResolution.IGNORE:
                for sid, _ in sigs:
                    resolved[sid] = SignalResult(
                        direction=SignalDirection.FLAT, symbol=sym
                    )
                logger.info("[MSE] Conflict on %s — all signals ignored", sym)
            else:  # BEST_OF
                best_sid, best_sig = max(
                    sigs, key=lambda x: self._strategies[x[0]].get_metrics().sharpe
                )
                for sid, sig in sigs:
                    if sid != best_sid:
                        resolved[sid] = SignalResult(
                            direction=SignalDirection.FLAT, symbol=sym
                        )
                logger.info(
                    "[MSE] Conflict on %s — best-of winner: %s", sym, best_sid
                )
        return resolved

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_metrics(self) -> list[dict[str, Any]]:
        return [
            {**s.get_metrics().__dict__, "allocation": self._allocations.get(sid, 0.0)}
            for sid, s in self._strategies.items()
        ]

    def get_signal_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._signal_history[-limit:]

    def get_strategy(self, strategy_id: str) -> BaseStrategy | None:
        return self._strategies.get(strategy_id)
