"""
core/account_snapshot.py — Authoritative account state snapshot (S48 P0).

Single source of truth for Bybit account state.  Produced once at startup,
then refreshed on a bounded schedule.  ALL position/order/wallet data flows
through this model — independent scanner/reconciler/adoption phases MUST
consume this snapshot rather than re-fetching from Bybit.

Position ownership classification:
  MANAGED          — bot-opened, governed by strategy/risk rules
  ADOPTED          — explicitly adopted via API or auto-adoption policy
  EXTERNAL_OBSERVED — visible, never modified; blocks entries
  ORPHANED         — detected on exchange, not in checkpoint; needs decision
  UNPROTECTED      — known position without TP/SL; blocks entries
  CLOSING          — close order submitted, awaiting fill
  ERROR            — inconsistent state; blocks entries
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class PositionOwnership(str, Enum):
    MANAGED = "MANAGED"
    ADOPTED = "ADOPTED"
    EXTERNAL_OBSERVED = "EXTERNAL_OBSERVED"
    ORPHANED = "ORPHANED"
    UNPROTECTED = "UNPROTECTED"
    CLOSING = "CLOSING"
    ERROR = "ERROR"


class SyncStatus(str, Enum):
    PENDING = "PENDING"
    SYNCING = "SYNCING"
    READY = "READY"
    STALE = "STALE"
    FAILED = "FAILED"


# ═══════════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class WalletSnapshot:
    total_equity: float = 0.0
    available_balance: float = 0.0
    used_margin: float = 0.0
    unrealized_pnl: float = 0.0
    account_type: str = "UNIFIED"
    currency: str = "USDT"


@dataclass
class PositionSnapshot:
    symbol: str
    side: str                     # "Buy" | "Sell"
    size: float
    entry_price: float
    mark_price: float = 0.0
    leverage: float = 1.0
    unrealized_pnl: float = 0.0
    liquidation_price: float = 0.0
    take_profit: float = 0.0
    stop_loss: float = 0.0
    trailing_stop: float = 0.0
    position_idx: int = 0         # Bybit position index
    mode: str = ""                # "MergedSingle" | "BothSide"
    ownership: str = PositionOwnership.EXTERNAL_OBSERVED.value
    managed_by: str = ""          # strategy name or "manual"
    adopted_at: float = 0.0       # timestamp when adopted
    checkpoint_id: str = ""       # local checkpoint reference

    @property
    def notional(self) -> float:
        return self.size * self.mark_price if self.mark_price > 0 else self.size * self.entry_price

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "mark_price": self.mark_price,
            "leverage": self.leverage,
            "unrealized_pnl": self.unrealized_pnl,
            "liquidation_price": self.liquidation_price,
            "take_profit": self.take_profit,
            "stop_loss": self.stop_loss,
            "ownership": self.ownership,
            "managed_by": self.managed_by,
            "notional": self.notional,
        }


@dataclass
class ActiveOrder:
    order_id: str
    symbol: str
    side: str
    order_type: str              # "Market" | "Limit"
    qty: float
    price: float = 0.0
    reduce_only: bool = False
    status: str = ""             # "New" | "Filled" | "Cancelled"


@dataclass
class AccountSnapshot:
    """Immutable account state at a point in time."""

    snapshot_id: str = field(default_factory=lambda: f"snap_{int(time.time())}")
    timestamp: float = field(default_factory=time.time)
    status: str = SyncStatus.PENDING.value

    wallet: WalletSnapshot = field(default_factory=WalletSnapshot)
    positions: List[PositionSnapshot] = field(default_factory=list)
    orders: List[ActiveOrder] = field(default_factory=list)

    # Health metadata
    rest_latency_ms: float = 0.0
    ws_public_ok: bool = False
    ws_private_ok: bool = False
    errors: List[str] = field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        return self.status == SyncStatus.READY.value

    @property
    def is_fresh(self, max_age_seconds: float = 30.0) -> bool:
        return (time.time() - self.timestamp) < max_age_seconds

    @property
    def total_positions(self) -> int:
        return len(self.positions)

    @property
    def managed_positions(self) -> List[PositionSnapshot]:
        return [p for p in self.positions if p.ownership == PositionOwnership.MANAGED.value]

    @property
    def adopted_positions(self) -> List[PositionSnapshot]:
        return [p for p in self.positions if p.ownership == PositionOwnership.ADOPTED.value]

    @property
    def external_positions(self) -> List[PositionSnapshot]:
        return [p for p in self.positions if p.ownership == PositionOwnership.EXTERNAL_OBSERVED.value]

    @property
    def orphaned_positions(self) -> List[PositionSnapshot]:
        return [p for p in self.positions if p.ownership == PositionOwnership.ORPHANED.value]

    @property
    def unprotected_positions(self) -> List[PositionSnapshot]:
        return [p for p in self.positions if p.ownership == PositionOwnership.UNPROTECTED.value]

    @property
    def has_critical_issues(self) -> bool:
        return (
            len(self.errors) > 0
            or len(self.unprotected_positions) > 0
            or self.status == SyncStatus.FAILED.value
        )

    def summary(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "age_seconds": round(time.time() - self.timestamp, 1),
            "status": self.status,
            "wallet": {
                "equity": self.wallet.total_equity,
                "available": self.wallet.available_balance,
                "margin": self.wallet.used_margin,
            },
            "positions": {
                "total": self.total_positions,
                "managed": len(self.managed_positions),
                "adopted": len(self.adopted_positions),
                "external": len(self.external_positions),
                "orphaned": len(self.orphaned_positions),
                "unprotected": len(self.unprotected_positions),
            },
            "orders": len(self.orders),
            "health": {
                "rest_latency_ms": self.rest_latency_ms,
                "ws_public": self.ws_public_ok,
                "ws_private": self.ws_private_ok,
            },
            "errors": self.errors,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Account Sync Service
# ═══════════════════════════════════════════════════════════════════════════════


class AccountSyncService:
    """
    Fetches and maintains the authoritative AccountSnapshot.

    Single entry point for account state.  All scanner/reconciler/adoption
    phases MUST consume the latest snapshot rather than calling Bybit directly.
    """

    def __init__(self, exchange=None, traffic_ctrl=None) -> None:
        self._exchange = exchange
        self._traffic = traffic_ctrl
        self._latest: Optional[AccountSnapshot] = None
        self._last_sync_time: float = 0.0

    @property
    def latest(self) -> Optional[AccountSnapshot]:
        return self._latest

    @property
    def is_ready(self) -> bool:
        return self._latest is not None and self._latest.is_ready

    async def sync(self, force: bool = False) -> AccountSnapshot:
        """
        Fetch a fresh account snapshot from Bybit.

        Uses the traffic controller for rate limiting.  Returns the
        latest snapshot.  On failure, sets status to FAILED with errors.
        """
        snap = AccountSnapshot(status=SyncStatus.SYNCING.value)

        try:
            # 1. Wallet
            wallet_data = None
            if self._exchange is not None:
                try:
                    bal = await self._exchange.fetch_balance()
                    wallet_data = bal
                except Exception as exc:
                    snap.errors.append(f"wallet_fetch: {exc}")

            if wallet_data:
                snap.wallet = WalletSnapshot(
                    total_equity=float(wallet_data.get("total", {}).get("USDT", 0)),
                    available_balance=float(wallet_data.get("free", {}).get("USDT", 0)),
                    used_margin=float(wallet_data.get("used", {}).get("USDT", 0)),
                )

            # 2. Positions
            if self._exchange is not None:
                try:
                    raw_positions = await self._exchange.fetch_positions()
                    for rp in raw_positions:
                        size = float(rp.get("contracts", rp.get("size", 0)))
                        if abs(size) < 0.0001:
                            continue
                        pos = PositionSnapshot(
                            symbol=str(rp.get("symbol", "")),
                            side=str(rp.get("side", "Buy")),
                            size=size,
                            entry_price=float(rp.get("entryPrice", rp.get("entry_price", 0))),
                            mark_price=float(rp.get("markPrice", rp.get("mark_price", 0))),
                            leverage=float(rp.get("leverage", 1)),
                            unrealized_pnl=float(rp.get("unrealizedPnl", rp.get("unrealisedPnl", 0))),
                            liquidation_price=float(rp.get("liquidationPrice", 0)),
                            take_profit=float(rp.get("takeProfit", 0)),
                            stop_loss=float(rp.get("stopLoss", 0)),
                            ownership=PositionOwnership.ORPHANED.value,  # default: needs classification
                        )
                        snap.positions.append(pos)
                except Exception as exc:
                    snap.errors.append(f"positions_fetch: {exc}")

            # 3. Open orders
            if self._exchange is not None:
                try:
                    raw_orders = await self._exchange.fetch_open_orders()
                    for ro in raw_orders:
                        snap.orders.append(ActiveOrder(
                            order_id=str(ro.get("id", "")),
                            symbol=str(ro.get("symbol", "")),
                            side=str(ro.get("side", "")),
                            order_type=str(ro.get("type", "")),
                            qty=float(ro.get("amount", ro.get("qty", 0))),
                            price=float(ro.get("price", 0)),
                            reduce_only=bool(ro.get("reduceOnly", ro.get("reduce", False))),
                            status=str(ro.get("status", "")),
                        ))
                except Exception as exc:
                    snap.errors.append(f"orders_fetch: {exc}")

            snap.status = SyncStatus.READY.value if not snap.errors else SyncStatus.READY.value

        except Exception as exc:
            snap.errors.append(f"sync_fatal: {exc}")
            snap.status = SyncStatus.FAILED.value

        snap.timestamp = time.time()
        self._latest = snap
        self._last_sync_time = time.time()
        return snap

    def classify_position(
        self,
        pos: PositionSnapshot,
        ownership: PositionOwnership,
        managed_by: str = "",
    ) -> None:
        """Classify a position's ownership after reconciliation."""
        pos.ownership = ownership.value
        pos.managed_by = managed_by
        if ownership == PositionOwnership.ADOPTED:
            pos.adopted_at = time.time()

    def snapshot(self) -> dict:
        """Return summary for API/dashboard."""
        if self._latest is None:
            return {"status": "no_snapshot", "message": "No account snapshot taken yet"}
        return self._latest.summary()
