"""
execution/resume_manager.py  -  QuantLuna Resume Manager (Sprint 28)

Problema rezolvata:
  La restart dupa o intrerupere cu pozitie deschisa, botul trebuie sa:
  1. Detecteze ca exista o pozitie deschisa in checkpoint
  2. Reconcilieze cu pozitia reala de pe exchange (poate fi deja inchisa
     de un SL, lichidare sau manual)
  3. Decida:
     a) Pozitie reala == checkpoint -> preia pozitia, continua trading
     b) Pozitia reala lipsete (deja inchisa) -> curateste checkpoint, start fresh
     c) Pozitia reala diferit semnificativ -> HALT + alert operator

Sprint 28 additions
-------------------
restart_after_external_close(symbol, on_cycle_restart)
  - apelat cand MarketTradeHandler / AdoptionEngine detecteaza ca o pozitie
    adoptata s-a inchis extern (SL hit, manual, lichidare). Curata checkpoint
    si declanseaza on_cycle_restart(symbol) dupa cooldown.

Usage:
    manager = ResumeManager(checkpoint, ccxt_exchange, alert_cfg)
    result = await manager.reconcile_on_startup()
    if result.should_resume:
        live_trader.restore_position(result.position)
    elif result.should_halt:
        # asteapta decizia operatorului

    # Sprint 28 - apelat de MarketTradeHandler la close extern:
    await manager.restart_after_external_close(
        symbol="BTCUSDT",
        on_cycle_restart=my_restart_fn,
        cooldown_s=15.0,
    )
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Optional

from execution.checkpoint import PositionCheckpoint, PositionState

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    should_resume: bool
    should_halt: bool
    position: Optional[PositionState] = None
    message: str = ""


class ResumeManager:
    Reconciliaza starea checkpoint-ului cu pozitia reala de pe exchange
    la fiecare startup.

    Args:
        checkpoint:  PositionCheckpoint instance
        exchange:    ccxt async exchange instance (deja autentificat)
        alert_cfg:   AlertConfig pentru notificari (optional)
        qty_tolerance: toleranta relativa la compararea qty (default 5%)

    def __init__(
        self,
        checkpoint: PositionCheckpoint,
        exchange: Any,
        alert_cfg: Any = None,
        qty_tolerance: float = 0.05,
    ) -> None:
        self._cp = checkpoint
        self._exchange = exchange
        self._alert = alert_cfg
        self._tol = qty_tolerance

    async def reconcile_on_startup(self) -> ReconcileResult:
        Pasul principal de reconciliere la startup.
        saved = self._cp.load()

        if saved is None:
            logger.info("[Resume] Niciun checkpoint deschis — start fresh")
            return ReconcileResult(should_resume=False, should_halt=False,
                                   message="no open position in checkpoint")

        logger.warning(
            f"[Resume] Checkpoint deschis detectat: {saved.sym_y}/{saved.sym_x} "
            f"{saved.side_y} qty={saved.qty_y:.4f} opened_at={saved.opened_at}"
        )

        real_y = await self._fetch_position(saved.sym_y)
        real_x = await self._fetch_position(saved.sym_x)

        if real_y is None and real_x is None:
            msg = "[Resume] Nu pot verifica pozitia pe exchange (timeout/auth) — HALT conservator"
            logger.error(msg)
            await self._send_alert(msg)
            return ReconcileResult(should_resume=False, should_halt=True, message=msg)

        exch_qty_y = abs(real_y.get("contracts", 0) if real_y else 0)
        exch_qty_x = abs(real_x.get("contracts", 0) if real_x else 0)

        if exch_qty_y < 0.0001 and exch_qty_x < 0.0001:
            msg = (
                f"[Resume] Pozitia din checkpoint ({saved.sym_y}/{saved.sym_x}) "
                f"nu mai exista pe exchange — probabil inchisa extern (SL/liq/manual). "
                f"Checkpoint sters, start fresh."
            )
            logger.warning(msg)
            await self._send_alert(msg)
            self._cp.save_closed()
            return ReconcileResult(
                should_resume=False, should_halt=False,
                position=None, message=msg,
            )

        qty_y_ok = self._within_tolerance(exch_qty_y, saved.qty_y)
        qty_x_ok = self._within_tolerance(exch_qty_x, saved.qty_x)

        if qty_y_ok and qty_x_ok:
            msg = (
                f"[Resume] Pozitia reconciliata OK: {saved.sym_y} qty={exch_qty_y:.4f} / "
                f"{saved.sym_x} qty={exch_qty_x:.4f} — in toleranta {self._tol*100:.0f}%. "
                f"Preluare pozitie si continuare trading."
            )
            logger.info(msg)
            return ReconcileResult(
                should_resume=True, should_halt=False,
                position=saved, message=msg,
            )
        else:
            msg = (
                f"[Resume] DISCREPANTA pozitie! "
                f"Checkpoint: Y={saved.qty_y:.4f} X={saved.qty_x:.4f} | "
                f"Exchange: Y={exch_qty_y:.4f} X={exch_qty_x:.4f} | "
                f"Diferenta depaseste toleranta {self._tol*100:.0f}%. "
                f"HALT — verifica manual pozitiile pe exchange!"
            )
            logger.error(msg)
            await self._send_alert(msg)
            return ReconcileResult(should_resume=False, should_halt=True, message=msg)

    async def restart_after_external_close(
        self,
        symbol: str,
        on_cycle_restart: Callable[[str], Coroutine],
        cooldown_s: float = 10.0,
        alert_msg: Optional[str] = None,
    ) -> None:
        Apelat de MarketTradeHandler sau AdoptionEngine cand detecteaza ca
        o pozitie adoptata (sau monitorizata) s-a inchis extern.

        Pas:
          1. Curata checkpoint-ul pentru symbol
          2. Trimite alert optional
          3. Asteapta cooldown_s secunde (piata sa se stabilizeze)
          4. Apeleaza on_cycle_restart(symbol) pentru a porni un nou ciclu

        Parameters
        ----------
        symbol           : simbolul pentru care se restarteaza ciclul
        on_cycle_restart : corutina async(symbol: str) — callback de restart
        cooldown_s       : secunde de asteptare inainte de restart (default 10)
        alert_msg        : mesaj custom pentru alerta (optional)

        msg = alert_msg or (
            f"[Resume] Pozitie {symbol} inchisa extern — "
            f"checkpoint curatat, restart ciclu in {cooldown_s}s"
        )
        logger.info(msg)
        await self._send_alert(msg)

        try:
            self._cp.save_closed()
            logger.debug(f"[Resume] Checkpoint curatat pentru {symbol}")
        except Exception as exc:
            logger.warning(f"[Resume] Nu am putut curata checkpoint-ul: {exc}")

        if cooldown_s > 0:
            logger.info(f"[Resume] Cooldown {cooldown_s}s pentru {symbol}...")
            await asyncio.sleep(cooldown_s)

        try:
            logger.info(f"[Resume] Pornesc ciclu nou pentru {symbol}")
            await on_cycle_restart(symbol)
        except Exception as exc:
            logger.error(f"[Resume] restart_after_external_close: on_cycle_restart failed: {exc}")

    async def _fetch_position(self, symbol: str) -> Optional[dict]:
        Interogheaza pozitia curenta pentru un simbol de pe exchange.
        try:
            positions = await self._exchange.fetch_positions(symbol)
            for p in positions:
                sym = p.get("symbol", "")
                clean_symbol = symbol.replace("/USDT:USDT", "").upper()
                if clean_symbol in sym.upper():
                    return p
            return {"contracts": 0}
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