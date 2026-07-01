"""
execution/resume_manager.py  —  QuantLuna Resume Manager

Problema rezolvată:
  La restart după o întrerupere cu poziție deschisă, botul trebuie să:
  1. Detecteze că există o poziție deschisă în checkpoint
  2. Reconcilieze cu poziția reală de pe exchange (poate fi deja închisă
     de un SL, lichidare sau manual)
  3. Decidă:
     a) Poziție reală == checkpoint → preia poziția, continuă trading
     b) Poziția reală lipsete (deja închisă) → curatețte checkpoint, start fresh
     c) Poziția reală diferă semnificativ → HALT + alert operator

Usage:
    manager = ResumeManager(checkpoint, ccxt_exchange, alert_cfg)
    result = await manager.reconcile_on_startup()
    if result.should_resume:
        live_trader.restore_position(result.position)
    elif result.should_halt:
        # aşteaptă decizia operatorului
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from execution.checkpoint import PositionCheckpoint, PositionState

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    should_resume: bool       # True = preia poziția din checkpoint
    should_halt: bool         # True = neștimt ce s-a întâmplat, halt
    position: Optional[PositionState] = None
    message: str = ""


class ResumeManager:
    """
    Reconciliază starea checkpoint-ului cu poziția reală de pe exchange
    la fiecare startup.

    Args:
        checkpoint:  PositionCheckpoint instance
        exchange:    ccxt async exchange instance (deja autentificat)
        alert_cfg:   AlertConfig pentru notificări (optional)
        qty_tolerance: toleranță relativă la compararea qty (default 5%)
    """

    def __init__(
        self,
        checkpoint: PositionCheckpoint,
        exchange,
        alert_cfg=None,
        qty_tolerance: float = 0.05,
    ) -> None:
        self._cp          = checkpoint
        self._exchange    = exchange
        self._alert       = alert_cfg
        self._tol         = qty_tolerance

    async def reconcile_on_startup(self) -> ReconcileResult:
        """
        Pasul principal de reconciliere la startup.
        """
        saved = self._cp.load()

        if saved is None:
            logger.info("[Resume] Niciun checkpoint deschis — start fresh")
            return ReconcileResult(should_resume=False, should_halt=False,
                                   message="no open position in checkpoint")

        logger.warning(
            f"[Resume] Checkpoint deschis detectat: {saved.sym_y}/{saved.sym_x} "
            f"{saved.side_y} qty={saved.qty_y:.4f} opened_at={saved.opened_at}"
        )

        # încearcă să interogăm poziția reală de pe exchange
        real_y = await self._fetch_position(saved.sym_y)
        real_x = await self._fetch_position(saved.sym_x)

        if real_y is None and real_x is None:
            # exchange inaccessibil — halt conservator
            msg = "[Resume] Nu pot verifica poziția pe exchange (timeout/auth) — HALT conservator"
            logger.error(msg)
            await self._send_alert(msg)
            return ReconcileResult(should_resume=False, should_halt=True, message=msg)

        # Poziții de pe exchange
        exch_qty_y = abs(real_y.get("contracts", 0) if real_y else 0)
        exch_qty_x = abs(real_x.get("contracts", 0) if real_x else 0)

        # Dacă ambele qty sunt ~0 → poziția a fost închisă extern (SL, lichidare, manual)
        if exch_qty_y < 0.0001 and exch_qty_x < 0.0001:
            msg = (
                f"[Resume] Poziția din checkpoint ({saved.sym_y}/{saved.sym_x}) "
                f"nu mai există pe exchange — probabil închisă extern (SL/liq/manual). "
                f"Checkpoint şters, start fresh."
            )
            logger.warning(msg)
            await self._send_alert(msg)
            self._cp.save_closed()
            return ReconcileResult(
                should_resume=False, should_halt=False,
                position=None, message=msg,
            )

        # Verifică dacă qty-urile sunt în toleranță
        qty_y_ok = self._within_tolerance(exch_qty_y, saved.qty_y)
        qty_x_ok = self._within_tolerance(exch_qty_x, saved.qty_x)

        if qty_y_ok and qty_x_ok:
            msg = (
                f"[Resume] Poziția reconciliată OK: {saved.sym_y} qty={exch_qty_y:.4f} / "
                f"{saved.sym_x} qty={exch_qty_x:.4f} — în toleranță {self._tol*100:.0f}%. "
                f"Preluare poziție şi continuare trading."
            )
            logger.info(msg)
            return ReconcileResult(
                should_resume=True, should_halt=False,
                position=saved, message=msg,
            )
        else:
            # Diferență semnificativă — halt + alert operator
            msg = (
                f"[Resume] DISCREPAN\u0162\u0102 poziție! "
                f"Checkpoint: Y={saved.qty_y:.4f} X={saved.qty_x:.4f} | "
                f"Exchange: Y={exch_qty_y:.4f} X={exch_qty_x:.4f} | "
                f"Diferența depăşeşte toleranța {self._tol*100:.0f}%. "
                f"HALT — verifică manual pozițiile pe exchange!"
            )
            logger.error(msg)
            await self._send_alert(msg)
            return ReconcileResult(should_resume=False, should_halt=True, message=msg)

    async def _fetch_position(self, symbol: str) -> Optional[dict]:
        """Interoghează poziția curentă pentru un simbol de pe exchange."""
        try:
            positions = await self._exchange.fetch_positions([symbol])
            for p in positions:
                sym = p.get("symbol", "")
                if symbol.replace("/USDT:USDT", "").upper() in sym.upper():
                    return p
            return {"contracts": 0}  # simbol găsit dar fără poziție
        except Exception as exc:
            logger.warning(f"[Resume] fetch_position({symbol}) failed: {exc}")
            return None

    def _within_tolerance(self, actual: float, expected: float) -> bool:
        if expected == 0:
            return actual < 0.0001
        return abs(actual - expected) / expected <= self._tol

    async def _send_alert(self, message: str) -> None:
        if not self._alert:
            return
        try:
            from execution.live_trader import _send_alert
            await _send_alert(self._alert, f"[Resume] {message}")
        except Exception as exc:
            logger.warning(f"[Resume] alert send failed: {exc}")
