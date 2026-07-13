"""
execution/single_hedge_manager.py — QuantLuna Single Hedge Manager v1.0
Sprint S29 v3.9 — 2026-07-12

Gestioneaza complet o pozitie solo detectata la boot (ex: EGLDUSDT hedge mode).

Functionalitati:
  - Adopta pozitia din SoloHedgeGroup (nu deschide nimic nou)
  - Trailing SL: muta SL-ul in sus pe masura ce pretul creste (pentru LONG)
    sau in jos (pentru SHORT). Pasul implicit: 0.5%.
  - Hard SL/TP nativ Bybit: setat o data dupa adoptie daca lipseste
  - PnL loop: calculeaza uPnL live si trimite update Telegram la fiecare
    pnl_report_interval_s secunde
  - Exit complet: inchide pozitia la target sau la SL/TP trigger
  - Suporta hedge mode Bybit (positionIdx=1 LONG / positionIdx=2 SHORT)

Usage din orchestrator::

    mgr = SingleHedgeManager(
        group=solo_hedge_group,   # SoloHedgeGroup detectat la boot
        order_router=router,
        notifier_bus=bus,
        cfg=SingleHedgeConfig(),
    )
    task = asyncio.create_task(mgr.manage())
    # task ruleaza pana cand pozitia e inchisa sau runner-ul e oprit
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

from execution.strategy_classifier import SoloHedgeGroup


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class SingleHedgeConfig:
    """Configurare pentru un SingleHedgeManager."""

    # Trailing SL
    trailing_sl_enabled: bool = True
    trailing_sl_pct: float = 0.015      # 1.5% trailing
    trailing_step_pct: float = 0.005    # muta SL doar la fiecare 0.5% avans

    # Hard SL/TP initial (aplicat daca pozitia nu are SL/TP la adoptie)
    initial_sl_pct: float = 0.03        # 3% SL fix de la entry
    initial_tp_pct: float = 0.06        # 6% TP fix de la entry
    apply_initial_sl_tp: bool = True

    # PnL reporting
    pnl_report_interval_s: float = 300.0   # 5 minute
    price_poll_interval_s: float = 5.0     # poll pret la fiecare 5s

    # Bybit
    category: str = "linear"


# ---------------------------------------------------------------------------
# Stare interna per leg
# ---------------------------------------------------------------------------

@dataclass
class LegState:
    """Stare live pentru un singur leg (LONG sau SHORT)."""
    side: str                  # 'long' | 'short'
    qty: float
    entry_price: float
    current_price: float = 0.0
    sl_price: float = 0.0
    tp_price: float = 0.0
    trailing_high: float = 0.0  # cel mai bun pret atins de la intrare (LONG)
    trailing_low: float = float("inf")  # cel mai mic pret atins (SHORT)
    active: bool = True

    @property
    def unrealised_pnl(self) -> float:
        if self.current_price <= 0:
            return 0.0
        if self.side == "long":
            return (self.current_price - self.entry_price) * self.qty
        return (self.entry_price - self.current_price) * self.qty

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        if self.side == "long":
            return (self.current_price - self.entry_price) / self.entry_price
        return (self.entry_price - self.current_price) / self.entry_price


# ---------------------------------------------------------------------------
# SingleHedgeManager
# ---------------------------------------------------------------------------

class SingleHedgeManager:
    """
    Gestioneaza o pozitie solo (detectata la boot) pana la inchidere.

    Pornit ca asyncio.Task de catre WorkflowOrchestrator.
    Se opreste singur cand pozitia e inchisa sau cand stop() e apelat.
    """

    def __init__(
        self,
        group: SoloHedgeGroup,
        order_router: Any,
        notifier_bus: Optional[Any] = None,
        cfg: Optional[SingleHedgeConfig] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        self._group = group
        self._router = order_router
        self._bus = notifier_bus
        self._cfg = cfg or SingleHedgeConfig()
        self._stop = stop_event or asyncio.Event()

        self._symbol = group.symbol
        self._long_state: Optional[LegState] = None
        self._short_state: Optional[LegState] = None
        self._last_pnl_report: float = 0.0
        self._started_at: float = time.time()

        # Initializeaza starea din group
        if group.long_leg:
            self._long_state = LegState(
                side="long",
                qty=group.long_leg.qty,
                entry_price=group.long_leg.entry_price,
                current_price=group.long_leg.mark_price or group.long_leg.entry_price,
                trailing_high=group.long_leg.mark_price or group.long_leg.entry_price,
            )
        if group.short_leg:
            self._short_state = LegState(
                side="short",
                qty=group.short_leg.qty,
                entry_price=group.short_leg.entry_price,
                current_price=group.short_leg.mark_price or group.short_leg.entry_price,
                trailing_low=group.short_leg.mark_price or group.short_leg.entry_price,
            )

        # Fetch current positions from Bybit to verify state
        self._verify_positions_task = None

    async def _verify_positions(self) -> None:
        """Fetch current positions from Bybit and verify they match our state."""
        try:
            positions = await self._router.get_open_positions(symbol=self._symbol)
            if not positions:
                logger.info(
                    "SingleHedgeManager [%s]: No positions found on Bybit for symbol",
                    self._symbol,
                )
                return

            for pos in positions:
                side = pos.get("side", "").lower()
                size = float(pos.get("size", 0))
                entry = float(pos.get("entryPrice", 0))
                upnl = float(pos.get("unrealisedPnl", 0))

                if side == "long" and self._long_state:
                    self._long_state.current_price = entry
                    self._long_state.trailing_high = max(
                        self._long_state.trailing_high, entry
                    )
                    logger.info(
                        "SingleHedgeManager [%s]: Verified LONG position: "
                        "size=%s entry=%.4f uPnL=%+.4f",
                        self._symbol, size, entry, upnl,
                    )
                elif side == "short" and self._short_state:
                    self._short_state.current_price = entry
                    self._short_state.trailing_low = min(
                        self._short_state.trailing_low, entry
                    )
                    logger.info(
                        "SingleHedgeManager [%s]: Verified SHORT position: "
                        "size=%s entry=%.4f uPnL=%+.4f",
                        self._symbol, size, entry, upnl,
                    )
                else:
                    # Position exists on Bybit but not in our state
                    logger.warning(
                        "SingleHedgeManager [%s]: Found %s position on Bybit "
                        "but no matching state exists",
                        self._symbol, side,
                    )

                # Register position on StateBus
                try:
                    from core.state_bus import bus
                    bus.add_bybit_position(
                        symbol=self._symbol,
                        side=side,
                        size=size,
                        entry_price=entry,
                        unrealised_pnl=upnl,
                        pair_id=self._symbol,
                    )
                except Exception as exc:
                    logger.debug(
                        "SingleHedgeManager [%s]: Failed to register position on bus: %s",
                        self._symbol, exc,
                    )
        except Exception as exc:
            logger.warning(
                "SingleHedgeManager [%s]: Position verification failed: %s",
                self._symbol, exc,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def manage(self) -> None:
        """
        Coroutine principala. Ruleaza pana cand toate legurile sunt inchise
        sau pana cand stop() e apelat.

        Secventa:
          1. Verifica pozitiile pe Bybit
          2. Notificare Telegram: adoptie + stare initiala
          3. Aplica SL/TP initial daca e configurat
          4. Loop principal: poll pret -> trailing SL -> check SL/TP -> PnL report
        """
        # Verify positions on Bybit first
        await self._verify_positions()

        logger.info(
            "SingleHedgeManager [%s]: pornit | %s",
            self._symbol, self._group,
        )
        await self._notify_adoption()

        # Aplica SL/TP initial daca e configurat si pozitia nu il are deja
        if self._cfg.apply_initial_sl_tp:
            await self._apply_initial_sl_tp()

        # Loop principal
        while not self._stop.is_set() and self._has_active_legs():
            try:
                price = await self._fetch_mark_price()
                if price and price > 0:
                    await self._on_price_tick(price)
                await self._maybe_report_pnl()
            except asyncio.CancelledError:
                logger.info("SingleHedgeManager [%s]: task cancelled", self._symbol)
                return
            except Exception as exc:
                logger.warning(
                    "SingleHedgeManager [%s]: loop error: %s", self._symbol, exc
                )
            await asyncio.sleep(self._cfg.price_poll_interval_s)

        logger.info(
            "SingleHedgeManager [%s]: toate legurile inchise — task complet",
            self._symbol,
        )
        await self._notify_closed()

    def stop(self) -> None:
        """Opreste loop-ul de management la urmatoarea iteratie."""
        self._stop.set()

    # ------------------------------------------------------------------
    # Price tick logic
    # ------------------------------------------------------------------

    async def _on_price_tick(self, price: float) -> None:
        """Proceseaza un nou pret: update state, trailing SL, check SL/TP."""
        if self._long_state and self._long_state.active:
            self._long_state.current_price = price
            if self._cfg.trailing_sl_enabled:
                await self._update_trailing_sl_long(price)
            await self._check_sl_tp_long(price)

        if self._short_state and self._short_state.active:
            self._short_state.current_price = price
            if self._cfg.trailing_sl_enabled:
                await self._update_trailing_sl_short(price)
            await self._check_sl_tp_short(price)

    async def _update_trailing_sl_long(self, price: float) -> None:
        """Muta SL-ul in sus pe masura ce pretul creste (pentru leg LONG)."""
        st = self._long_state
        if st is None or not st.active:
            return

        new_trailing_high = max(st.trailing_high, price)
        step = st.entry_price * self._cfg.trailing_step_pct

        if new_trailing_high >= st.trailing_high + step:
            new_sl = new_trailing_high * (1.0 - self._cfg.trailing_sl_pct)
            if new_sl > st.sl_price:
                old_sl = st.sl_price
                st.sl_price = new_sl
                st.trailing_high = new_trailing_high
                logger.debug(
                    "SingleHedgeManager [%s] LONG trailing SL: %.4f → %.4f "
                    "(price=%.4f high=%.4f)",
                    self._symbol, old_sl, new_sl, price, new_trailing_high,
                )
                # Actualizeaza SL-ul nativ pe Bybit
                await self._set_sl_native(
                    side="long", sl_price=new_sl, qty=st.qty
                )
        else:
            st.trailing_high = new_trailing_high

    async def _update_trailing_sl_short(self, price: float) -> None:
        """Muta SL-ul in jos pe masura ce pretul scade (pentru leg SHORT)."""
        st = self._short_state
        if st is None or not st.active:
            return

        new_trailing_low = min(st.trailing_low, price)
        step = st.entry_price * self._cfg.trailing_step_pct

        if new_trailing_low <= st.trailing_low - step:
            new_sl = new_trailing_low * (1.0 + self._cfg.trailing_sl_pct)
            if st.sl_price <= 0 or new_sl < st.sl_price:
                old_sl = st.sl_price
                st.sl_price = new_sl
                st.trailing_low = new_trailing_low
                logger.debug(
                    "SingleHedgeManager [%s] SHORT trailing SL: %.4f → %.4f "
                    "(price=%.4f low=%.4f)",
                    self._symbol, old_sl, new_sl, price, new_trailing_low,
                )
                await self._set_sl_native(
                    side="short", sl_price=new_sl, qty=st.qty
                )
        else:
            st.trailing_low = new_trailing_low

    async def _check_sl_tp_long(self, price: float) -> None:
        """Verifica daca SL sau TP a fost atins pentru leg LONG."""
        st = self._long_state
        if st is None or not st.active:
            return
        if st.sl_price > 0 and price <= st.sl_price:
            logger.warning(
                "SingleHedgeManager [%s] LONG SL HIT: price=%.4f sl=%.4f",
                self._symbol, price, st.sl_price,
            )
            await self._close_leg("long", st, reason="SL")
        elif st.tp_price > 0 and price >= st.tp_price:
            logger.info(
                "SingleHedgeManager [%s] LONG TP HIT: price=%.4f tp=%.4f",
                self._symbol, price, st.tp_price,
            )
            await self._close_leg("long", st, reason="TP")

    async def _check_sl_tp_short(self, price: float) -> None:
        """Verifica daca SL sau TP a fost atins pentru leg SHORT."""
        st = self._short_state
        if st is None or not st.active:
            return
        if st.sl_price > 0 and price >= st.sl_price:
            logger.warning(
                "SingleHedgeManager [%s] SHORT SL HIT: price=%.4f sl=%.4f",
                self._symbol, price, st.sl_price,
            )
            await self._close_leg("short", st, reason="SL")
        elif st.tp_price > 0 and price <= st.tp_price:
            logger.info(
                "SingleHedgeManager [%s] SHORT TP HIT: price=%.4f tp=%.4f",
                self._symbol, price, st.tp_price,
            )
            await self._close_leg("short", st, reason="TP")

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    async def _close_leg(self, side: str, state: LegState, reason: str) -> None:
        """Inchide un leg (market order reduce_only)."""
        if not state.active:
            return
        state.active = False

        pnl = state.unrealised_pnl
        logger.info(
            "SingleHedgeManager [%s] CLOSE %s: reason=%s qty=%.6f "
            "entry=%.4f price=%.4f pnl=%+.4f USDT",
            self._symbol, side.upper(), reason,
            state.qty, state.entry_price, state.current_price, pnl,
        )

        close_side = "sell" if side == "long" else "buy"
        position_idx = 1 if side == "long" else 2  # Bybit hedge mode

        try:
            from execution.bybit_order_router import OrderRequest, OrderSide, OrderType
            req = OrderRequest(
                symbol=self._symbol,
                side=OrderSide.SELL if close_side == "sell" else OrderSide.BUY,
                order_type=OrderType.MARKET,
                qty=state.qty,
                price=0.0,
                reduce_only=True,
            )
            # Seteaza positionIdx pentru hedge mode
            if hasattr(req, "position_idx"):
                req.position_idx = position_idx

            await self._router.create_order(req)
            logger.info(
                "SingleHedgeManager [%s] CLOSE %s: ordin trimis OK",
                self._symbol, side.upper(),
            )
        except Exception as exc:
            logger.error(
                "SingleHedgeManager [%s] CLOSE %s FAILED: %s",
                self._symbol, side.upper(), exc,
            )
            state.active = True  # re-activam ca sa incercam din nou
            if self._bus:
                try:
                    await self._bus.send_alert(
                        f"\u2620\ufe0f CLOSE FAILED [{self._symbol}] {side.upper()} {reason}: {exc}",
                        level="critical",
                    )
                except Exception:
                    pass
            return

        if self._bus:
            icon = "\u2705" if pnl >= 0 else "\U0001f534"
            try:
                await self._bus.send_alert(
                    f"{icon} [{self._symbol}] {side.upper()} INCHIS ({reason}) "
                    f"qty={state.qty:.4f} entry={state.entry_price:.4f} "
                    f"exit={state.current_price:.4f} PnL={pnl:+.4f} USDT",
                    level="info" if pnl >= 0 else "error",
                )
            except Exception:
                pass

    async def _apply_initial_sl_tp(self) -> None:
        """Aplica SL/TP initial pe pozitiile adoptate daca nu au deja."""
        if self._long_state and self._long_state.active and self._long_state.sl_price == 0:
            sl = self._long_state.entry_price * (1.0 - self._cfg.initial_sl_pct)
            tp = self._long_state.entry_price * (1.0 + self._cfg.initial_tp_pct)
            self._long_state.sl_price = sl
            self._long_state.tp_price = tp
            logger.info(
                "SingleHedgeManager [%s] LONG initial SL=%.4f TP=%.4f",
                self._symbol, sl, tp,
            )
            await self._set_sl_tp_native("long", sl, tp, self._long_state.qty)

        if self._short_state and self._short_state.active and self._short_state.sl_price == 0:
            sl = self._short_state.entry_price * (1.0 + self._cfg.initial_sl_pct)
            tp = self._short_state.entry_price * (1.0 - self._cfg.initial_tp_pct)
            self._short_state.sl_price = sl
            self._short_state.tp_price = tp
            logger.info(
                "SingleHedgeManager [%s] SHORT initial SL=%.4f TP=%.4f",
                self._symbol, sl, tp,
            )
            await self._set_sl_tp_native("short", sl, tp, self._short_state.qty)

    async def _set_sl_native(
        self, side: str, sl_price: float, qty: float
    ) -> None:
        """Actualizeaza SL nativ Bybit pentru un leg."""
        try:
            from execution.native_sl_tp import place_sl_tp
            position_idx = 1 if side == "long" else 2
            await place_sl_tp(
                self._router, self._symbol, side,
                qty, sl_price, 0.0, self._cfg.category,
                position_idx=position_idx,
            )
        except Exception as exc:
            logger.debug(
                "SingleHedgeManager [%s] set_sl_native failed: %s", self._symbol, exc
            )

    async def _set_sl_tp_native(
        self, side: str, sl_price: float, tp_price: float, qty: float
    ) -> None:
        """Seteaza atat SL cat si TP nativ Bybit."""
        try:
            from execution.native_sl_tp import place_sl_tp
            position_idx = 1 if side == "long" else 2
            await place_sl_tp(
                self._router, self._symbol, side,
                qty, sl_price, tp_price, self._cfg.category,
                position_idx=position_idx,
            )
        except Exception as exc:
            logger.debug(
                "SingleHedgeManager [%s] set_sl_tp_native failed: %s", self._symbol, exc
            )

    # ------------------------------------------------------------------
    # Price fetch
    # ------------------------------------------------------------------

    async def _fetch_mark_price(self) -> Optional[float]:
        """Citeste mark price curent pt symbol via order_router REST."""
        try:
            # Try get_mark_price first
            if hasattr(self._router, "get_mark_price"):
                raw = await self._router.get_mark_price(
                    symbol=self._symbol, category=self._cfg.category
                )
                price = float(
                    raw.get("result", {}).get("list", [{}])[0].get("markPrice", 0) or 0
                )
                if price > 0:
                    return price

            # Fallback: get_tickers
            if hasattr(self._router, "get_tickers"):
                raw = await self._router.get_tickers(
                    category=self._cfg.category, symbol=self._symbol
                )
                price = float(
                    raw.get("result", {}).get("list", [{}])[0].get("markPrice", 0) or 0
                )
                if price > 0:
                    return price

            # Fallback: get_open_positions (for paper/dry mode)
            if hasattr(self._router, "get_open_positions"):
                positions = await self._router.get_open_positions(symbol=self._symbol)
                if positions:
                    # Use entry price as fallback
                    price = float(positions[0].get("entryPrice", 0))
                    if price > 0:
                        return price
        except Exception as exc:
            logger.debug(
                "SingleHedgeManager [%s] fetch_mark_price failed: %s",
                self._symbol, exc,
            )
        return None

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    async def _notify_adoption(self) -> None:
        """Trimite mesaj Telegram la adoptia pozitiei."""
        if not self._bus:
            return
        lines = [f"\U0001f9e9 *SingleHedge adoptat: `{self._symbol}`*"]
        if self._long_state:
            lines.append(
                f"  \U0001f7e2 LONG qty=`{self._long_state.qty:.6f}` "
                f"@ `{self._long_state.entry_price:.4f}`"
            )
        if self._short_state:
            lines.append(
                f"  \U0001f534 SHORT qty=`{self._short_state.qty:.6f}` "
                f"@ `{self._short_state.entry_price:.4f}`"
            )
        mode = "HEDGE" if self._group.is_hedge else self._group.dominant_side.upper()
        lines.append(f"  Mode: `{mode}` | Trailing SL: `{self._cfg.trailing_sl_pct*100:.1f}%`")
        try:
            await self._bus.send_alert("\n".join(lines), level="info")
        except Exception:
            pass

    async def _maybe_report_pnl(self) -> None:
        """Trimite raport PnL periodic pe Telegram."""
        now = time.time()
        if now - self._last_pnl_report < self._cfg.pnl_report_interval_s:
            return
        self._last_pnl_report = now

        long_pnl  = self._long_state.unrealised_pnl  if self._long_state  else 0.0
        short_pnl = self._short_state.unrealised_pnl if self._short_state else 0.0
        total_pnl = long_pnl + short_pnl

        icon = "\u2705" if total_pnl >= 0 else "\U0001f7e1"
        msg_parts = [
            f"{icon} *[{self._symbol}] PnL update*",
            f"  Total uPnL: `{total_pnl:+.4f} USDT`",
        ]
        if self._long_state and self._long_state.active:
            msg_parts.append(
                f"  LONG: `{self._long_state.unrealised_pnl:+.4f}` "
                f"({self._long_state.pnl_pct*100:+.2f}%) "
                f"SL=`{self._long_state.sl_price:.4f}`"
            )
        if self._short_state and self._short_state.active:
            msg_parts.append(
                f"  SHORT: `{self._short_state.unrealised_pnl:+.4f}` "
                f"({self._short_state.pnl_pct*100:+.2f}%) "
                f"SL=`{self._short_state.sl_price:.4f}`"
            )
        if self._bus:
            try:
                await self._bus.send_alert("\n".join(msg_parts), level="info")
            except Exception:
                pass

    async def _notify_closed(self) -> None:
        """Trimite mesaj final Telegram cand toate legurile sunt inchise."""
        if not self._bus:
            return
        total_pnl = (
            (self._long_state.unrealised_pnl  if self._long_state  else 0.0) +
            (self._short_state.unrealised_pnl if self._short_state else 0.0)
        )
        icon = "\u2705" if total_pnl >= 0 else "\U0001f534"
        try:
            await self._bus.send_alert(
                f"{icon} [{self._symbol}] SingleHedge COMPLET "
                f"PnL total: `{total_pnl:+.4f} USDT`",
                level="info" if total_pnl >= 0 else "error",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _has_active_legs(self) -> bool:
        long_active  = self._long_state  is not None and self._long_state.active
        short_active = self._short_state is not None and self._short_state.active
        return long_active or short_active
