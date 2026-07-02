"""
QuantLuna — Adoption Engine (Sprint 17)

Decides what to do with ORPHAN positions found by PositionScanner.

Decisions:
  ADOPT        — position is healthy; register in checkpoint + set TP/SL
  CLOSE_NOW    — position is dangerous (loss too high, liq imminent); close immediately
  MONITOR_ONLY — position too small to manage; track without TP/SL

Usage:
    engine = AdoptionEngine(exchange, checkpoint, config=AdoptionConfig())
    results = await engine.process_report(scan_report)
    for r in results:
        if r.decision == AdoptionDecision.ADOPT:
            profit_optimizer.register(r)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional, Tuple

from loguru import logger

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


@dataclass
class AdoptionResult:
    position:     ExchangePosition
    decision:     AdoptionDecision
    reason:       str
    tp_price:     Optional[float] = None
    sl_price:     Optional[float] = None
    trailing_pct: float = 0.015


class AdoptionEngine:
    """
    Evaluates orphan positions and decides how to handle them.

    Parameters
    ----------
    exchange   : async CCXT exchange (for closing positions)
    checkpoint : checkpoint store
    config     : AdoptionConfig
    """

    def __init__(
        self,
        exchange: Any,
        checkpoint: Any,
        config: Optional[AdoptionConfig] = None,
    ) -> None:
        self._exchange   = exchange
        self._checkpoint = checkpoint
        self.cfg         = config or AdoptionConfig()

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

        # 4. ADOPT — calculate exits and save checkpoint
        tp, sl, trail = self._calculate_exits(pos)
        self._checkpoint.save_open(
            symbol=pos.symbol,
            side=pos.side,
            qty=pos.qty,
            entry_price=pos.entry_price,
            tp_price=tp,
            sl_price=sl,
        )
        logger.info(
            f"AdoptionEngine: ADOPT {pos.symbol} side={pos.side} "
            f"tp={tp:.4f} sl={sl:.4f}"
        )
        return AdoptionResult(
            position=pos,
            decision=AdoptionDecision.ADOPT,
            reason="healthy position",
            tp_price=tp,
            sl_price=sl,
            trailing_pct=trail,
        )

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
