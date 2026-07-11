"""
execution/margin_risk_guard.py  -  QuantLuna Margin Risk Guard v1.0

Sprint S35 (2026-07-12):
  Monitorizeaza margin ratio pentru toate pozitiile si actioneaza
  automat inainte de lichidare.

  Logica de protectie:
    - margin_ratio >= 1.5  : SAFE    - nicio actiune
    - 1.1 <= ratio < 1.5   : DANGER  - alerta Telegram
    - ratio < 1.1           : CRITICAL - inchide pozitia automat

  Configurabil:
    - poll_interval_s: cat de des verifica (default 30s)
    - danger_threshold: sub ce ratio trimite alerta (default 1.5)
    - critical_threshold: sub ce ratio inchide pozitia (default 1.1)
    - auto_close_on_critical: daca inchide automat (default True)

  Protectie suplimentara:
    - Cooldown 60s intre actiuni pe acelasi simbol
    - Max 3 inchideri automate per sesiune (dupa care opreste si alerteaza)
    - Nu inchide niciodata mai mult de o pozitie simultan

Usage::

    guard = MarginRiskGuard(
        order_router=margin_router,
        notifier_bus=bus,
    )
    await guard.watch_loop()  # blocheaza, monitorizeaza continuu
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

from loguru import logger


@dataclass
class MarginRiskConfig:
    poll_interval_s: float = 30.0
    danger_threshold: float = 1.5      # alerta Telegram
    critical_threshold: float = 1.1    # inchide automat
    auto_close_on_critical: bool = True
    max_auto_closes_per_session: int = 3
    cooldown_per_symbol_s: float = 60.0
    category: str = "linear"


class MarginRiskGuard:
    """
    Monitorizeaza margin ratio si protejeaza impotriva lichidarii.
    """

    def __init__(
        self,
        order_router,  # MarginOrderRouter
        notifier_bus=None,
        cfg: Optional[MarginRiskConfig] = None,
    ) -> None:
        self._router = order_router
        self._bus = notifier_bus
        self._cfg = cfg or MarginRiskConfig()
        self._running = False
        self._auto_closes_count = 0
        self._last_action_ts: Dict[str, float] = {}
        self._alerted_symbols: Set[str] = set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def watch_loop(self) -> None:
        """Loop principal: verifica margin ratio la fiecare poll_interval_s."""
        self._running = True
        logger.info(
            "[MarginRiskGuard] Watch loop pornit | poll={}s danger={} critical={}",
            self._cfg.poll_interval_s,
            self._cfg.danger_threshold,
            self._cfg.critical_threshold,
        )
        while self._running:
            try:
                await self._check_all_positions()
            except asyncio.CancelledError:
                logger.info("[MarginRiskGuard] Cancelled")
                return
            except Exception as exc:
                logger.error("[MarginRiskGuard] watch_loop error: {}", exc)
            await asyncio.sleep(self._cfg.poll_interval_s)

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Check logic
    # ------------------------------------------------------------------

    async def _check_all_positions(self) -> None:
        positions = await self._router.fetch_margin_positions(
            category=self._cfg.category
        )
        if not positions:
            return

        for pos in positions:
            await self._evaluate_position(pos)

    async def _evaluate_position(self, pos) -> None:
        symbol = pos.symbol
        ratio = pos.margin_ratio

        # CRITICAL: inchide automat
        if ratio < self._cfg.critical_threshold:
            if self._auto_closes_count >= self._cfg.max_auto_closes_per_session:
                logger.error(
                    "[MarginRiskGuard] CRITIC {} ratio={:.3f} dar max_auto_closes "
                    "({}) atins - ALERTA MANUALA NECESARA",
                    symbol, ratio, self._cfg.max_auto_closes_per_session,
                )
                await self._alert(
                    f"\u26a0\ufe0f\u26a0\ufe0f *MARGIN CRITIC* `{symbol}`\n"
                    f"  Ratio: `{ratio:.3f}` (sub {self._cfg.critical_threshold})\n"
                    f"  **MAX AUTO-CLOSES ATINS** \u2014 ACTIUNE MANUALA NECESARA!\n"
                    f"  Liq price: `{pos.liq_price}`",
                    level="error",
                )
                return

            # Cooldown check
            elapsed = time.monotonic() - self._last_action_ts.get(symbol, 0)
            if elapsed < self._cfg.cooldown_per_symbol_s:
                return

            logger.error(
                "[MarginRiskGuard] CRITIC {} ratio={:.3f} < {} "
                "-> INCHID POZITIA AUTOMAT",
                symbol, ratio, self._cfg.critical_threshold,
            )
            await self._alert(
                f"\u26d4 *MARGIN CRITIC* `{symbol}`\n"
                f"  Ratio: `{ratio:.3f}` (pericol lichidare!)\n"
                f"  Liq price: `{pos.liq_price}`\n"
                f"  **Inchid pozitia automat** ({self._auto_closes_count+1}/"
                f"{self._cfg.max_auto_closes_per_session})",
                level="error",
            )

            if self._cfg.auto_close_on_critical:
                success = await self._router.close_position(
                    symbol, category=self._cfg.category
                )
                if success:
                    self._auto_closes_count += 1
                    self._last_action_ts[symbol] = time.monotonic()
                    self._alerted_symbols.discard(symbol)
                    await self._alert(
                        f"\u2705 Pozitie `{symbol}` INCHISA (margin protection)\n"
                        f"  PnL: `{pos.unrealised_pnl:+.2f} USDT`"
                    )

        # DANGER: alerta Telegram (o singura data per simbol pana redevine SAFE)
        elif ratio < self._cfg.danger_threshold:
            if symbol not in self._alerted_symbols:
                logger.warning(
                    "[MarginRiskGuard] DANGER {} ratio={:.3f} < {}",
                    symbol, ratio, self._cfg.danger_threshold,
                )
                await self._alert(
                    f"\u26a0\ufe0f *Margin DANGER* `{symbol}`\n"
                    f"  Ratio: `{ratio:.3f}` (sub {self._cfg.danger_threshold})\n"
                    f"  Liq price: `{pos.liq_price}`\n"
                    f"  Leverage: `{pos.leverage}x` | "
                    f"PnL: `{pos.unrealised_pnl:+.2f} USDT`\n"
                    f"  Monitorizeaza si reduce leverage-ul daca e posibil."
                )
                self._alerted_symbols.add(symbol)

        # SAFE: curata alert
        else:
            if symbol in self._alerted_symbols:
                logger.info(
                    "[MarginRiskGuard] {} revenit SAFE (ratio={:.3f})",
                    symbol, ratio,
                )
                self._alerted_symbols.discard(symbol)

    async def _alert(self, msg: str, level: str = "info") -> None:
        if not self._bus:
            logger.info("[MarginRiskGuard] (no bus) {}", msg)
            return
        try:
            await self._bus.send_alert(msg, level=level)
        except Exception as exc:
            logger.warning("[MarginRiskGuard] alert failed: {}", exc)
