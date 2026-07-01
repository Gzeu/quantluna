"""
execution/adoption_engine.py  —  QuantLuna Orphan Position Adoption Engine

Scop:
  Preia poziții orfane de pe exchange (întroduced manual sau de altă sesiune)
  şi le integrează în sistemul de management al botului:
    1. Calculează parametrii impliciti (hedge ratio, z-score estimat)
    2. Salvează checkpoint-ul corect
    3. Setează TrailingStopManager pentru profit maxim
    4. Trimite alert Telegram cu detalii complet

Decizii automate:
  - PnL > ADOPT_MIN_PNL_PCT  → adoptă şi gestionează
  - PnL în (CLOSE_LOSS_PCT, ADOPT_MIN_PCT)  → adoptă conservator
  - PnL < CLOSE_LOSS_PCT     → închidem imediat (reducere pierderi)
  - dist_liq < MIN_LIQ_DIST  → închidem imediat (risc lichidare iminent)

Usage:
    engine = AdoptionEngine(exchange, checkpoint, alert_cfg)
    results = await engine.adopt_all(scan_report.orphans)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from execution.checkpoint import PositionCheckpoint
from execution.position_scanner import ExchangePosition

logger = logging.getLogger(__name__)


class AdoptDecision(Enum):
    ADOPT        = "adopt"          # preia şi gestionează
    CLOSE_NOW    = "close_now"      # închidem imediat (pierdere sau risc liq)
    MONITOR_ONLY = "monitor_only"   # adoptă fără TP/SL activ (poziție prea mică)


@dataclass
class AdoptionResult:
    position: ExchangePosition
    decision: AdoptDecision
    reason: str
    tp_price: Optional[float] = None        # take-profit calculat
    sl_price: Optional[float] = None        # stop-loss calculat
    trailing_pct: Optional[float] = None    # trailing stop %
    estimated_hedge_ratio: float = 1.0
    success: bool = True
    error: Optional[str] = None


@dataclass
class AdoptionConfig:
    # Praguri de decizie
    adopt_min_pnl_pct: float  = -0.02    # adopt dacă PnL > -2%
    close_loss_pct: float     = -0.05    # închidem dacă PnL < -5%
    min_liq_distance_pct: float = 0.08   # închidem dacă dist liq < 8%
    min_notional_adopt: float = 5.0      # adoptă poziții > 5 USDT

    # Take-profit şi stop-loss automat
    tp_target_pct: float      = 0.04     # TP la +4% PnL
    sl_max_loss_pct: float    = 0.03     # SL la -3% față de entry
    trailing_activation_pct: float = 0.02  # activează trailing după +2%
    trailing_distance_pct: float   = 0.015 # trail cu 1.5% sub peak


class AdoptionEngine:
    """
    Preia şi gestionează poziții orfane.

    Args:
        exchange:   ccxt async exchange instance
        checkpoint: PositionCheckpoint instance
        alert_cfg:  AlertConfig pentru notificări (optional)
        config:     AdoptionConfig cu praguri
    """

    def __init__(
        self,
        exchange,
        checkpoint: PositionCheckpoint,
        alert_cfg=None,
        config: Optional[AdoptionConfig] = None,
    ) -> None:
        self._exchange  = exchange
        self._cp        = checkpoint
        self._alert     = alert_cfg
        self._cfg       = config or AdoptionConfig()

    async def adopt_all(
        self, orphans: List[ExchangePosition]
    ) -> List[AdoptionResult]:
        """
        Procesează toate pozițiile orfane şi returnează rezultatele.
        """
        results = []
        for pos in orphans:
            result = await self._process_one(pos)
            results.append(result)
        return results

    async def _process_one(self, pos: ExchangePosition) -> AdoptionResult:
        logger.info(
            f"[Adopt] Procesez: {pos.symbol} {pos.side} "
            f"qty={pos.qty:.4f} PnL={pos.unrealized_pnl:+.2f} ({pos.pnl_pct:+.1%})"
        )

        # --- Decizie ---
        decision, reason = self._decide(pos)

        if decision == AdoptDecision.CLOSE_NOW:
            success, error = await self._close_position(pos)
            result = AdoptionResult(
                position=pos,
                decision=decision,
                reason=reason,
                success=success,
                error=error,
            )
            await self._alert_result(result)
            return result

        # --- Calcule TP/SL/Trailing ---
        tp_price, sl_price, trailing_pct = self._calculate_exits(pos)

        # --- Salvează checkpoint ---
        side_y = 'buy' if pos.side == 'long' else 'sell'
        self._cp.save_open(
            sym_y=pos.symbol,
            sym_x=pos.symbol,    # poziție unilaterală adoptata
            side_y=side_y,
            side_x='none',
            qty_y=pos.qty,
            qty_x=0.0,
            entry_price_y=pos.entry_price,
            entry_price_x=0.0,
            entry_zscore=0.0,
            hedge_ratio=1.0,
            notional_usdt=pos.notional_usdt,
            meta={
                'adopted': True,
                'adopted_at': time.time(),
                'tp_price': tp_price,
                'sl_price': sl_price,
                'trailing_pct': trailing_pct,
                'original_pnl_at_adoption': pos.unrealized_pnl,
                'decision': decision.value,
            },
        )

        result = AdoptionResult(
            position=pos,
            decision=decision,
            reason=reason,
            tp_price=tp_price,
            sl_price=sl_price,
            trailing_pct=trailing_pct,
            estimated_hedge_ratio=1.0,
            success=True,
        )
        await self._alert_result(result)
        return result

    def _decide(
        self, pos: ExchangePosition
    ) -> tuple[AdoptDecision, str]:
        cfg = self._cfg

        # Risc de lichidare iminentă
        if 0 < pos.distance_to_liq_pct < cfg.min_liq_distance_pct:
            return (
                AdoptDecision.CLOSE_NOW,
                f"Lichidare iminentă: dist={pos.distance_to_liq_pct:.1%} < {cfg.min_liq_distance_pct:.1%}",
            )

        # Pierdere excesivă
        if pos.pnl_pct < cfg.close_loss_pct:
            return (
                AdoptDecision.CLOSE_NOW,
                f"Pierdere excesivă: {pos.pnl_pct:.1%} < {cfg.close_loss_pct:.1%} (SL automat)",
            )

        # Notional prea mică — monitor fără TP/SL activ
        if pos.notional_usdt < cfg.min_notional_adopt:
            return (
                AdoptDecision.MONITOR_ONLY,
                f"Notional prea mic: {pos.notional_usdt:.1f} USDT < {cfg.min_notional_adopt:.1f} USDT",
            )

        # Adopt standard
        return (
            AdoptDecision.ADOPT,
            f"Adoptă poziția: PnL={pos.pnl_pct:+.1%} dist_liq={pos.distance_to_liq_pct:.1%}",
        )

    def _calculate_exits(
        self, pos: ExchangePosition
    ) -> tuple[Optional[float], Optional[float], Optional[float]]:
        cfg = self._cfg
        entry = pos.entry_price

        if pos.side == 'long':
            tp_price     = entry * (1 + cfg.tp_target_pct)
            sl_price     = entry * (1 - cfg.sl_max_loss_pct)
        else:  # short
            tp_price     = entry * (1 - cfg.tp_target_pct)
            sl_price     = entry * (1 + cfg.sl_max_loss_pct)

        trailing_pct = cfg.trailing_distance_pct

        logger.info(
            f"[Adopt] Exits calculate: entry={entry:.4f} "
            f"TP={tp_price:.4f} SL={sl_price:.4f} trail={trailing_pct:.1%}"
        )
        return tp_price, sl_price, trailing_pct

    async def _close_position(
        self, pos: ExchangePosition
    ) -> tuple[bool, Optional[str]]:
        """închidem imediat o poziție orfană risc/pierdere."""
        try:
            close_side = 'sell' if pos.side == 'long' else 'buy'
            symbol = pos.symbol

            order = await self._exchange.create_order(
                symbol=symbol,
                type='market',
                side=close_side,
                amount=pos.qty,
                params={'reduceOnly': True},
            )
            logger.warning(
                f"[Adopt] CLOSE_NOW executat: {symbol} {close_side} "
                f"qty={pos.qty:.4f} | order_id={order.get('id', 'unknown')}"
            )
            return True, None
        except Exception as exc:
            error = f"close_position({pos.symbol}) failed: {exc}"
            logger.error(f"[Adopt] {error}")
            return False, error

    async def _alert_result(self, result: AdoptionResult) -> None:
        if not self._alert:
            return
        pos = result.position
        emoji = {
            AdoptDecision.ADOPT:        '✅',
            AdoptDecision.CLOSE_NOW:    '⚠️',
            AdoptDecision.MONITOR_ONLY: '👁️',
        }.get(result.decision, 'ℹ️')

        lines = [
            f"{emoji} QuantLuna Adoption [{result.decision.value.upper()}]",
            f"Symbol: {pos.symbol} {pos.side.upper()}",
            f"Qty: {pos.qty:.4f} | Notional: {pos.notional_usdt:.1f} USDT",
            f"Entry: {pos.entry_price:.4f} | Mark: {pos.mark_price:.4f}",
            f"PnL: {pos.unrealized_pnl:+.2f} USDT ({pos.pnl_pct:+.1%})",
            f"Motiv: {result.reason}",
        ]
        if result.tp_price:
            lines.append(f"TP: {result.tp_price:.4f} | SL: {result.sl_price:.4f} | Trail: {result.trailing_pct:.1%}")
        if result.error:
            lines.append(f"❌ Eroare: {result.error}")

        try:
            from execution.live_trader import _send_alert
            await _send_alert(self._alert, '\n'.join(lines))
        except Exception as exc:
            logger.warning(f"[Adopt] alert failed: {exc}")
