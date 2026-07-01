"""
execution/partial_exit_handler.py  —  QuantLuna Partial Exit Handler

Scop:
  Modul separat care gestioneaza semnalul Signal.PARTIAL_EXIT (v4).
  Importat de LiveTrader si PaperTrader pentru a evita duplicarea logicii.

  La PARTIAL_EXIT:
    1. Calculeaza qty de inchis (partial_exit_pct % din pozitia Y si X)
    2. Plaseaza ordine reduce-only pe ambele legs
    3. Actualizeaza checkpoint cu qty ramasa
    4. Trimite alert Telegram
    5. Logheza evenimentul

Usage (in LiveTrader._handle_signal):
    from execution.partial_exit_handler import handle_partial_exit

    if signal.signal == Signal.PARTIAL_EXIT:
        result = await handle_partial_exit(
            signal=signal,
            position=current_position,
            exchange=self._exchange,
            checkpoint=self._checkpoint,
            alert_cfg=self._alert_cfg,
        )
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Any

from strategy.signal import TradeSignal, Signal

logger = logging.getLogger(__name__)


@dataclass
class PartialExitResult:
    executed: bool
    qty_y_closed: float
    qty_x_closed: float
    close_pct: float
    reason: str
    error: Optional[str] = None
    order_y_id: Optional[str] = None
    order_x_id: Optional[str] = None


async def handle_partial_exit(
    signal: TradeSignal,
    position: Any,
    exchange: Any,
    checkpoint: Any,
    alert_cfg: Optional[Any] = None,
) -> PartialExitResult:
    """
    Executa partial exit pentru o pozitie pair-trading.

    Args:
        signal:     TradeSignal cu signal=PARTIAL_EXIT si partial_close_pct setat
        position:   obiect/dict cu sym_y, sym_x, qty_y, qty_x, side_y, side_x
        exchange:   ccxt async exchange
        checkpoint: PositionCheckpoint pentru actualizare qty
        alert_cfg:  AlertConfig Telegram (optional)

    Returns:
        PartialExitResult cu detalii despre executie
    """
    if signal.signal != Signal.PARTIAL_EXIT:
        return PartialExitResult(
            executed=False, qty_y_closed=0.0, qty_x_closed=0.0,
            close_pct=0.0, reason="not_partial_exit_signal",
        )

    close_pct = signal.partial_close_pct or 0.50

    # Extrage informatii pozitie (suporta dict sau obiect)
    if isinstance(position, dict):
        sym_y  = position.get('sym_y', '')
        sym_x  = position.get('sym_x', '')
        qty_y  = float(position.get('qty_y', 0.0))
        qty_x  = float(position.get('qty_x', 0.0))
        side_y = position.get('side_y', 'buy')
        side_x = position.get('side_x', 'sell')
    else:
        sym_y  = getattr(position, 'sym_y', '')
        sym_x  = getattr(position, 'sym_x', '')
        qty_y  = float(getattr(position, 'qty_y', 0.0))
        qty_x  = float(getattr(position, 'qty_x', 0.0))
        side_y = getattr(position, 'side_y', 'buy')
        side_x = getattr(position, 'side_x', 'sell')

    qty_y_close = qty_y * close_pct
    qty_x_close = qty_x * close_pct

    # Directii de inchidere (invers fata de deschidere)
    close_side_y = 'sell' if side_y in ('buy', 'long')  else 'buy'
    close_side_x = 'buy'  if side_x in ('sell', 'short') else 'sell'

    logger.info(
        f"[PartialExit] {close_pct:.0%} din pozitie: "
        f"{sym_y} {close_side_y} {qty_y_close:.6f} | "
        f"{sym_x} {close_side_x} {qty_x_close:.6f} | "
        f"z={signal.zscore:.3f}"
    )

    order_y_id: Optional[str] = None
    order_x_id: Optional[str] = None

    # --- Plasare ordine ---
    try:
        if qty_y_close > 1e-8:
            order_y = await exchange.create_order(
                symbol=sym_y,
                type='market',
                side=close_side_y,
                amount=qty_y_close,
                params={'reduceOnly': True},
            )
            order_y_id = str(order_y.get('id', 'unknown'))
            logger.info(f"[PartialExit] Y filled: {sym_y} {order_y_id}")

        if qty_x_close > 1e-8:
            order_x = await exchange.create_order(
                symbol=sym_x,
                type='market',
                side=close_side_x,
                amount=qty_x_close,
                params={'reduceOnly': True},
            )
            order_x_id = str(order_x.get('id', 'unknown'))
            logger.info(f"[PartialExit] X filled: {sym_x} {order_x_id}")

    except Exception as exc:
        error = f"partial_exit order failed: {exc}"
        logger.error(f"[PartialExit] {error}")
        return PartialExitResult(
            executed=False, qty_y_closed=0.0, qty_x_closed=0.0,
            close_pct=close_pct, reason="order_failed", error=error,
        )

    # --- Actualizeaza checkpoint ---
    try:
        cp_state = checkpoint.load()
        if cp_state is not None:
            new_qty_y = max(0.0, qty_y - qty_y_close)
            new_qty_x = max(0.0, qty_x - qty_x_close)
            checkpoint.update_qty(qty_y=new_qty_y, qty_x=new_qty_x)
            logger.info(
                f"[PartialExit] Checkpoint: qty_y {qty_y:.6f}→{new_qty_y:.6f} "
                f"qty_x {qty_x:.6f}→{new_qty_x:.6f}"
            )
    except Exception as exc:
        logger.warning(f"[PartialExit] checkpoint update failed: {exc}")

    # --- Alert Telegram ---
    if alert_cfg:
        try:
            from execution.live_trader import _send_alert
            msg = (
                f"⚡ Partial Exit [{close_pct:.0%}]\n"
                f"Y: {sym_y} {close_side_y} {qty_y_close:.6f}\n"
                f"X: {sym_x} {close_side_x} {qty_x_close:.6f}\n"
                f"Z-score: {signal.zscore:.3f} | motiv: {signal.reason}"
            )
            await _send_alert(alert_cfg, msg)
        except Exception as exc:
            logger.warning(f"[PartialExit] alert failed: {exc}")

    return PartialExitResult(
        executed=True,
        qty_y_closed=qty_y_close,
        qty_x_closed=qty_x_close,
        close_pct=close_pct,
        reason=f"partial_exit_z0 z={signal.zscore:.3f}",
        order_y_id=order_y_id,
        order_x_id=order_x_id,
    )
