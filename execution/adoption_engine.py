"""
QuantLuna — Adoption Engine (Sprint 17 + Sprint 28)

Decides what to do with ORPHAN positions found by PositionScanner.

Decisions:
  ADOPT        — position is healthy; register in checkpoint + set TP/SL via order router
  CLOSE_NOW    — position is dangerous (loss too high, liq imminent); close immediately
  MONITOR_ONLY — position too small to manage; track without TP/SL

Sprint 28 additions
-------------------
adopt_and_protect(position_dict) — unified method that:
  1. Adopts the market trade into a local OrderRecord (status=FILLED)
  2. Places SL and TP reduce-only orders on the exchange via order_manager
  3. Registers on_fill callbacks so that when either exit order fills,
     the cycle restart is triggered automatically

Usage:
    engine = AdoptionEngine(exchange, checkpoint, order_manager, config=AdoptionConfig())
    results = await engine.process_report(scan_report)
    for r in results:
        if r.decision == AdoptionDecision.ADOPT:
            # TP/SL already placed, cycle-restart already wired
            pass
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from execution.order_manager import OrderManager, OrderRecord, OrderRequest, OrderStatus
from execution.position_scanner import ExchangePosition, ScanReport


class AdoptionDecision(str, Enum):
    ADOPT        = "adopt"
    CLOSE_NOW    = "close_now"
    MONITOR_ONLY = "monitor_only"


@dataclass
class AdoptionConfig:
    # Close immediately if PnL % is worse than this (e.g. -0.05 = -5%)
    close_loss_pct: float = -0.05
    # Close immediately if distance to liquidation is below this fraction
    min_liq_distance_pct: float = 0.08
    # Don't adopt if notional USDT is below this
    min_notional_adopt: float = 5.0
    # TP target as fraction of entry price
    tp_target_pct: float = 0.04
    # Max loss before SL triggers
    sl_max_loss_pct: float = 0.03
    # Trailing stop pct from peak
    trailing_pct: float = 0.015
    # Cooldown seconds before restarting cycle after an adopted trade closes
    restart_cooldown_s: float = 10.0


@dataclass
class AdoptionResult:
    position:     ExchangePosition
    decision:     AdoptionDecision
    reason:       str
    tp_price:     Optional[float] = None
    sl_price:     Optional[float] = None
    trailing_pct: float = 0.015
    # Sprint 28: local_ids of the entry (adopted) record and exit orders
    adopted_local_id: Optional[str] = None
    tp_local_id:      Optional[str] = None
    sl_local_id:      Optional[str] = None


class AdoptionEngine:
    """
    Evaluates orphan positions and decides how to handle them.

    Parameters
    ----------
    exchange      : async CCXT exchange (for emergency closing positions)
    checkpoint    : checkpoint store
    order_manager : OrderManager instance — used to place TP/SL orders and
                    wire cycle-restart callbacks (Sprint 28, optional for
                    backward-compat; if None, falls back to legacy behaviour)
    config        : AdoptionConfig
    on_cycle_restart : optional async callable(symbol: str) invoked when an
                       adopted trade closes and the bot should begin a fresh cycle
    """

    def __init__(
        self,
        exchange: Any,
        checkpoint: Any,
        order_manager: Optional[OrderManager] = None,
        config: Optional[AdoptionConfig] = None,
        on_cycle_restart: Optional[Any] = None,
    ) -> None:
        self._exchange      = exchange
        self._checkpoint    = checkpoint
        self._om            = order_manager
        self.cfg            = config or AdoptionConfig()
        self._on_restart    = on_cycle_restart

        # Sprint 28: register close callback once, handled via tag matching
        if self._om is not None:
            self._om.register_on_fill(self._on_exit_filled)
            self._om.register_on_close(self._on_exit_closed)

    # ------------------------------------------------------------------
    # Main processing
    # ------------------------------------------------------------------

    async def process_report(self, report: ScanReport) -> List[AdoptionResult]:
        """Process all orphans in a ScanReport."""
        results = []
        for pos in report.orphans:
            result = await self._process_one(pos)
            results.append(result)
        return results

    async def _process_one(self, pos: ExchangePosition) -> AdoptionResult:
        cfg = self.cfg

        # 1. Notional too small — monitor only
        if pos.notional_usdt < cfg.min_notional_adopt:
            return AdoptionResult(
                position=pos,
                decision=AdoptionDecision.MONITOR_ONLY,
                reason=f"notional {pos.notional_usdt:.2f} < min {cfg.min_notional_adopt}",
            )

        # 2. Liquidation imminent
        if pos.distance_to_liq_pct < cfg.min_liq_distance_pct:
            await self._close_position(pos)
            return AdoptionResult(
                position=pos,
                decision=AdoptionDecision.CLOSE_NOW,
                reason=f"liq distance {pos.distance_to_liq_pct:.2%} < {cfg.min_liq_distance_pct:.2%}",
            )

        # 3. Loss threshold breached
        if pos.pnl_pct < cfg.close_loss_pct:
            await self._close_position(pos)
            return AdoptionResult(
                position=pos,
                decision=AdoptionDecision.CLOSE_NOW,
                reason=f"pnl {pos.pnl_pct:.2%} < threshold {cfg.close_loss_pct:.2%}",
            )

        # 4. ADOPT — calculate exits, save checkpoint, place TP/SL
        tp, sl, trail = self._calculate_exits(pos)
        self._checkpoint.save_open_single(
            symbol=pos.symbol,
            side=pos.side,
            qty=pos.qty,
            entry_price=pos.entry_price,
            notional_usdt=pos.notional_usdt,
        )
        logger.info(
            f"AdoptionEngine: ADOPT {pos.symbol} side={pos.side} "
            f"tp={tp:.4f} sl={sl:.4f}"
        )

        result = AdoptionResult(
            position=pos,
            decision=AdoptionDecision.ADOPT,
            reason="healthy position",
            tp_price=tp,
            sl_price=sl,
            trailing_pct=trail,
        )

        # Sprint 28: place TP/SL via OrderManager if available
        if self._om is not None:
            adopted_id, tp_id, sl_id = await self._place_protection_orders(pos, tp, sl)
            result.adopted_local_id = adopted_id
            result.tp_local_id      = tp_id
            result.sl_local_id      = sl_id

        return result

    # ------------------------------------------------------------------
    # Sprint 28: adopt_and_protect — public shortcut
    # ------------------------------------------------------------------

    async def adopt_and_protect(
        self,
        position: Dict,
        venue: str = "bybit",
    ) -> AdoptionResult:
        """
        Unified entry-point for adopting a position dict (from external source,
        e.g. WebSocket position update) and immediately placing protection orders.

        Constructs an ExchangePosition-compatible object from the raw dict and
        calls the full adoption pipeline.

        Parameters
        ----------
        position : dict with keys:
            symbol       : str   e.g. "BTCUSDT"
            side         : str   'long' | 'short'
            qty          : float
            entry_price  : float
            notional_usdt: float (optional, computed if missing)
            pnl_pct      : float (optional, default 0.0)
            distance_to_liq_pct: float (optional, default 1.0)
        venue    : exchange venue name for order routing
        """
        from execution.position_scanner import ExchangePosition

        notional = position.get(
            "notional_usdt",
            position.get("qty", 0.0) * position.get("entry_price", 0.0),
        )
        qty_f = float(position["qty"])
        entry_f = float(position["entry_price"])
        pos = ExchangePosition(
            symbol=position["symbol"],
            side=position["side"],
            qty=qty_f,
            entry_price=entry_f,
            mark_price=float(position.get("mark_price", entry_f)),
            unrealized_pnl=float(position.get("unrealized_pnl", 0.0)),
            leverage=float(position.get("leverage", 1)),
            notional_usdt=float(notional),
            liquidation_price=float(position.get("liquidation_price", 0.0)),
            margin_used=float(position.get("margin_used", 0.0)),
        )
        # Attach venue to position so _place_protection_orders can use it
        pos._venue = venue  # type: ignore[attr-defined]
        return await self._process_one(pos)

    # ------------------------------------------------------------------
    # Sprint 28: Protection order placement
    # ------------------------------------------------------------------

    async def _place_protection_orders(
        self,
        pos: ExchangePosition,
        tp_price: float,
        sl_price: float,
    ) -> Tuple[str, str, str]:
        """
        Registers the adopted fill and places TP + SL reduce-only orders
        via OrderManager.  Returns (adopted_local_id, tp_local_id, sl_local_id).
        """
        venue = getattr(pos, "_venue", getattr(pos, "venue", "bybit"))
        close_side = "sell" if pos.side == "long" else "buy"

        # 1. Register the adopted entry as a synthetic FILLED record
        entry_req = OrderRequest(
            symbol=pos.symbol,
            side="buy" if pos.side == "long" else "sell",
            qty=pos.qty,
            venue=venue,
            order_type="market",
            price=pos.entry_price,
            tag="adopted",
            client_id=f"adopted_{pos.symbol}_{int(time.time())}",
        )
        adopted_id = entry_req.client_id or ""
        from execution.order_manager import OrderRecord, OrderStatus
        entry_record = OrderRecord(
            local_id=adopted_id,
            request=entry_req,
            status=OrderStatus.FILLED,
            fill_price=pos.entry_price,
            fill_qty=pos.qty,
            filled_at=time.time(),
        )
        async with self._om._lock:
            self._om._orders[adopted_id] = entry_record
        logger.info(
            f"AdoptionEngine: registered adopted entry local_id={adopted_id} "
            f"{pos.symbol} {pos.side} qty={pos.qty} entry={pos.entry_price}"
        )

        # 2. Place TP limit order (reduce-only)
        tp_req = OrderRequest(
            symbol=pos.symbol,
            side=close_side,
            qty=pos.qty,
            venue=venue,
            order_type="limit",
            price=tp_price,
            reduce_only=True,
            tag="adopted_tp",
            client_id=f"tp_{pos.symbol}_{int(time.time())}",
        )
        tp_id = await self._om.submit(tp_req)
        logger.info(
            f"AdoptionEngine: TP order placed local_id={tp_id} "
            f"symbol={pos.symbol} price={tp_price:.4f}"
        )

        # 3. Place SL limit order (reduce-only)
        sl_req = OrderRequest(
            symbol=pos.symbol,
            side=close_side,
            qty=pos.qty,
            venue=venue,
            order_type="limit",
            price=sl_price,
            reduce_only=True,
            tag="adopted_sl",
            client_id=f"sl_{pos.symbol}_{int(time.time())}",
        )
        sl_id = await self._om.submit(sl_req)
        logger.info(
            f"AdoptionEngine: SL order placed local_id={sl_id} "
            f"symbol={pos.symbol} price={sl_price:.4f}"
        )

        return adopted_id, tp_id, sl_id

    # ------------------------------------------------------------------
    # Sprint 28: Exit-fill callbacks — trigger cycle restart
    # ------------------------------------------------------------------

    async def _on_exit_filled(self, record: OrderRecord) -> None:
        """
        Called by OrderManager when any order fills.
        If it is an adopted TP or SL order that filled, trigger cycle restart.
        """
        if record.request.tag not in ("adopted_tp", "adopted_sl"):
            return
        logger.info(
            f"AdoptionEngine: exit order FILLED [{record.request.tag}] "
            f"local_id={record.local_id} symbol={record.request.symbol} "
            f"fill_price={record.fill_price}"
        )
        await self._trigger_restart(record.request.symbol, reason=record.request.tag)

    async def _on_exit_closed(self, record: OrderRecord) -> None:
        """
        Called by OrderManager when any order is cancelled / failed / timed-out.
        If the cancel is for our SL/TP (e.g. manual close), also restart.
        """
        if record.request.tag not in ("adopted_tp", "adopted_sl"):
            return
        logger.warning(
            f"AdoptionEngine: exit order CLOSED (non-fill) [{record.request.tag}] "
            f"status={record.status} local_id={record.local_id} — "
            f"position likely closed externally, restarting cycle"
        )
        await self._trigger_restart(record.request.symbol, reason="external_close")

    async def _trigger_restart(self, symbol: str, reason: str = "") -> None:
        """Wait cooldown then invoke on_cycle_restart callback if registered."""
        import asyncio
        logger.info(
            f"AdoptionEngine: scheduling cycle restart for {symbol} "
            f"in {self.cfg.restart_cooldown_s}s (reason={reason})"
        )
        await asyncio.sleep(self.cfg.restart_cooldown_s)
        if self._on_restart is not None:
            try:
                await self._on_restart(symbol)
            except Exception as exc:
                logger.error(f"AdoptionEngine: on_cycle_restart callback failed: {exc}")
        else:
            logger.warning(
                f"AdoptionEngine: no on_cycle_restart callback registered — "
                f"cycle for {symbol} NOT restarted. Pass on_cycle_restart= at init."
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _calculate_exits(
        self, pos: ExchangePosition
    ) -> Tuple[float, float, float]:
        cfg = self.cfg
        entry = pos.entry_price
        if pos.side == "long":
            tp = entry * (1.0 + cfg.tp_target_pct)
            sl = entry * (1.0 - cfg.sl_max_loss_pct)
        else:
            tp = entry * (1.0 - cfg.tp_target_pct)
            sl = entry * (1.0 + cfg.sl_max_loss_pct)
        return tp, sl, cfg.trailing_pct

    async def _close_position(self, pos: ExchangePosition) -> None:
        close_side = "sell" if pos.side == "long" else "buy"
        try:
            await self._exchange.create_order(
                pos.symbol, "market", close_side, pos.qty,
                params={"reduceOnly": True},
            )
            logger.warning(
                f"AdoptionEngine: closed orphan {pos.symbol} side={pos.side} qty={pos.qty}"
            )
        except Exception as exc:
            logger.error(f"AdoptionEngine: failed to close {pos.symbol}: {exc}")
