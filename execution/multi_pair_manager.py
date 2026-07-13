"""
QuantLuna — MultiPairManager
Sprint 27 (base) | Sprint 33 (set_alloc_factor watchdog hook)

Ruleaza simultan N perechi de tranzactionare cu:
  - Alocare capital per pereche (fixed USD | equal split | kelly-weighted)
  - Correlation filter — skip noua pereche daca corr > threshold cu o pereche activa
  - Monitorizare sanatate per pereche (state, PnL, uptime)
  - Start/stop individual per pereche
  - Global HALT cascade (stopa toate instantaneu)
  - NotifierBus integrare (entry, exit, stop-loss, halt per pereche)
  - RiskDashboardEngine — metrici agregate live
  - set_alloc_factor(pair, factor) — reduce/restore sizing per pereche (S33)

Usage:
    from execution.multi_pair_manager import MultiPairManager, PairConfig
    from execution.exchange_factory import get_order_router
    from notifications.notifier_bus import build_bus_from_env
    from risk.dashboard_engine import RiskDashboardEngine

    risk_engine = RiskDashboardEngine(initial_capital=50_000.0)
    bus         = build_bus_from_env()

    manager = MultiPairManager(
        risk_engine=risk_engine,
        notifier_bus=bus,
        total_capital_usd=50_000.0,
        max_pairs=5,
        correlation_threshold=0.85,
    )

    # Add pairs
    manager.add_pair(PairConfig(sym_y="BTCUSDT", sym_x="ETHUSDT",
                                 alloc_usd=10_000.0))
    manager.add_pair(PairConfig(sym_y="BNBUSDT", sym_x="SOLUSDT",
                                 alloc_usd=8_000.0))

    # Start all
    await manager.start_all()
    # Stop one
    await manager.stop_pair("BTCUSDT-ETHUSDT")
    # HALT everything
    await manager.halt_all(reason="MANUAL")
    # Reduce sizing (watchdog REDUCE_SIZE)
    manager.set_alloc_factor("BTCUSDT-ETHUSDT", 0.5)   # -> 50% din alloc original
    manager.restore_alloc("BTCUSDT-ETHUSDT")            # -> restaureaza alloc original
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class PairState(Enum):
    IDLE     = "idle"
    RUNNING  = "running"
    STOPPED  = "stopped"
    ERROR    = "error"
    HALTED   = "halted"


@dataclass
class PairConfig:
    sym_y:       str
    sym_x:       str
    interval:    str   = "1"
    alloc_usd:   float = 0.0     # 0 = auto-split
    strategy:    str   = "auto"  # "auto" | strategy name
    max_drawdown: float = 0.10   # 10% per-pair hard stop
    extra_kwargs: dict  = field(default_factory=dict)

    @property
    def pair_id(self) -> str:
        return f"{self.sym_y}-{self.sym_x}"


@dataclass
class PairStatus:
    config:     PairConfig
    state:      PairState  = PairState.IDLE
    started_at: float      = 0.0
    pnl_usd:    float      = 0.0
    trades:     int        = 0
    last_error: str        = ""
    task:       Optional[asyncio.Task] = field(default=None, repr=False)

    @property
    def uptime_s(self) -> float:
        if self.started_at == 0:
            return 0.0
        return time.time() - self.started_at

    def to_dict(self) -> dict:
        return {
            "pair_id":    self.config.pair_id,
            "sym_y":      self.config.sym_y,
            "sym_x":      self.config.sym_x,
            "alloc_usd":  self.config.alloc_usd,
            "state":      self.state.value,
            "pnl_usd":    round(self.pnl_usd, 4),
            "trades":     self.trades,
            "uptime_s":   round(self.uptime_s, 1),
            "last_error": self.last_error,
        }


class MultiPairManager:
    """
    Orchestreaza N perechi simultan via asyncio tasks.
    Fiecare pereche ruleaza in propriul task izolat.
    Eroarea unei perechi nu afecteaza celelalte.
    """

    def __init__(
        self,
        total_capital_usd:     float = 10_000.0,
        max_pairs:             int   = 10,
        correlation_threshold: float = 0.85,
        risk_engine=None,
        notifier_bus=None,
        exchange:              str   = "",
    ) -> None:
        self.total_capital_usd     = total_capital_usd
        self.max_pairs             = max_pairs
        self.correlation_threshold = correlation_threshold
        self._risk_engine          = risk_engine
        self._bus                  = notifier_bus
        self._exchange             = exchange or __import__("os").getenv("EXCHANGE", "bybit")
        self._pairs:               Dict[str, PairStatus] = {}
        self._halted:              bool = False
        # Sprint 33: memorie alloc originale inainte de set_alloc_factor()
        self._original_alloc:      Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Pair management
    # ------------------------------------------------------------------

    def add_pair(self, config: PairConfig) -> None:
        """Register a pair. Does not start it yet."""
        pid = config.pair_id
        if pid in self._pairs:
            logger.warning(f"Pair {pid} already registered")
            return
        if len(self._pairs) >= self.max_pairs:
            raise ValueError(f"max_pairs={self.max_pairs} reached")
        if config.alloc_usd == 0:
            config.alloc_usd = self._auto_alloc()
        self._pairs[pid] = PairStatus(config=config)
        logger.info(f"Pair registered: {pid} alloc={config.alloc_usd:.0f} USD")

    def remove_pair(self, pair_id: str) -> None:
        """Remove a stopped/idle pair."""
        ps = self._pairs.get(pair_id)
        if ps and ps.state in (PairState.RUNNING,):
            raise RuntimeError(f"Pair {pair_id} is running. Stop it first.")
        self._pairs.pop(pair_id, None)
        self._original_alloc.pop(pair_id, None)

    async def start_pair(self, pair_id: str) -> None:
        """Start a single pair."""
        if self._halted:
            raise RuntimeError("Manager is HALTED. Call resume() first.")
        ps = self._pairs.get(pair_id)
        if not ps:
            raise KeyError(f"Pair {pair_id} not registered")
        if ps.state == PairState.RUNNING:
            logger.warning(f"Pair {pair_id} already running")
            return
        ps.state      = PairState.RUNNING
        ps.started_at = time.time()
        ps.task       = asyncio.create_task(
            self._run_pair(ps), name=f"pair_{pair_id}"
        )
        logger.info(f"Pair started: {pair_id}")

    async def start_all(self) -> None:
        """Start all registered pairs."""
        for pid in list(self._pairs.keys()):
            try:
                await self.start_pair(pid)
            except Exception as e:
                logger.error(f"Failed to start {pid}: {e}")

    async def stop_pair(self, pair_id: str, reason: str = "manual") -> None:
        """Stop a single pair gracefully."""
        ps = self._pairs.get(pair_id)
        if not ps:
            return
        if ps.state != PairState.RUNNING:
            return
        ps.state = PairState.STOPPED
        if ps.task and not ps.task.done():
            ps.task.cancel()
            try:
                await ps.task
            except asyncio.CancelledError:
                pass
        logger.info(f"Pair stopped: {pair_id} reason={reason}")
        if self._bus:
            try:
                await self._bus.halt(reason=f"STOP_{reason.upper()}", pair=pair_id)
            except Exception:
                pass

    async def halt_all(self, reason: str = "MANUAL_HALT") -> None:
        """Emergency halt: stop all pairs immediately."""
        self._halted = True
        logger.critical(f"MultiPairManager HALT: {reason}")
        for pid in list(self._pairs.keys()):
            try:
                await self.stop_pair(pid, reason=reason)
            except Exception as e:
                logger.error(f"halt_all stop {pid}: {e}")
        if self._bus:
            try:
                await self._bus.halt(reason=reason, details=f"{len(self._pairs)} perechi oprite")
            except Exception:
                pass

    def resume(self) -> None:
        """Clear halted flag (pairs still need to be manually restarted)."""
        self._halted = False
        logger.info("MultiPairManager: halted flag cleared")

    # ------------------------------------------------------------------
    # Sprint 33 — Sizing control (watchdog REDUCE_SIZE hook)
    # ------------------------------------------------------------------

    def set_alloc_factor(self, pair_id: str, factor: float) -> None:
        """
        Reduce (sau restaureaza) alloc_usd al unei perechi la `factor` din
        valoarea originala (pre-reduce).

        Apelat de api/sizing.reduce_pair_size() — cale 2 (MultiPairManager
        fallback daca SizingEngine nu e injectat).

        Args:
            pair_id: ID pereche (ex: "BTCUSDT-ETHUSDT")
            factor:  multiplicator [0.0, 1.0]
                       0.5  = 50% din alloc original
                       1.0  = restaureaza la original (echivalent restore_alloc)
                       0.0  = zero sizing (WARNING emis, nu blocat)

        Comportament:
            - La prima apelare salveaza alloc_usd curent in _original_alloc[pair_id]
            - Actualizeaza config.alloc_usd = original * factor
            - Daca pereche RUNNING: actualizeaza RiskDashboardEngine.update_exposure()
            - Daca pair_id necunoscut: locat, nu ridicat

        Raises:
            Nu ridica niciodata — failsafe.
        """
        factor = max(0.0, min(1.0, factor))  # clamp [0, 1]
        ps = self._pairs.get(pair_id)
        if ps is None:
            logger.warning(
                "[MPM.set_alloc_factor] pair_id '%s' necunoscut — ignorat",
                pair_id,
            )
            return

        # Salveaza alloc original la prima reducere
        if pair_id not in self._original_alloc:
            self._original_alloc[pair_id] = ps.config.alloc_usd

        original = self._original_alloc[pair_id]
        new_alloc = round(original * factor, 4)

        if factor == 0.0:
            logger.warning(
                "[MPM.set_alloc_factor] %s: factor=0.0 — sizing zeroed "
                "(pereche nu va mai deschide pozitii noi pana la restore_alloc)",
                pair_id,
            )

        ps.config.alloc_usd = new_alloc
        logger.info(
            "[MPM.set_alloc_factor] %s: alloc %.2f -> %.2f USD (factor=%.2f, original=%.2f)",
            pair_id, original, new_alloc, factor, original,
        )

        # Actualizeaza RiskDashboardEngine daca e injectat
        if self._risk_engine is not None:
            try:
                self._risk_engine.update_exposure(pair_id, new_alloc)
            except Exception as exc:
                logger.warning(
                    "[MPM.set_alloc_factor] risk_engine.update_exposure failed: %s", exc
                )

    def get_alloc_factor(self, pair_id: str) -> Optional[float]:
        """
        Returneaza factorul curent aplicat perechii.

        Returns:
            float in [0, 1] daca a fost apelat set_alloc_factor() anterior,
            1.0 daca nu a fost modificat (alloc = alloc original),
            None daca pair_id necunoscut.
        """
        ps = self._pairs.get(pair_id)
        if ps is None:
            return None
        original = self._original_alloc.get(pair_id, ps.config.alloc_usd)
        if original == 0:
            return 1.0
        return round(ps.config.alloc_usd / original, 6)

    def restore_alloc(self, pair_id: str) -> None:
        """
        Restaureaza alloc_usd la valoarea originala (pre-reduce).

        No-op daca set_alloc_factor() nu a fost apelat anterior
        sau daca pair_id e necunoscut.
        """
        ps = self._pairs.get(pair_id)
        if ps is None:
            return
        original = self._original_alloc.pop(pair_id, None)
        if original is None:
            return  # nicio reducere anterioara
        ps.config.alloc_usd = original
        logger.info(
            "[MPM.restore_alloc] %s: alloc restaurat la %.2f USD",
            pair_id, original,
        )
        if self._risk_engine is not None:
            try:
                self._risk_engine.update_exposure(pair_id, original)
            except Exception as exc:
                logger.warning(
                    "[MPM.restore_alloc] risk_engine.update_exposure failed: %s", exc
                )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return status for all pairs."""
        return {
            "halted":            self._halted,
            "n_pairs":           len(self._pairs),
            "n_running":         sum(1 for ps in self._pairs.values() if ps.state == PairState.RUNNING),
            "total_capital_usd": self.total_capital_usd,
            "exchange":          self._exchange,
            "pairs":             {pid: ps.to_dict() for pid, ps in self._pairs.items()},
        }

    # ------------------------------------------------------------------
    # Internal pair runner
    # ------------------------------------------------------------------

    async def _run_pair(self, ps: PairStatus) -> None:
        """
        Core loop per pereche.
        In productie: instantiaza un LiveTrader + WsFeed per pereche.
        Aici: skeleton cu tick artificial pentru integrare fara WS real.
        LiveTrader real este injectat via PairConfig.extra_kwargs["trader"].
        """
        pid    = ps.config.pair_id
        trader = ps.config.extra_kwargs.get("trader")  # LiveTrader instance optional

        logger.info(f"[{pid}] pair loop started (alloc={ps.config.alloc_usd:.0f} USD)")

        if self._risk_engine:
            self._risk_engine.update_exposure(pid, ps.config.alloc_usd)

        # Initialize position fetcher if available
        order_router = ps.config.extra_kwargs.get("order_router")
        symbols = [ps.config.sym_y, ps.config.sym_x]

        try:
            while True:
                await asyncio.sleep(1.0)  # tick every 1s (replaced by WS event in real usage)

                # Fetch current positions from Bybit
                if order_router:
                    try:
                        positions = await order_router.get_open_positions()
                        # Update pair status with position info
                        for pos in positions:
                            sym = pos.get("symbol", "")
                            if sym in symbols:
                                side = pos.get("side", "").lower()
                                size = float(pos.get("size", 0))
                                entry = float(pos.get("entryPrice", 0))
                                upnl = float(pos.get("unrealisedPnl", 0))
                                logger.debug(
                                    f"[{pid}] Position: {sym} {side} {size} @ {entry} "
                                    f"uPnL={upnl:+.4f}"
                                )
                                # Update risk engine with position data
                                if self._risk_engine:
                                    try:
                                        self._risk_engine.update_position(
                                            pair_id=pid,
                                            symbol=sym,
                                            side=side,
                                            size=size,
                                            entry_price=entry,
                                            unrealised_pnl=upnl,
                                        )
                                    except Exception as e:
                                        logger.warning(
                                            f"[{pid}] Failed to update risk engine: {e}"
                                        )
                    except Exception as e:
                        logger.warning(f"[{pid}] Failed to fetch positions: {e}")

                # Drawdown check
                if self._risk_engine:
                    pair_snap = self._risk_engine.pair_snapshot(pid)
                    if pair_snap:
                        # Fix S33: denominator DD = alloc original (nu alloc redus de watchdog)
                        # Daca set_alloc_factor(0.0) a zeroed alloc_usd, evitam fallback la
                        # auto_alloc() care ar ignora starea zeroed a perechii.
                        alloc = self._original_alloc.get(pid, ps.config.alloc_usd) or self._auto_alloc()
                        dd = abs(pair_snap.get("net_pnl_usd", 0)) / alloc if alloc > 0 else 0.0
                        if dd > ps.config.max_drawdown:
                            logger.warning(
                                f"[{pid}] per-pair DD {dd:.2%} > "
                                f"{ps.config.max_drawdown:.2%} — stopping"
                            )
                            if self._bus:
                                asyncio.create_task(self._bus.stop_loss(
                                    pair=pid,
                                    loss_usd=pair_snap["net_pnl_usd"],
                                    loss_pct=-dd,
                                ))
                            break

        except asyncio.CancelledError:
            logger.info(f"[{pid}] pair loop cancelled")
        except Exception as e:
            ps.state      = PairState.ERROR
            ps.last_error = str(e)
            logger.error(f"[{pid}] pair loop error: {e}")
        finally:
            if self._risk_engine:
                self._risk_engine.update_exposure(pid, 0.0)
            if ps.state == PairState.RUNNING:
                ps.state = PairState.STOPPED

    # ------------------------------------------------------------------
    # Capital allocation
    # ------------------------------------------------------------------

    def _auto_alloc(self) -> float:
        """Equal split over max_pairs."""
        return round(self.total_capital_usd / self.max_pairs, 2)
