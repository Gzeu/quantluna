"""
execution/live_trader.py  —  QuantLuna Live Trading Engine

Sprint history integrat:
  Sprint 4:  Bybit/Binance WS, asyncio queue, TraderState machine, OrderManager
  Sprint 5:  StateBus integration, _publish_state()
  Sprint 6:  FundingMonitor + PnLReconciler tasks, LiveSignalAdapter wrapping,
             NormalizedSignal typed access, monitor_api_key support, cleanup tasks
  Sprint 7:  WsWatchdog integrat — ping() per tick, run() task, gate la entry
  Sprint 10: PortfolioAllocator integration — sizing via KellyCrossPair,
             close_all() pentru HARD_STOP, DD-aware entry gate,
             allocator.update_state() per tick, allocator.record_exit() la exit

P0 fixes (mainnet blockers):
  FIX-1: Symbol parser robust — strip USDT/USDT-PERP/PERP corect (nu [:3])
  FIX-2: Rolling spread buffer real (500 ticks) → Kelly primește volatilitate reală
  FIX-3: Queue full → drop cu log WARN + contor; la 100 drops consecutive → HALT
  FIX-4: close_all() retry logic (3 încercări, 1s delay) + alertă externă la eșec
  FIX-5: FundingMonitor pornit cu exec_config credentials când monitor_api_key lipsă

P1 fixes (înainte de capital semnificativ):
  FIX-6:  Kalman reset explicit la WS reconnect via signal_gen.reset_kalman()
  FIX-7:  Telegram / webhook alert async pe HARD_STOP, EXIT fail, HALT
  FIX-8:  Persistent trade history — SQLite append la fiecare trade (fișier local)

Flux de decizie la entry:
  1. WsWatchdog.is_live  → blocat dacă feed stale (Sprint 7)
  2. PortfolioAllocator.request_entry()  → 5 gates: DD, max pairs, corr, Kelly, exposure
  3. notional vine din Kelly cross-pair (nu hardcodat)
  4. Per tick: allocator.update_state() + watchdog.ping() + spread_buffer.append()
  5. La HARD_STOP: close_all() cu retry + alert extern
  6. La exit: allocator.record_exit() + trade persistat în SQLite
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Deque, Dict, List, Optional

import aiohttp
import pandas as pd

from .order_manager import ExecutionConfig, FillPair, OrderManager
from .funding_monitor import FundingMonitor, FundingConfig, create_funding_monitor
from .pnl_reconciler import PnLReconciler, ReconcilerConfig
from .ws_watchdog import WsWatchdog, WatchdogConfig
from strategy.signal_adapter import LiveSignalAdapter
from risk import PortfolioAllocator, AllocatorConfig
from risk.drawdown_controller import DDLevel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FIX-1: Symbol parser robust
# ---------------------------------------------------------------------------
# Sufixele care trebuie eliminate din simbolul raw al exchange-ului
# pentru a obține baza (ex: "ETHUSDT" → "ETH", "BTCUSDT-PERP" → "BTC")
_SYMBOL_SUFFIXES = (
    "USDT-PERP", "USDTPERP", "-PERP", "PERP",
    "USDT", "USD", "BUSD",
)


def _extract_base(symbol_raw: str) -> str:
    """
    Extrage baza dintr-un simbol raw de exchange.

    Exemple:
      "ETHUSDT"        → "ETH"
      "BTCUSDT"        → "BTC"
      "DOGEUSDT"       → "DOGE"
      "1000PEPEUSDT"   → "1000PEPE"
      "SOLUSDT-PERP"   → "SOL"
      "XRPBUSD"        → "XRP"
    """
    s = symbol_raw.upper()
    for suffix in _SYMBOL_SUFFIXES:
        if s.endswith(suffix):
            base = s[: len(s) - len(suffix)]
            if base:  # nu lăsa string gol
                return base
    return s  # fallback: returnează as-is


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class TraderState(Enum):
    IDLE        = "idle"
    WARMING_UP  = "warming_up"
    ACTIVE      = "active"
    IN_POSITION = "in_position"
    CLOSING     = "closing"
    HALTED      = "halted"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PriceTick:
    symbol: str
    price: float
    ts: pd.Timestamp
    volume_24h: float = 0.0
    bid: float = 0.0
    ask: float = 0.0


@dataclass
class AlertConfig:
    """
    FIX-7: Configurare alertă externă (Telegram sau webhook generic).
    Dacă ambele sunt goale, alertele sunt doar log CRITICAL.
    """
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    webhook_url: str = ""           # orice URL POST cu {"text": "..."}
    timeout_s: float = 5.0


@dataclass
class LiveConfig:
    sym_y: str
    sym_x: str
    exchange: str = "bybit"           # "bybit" | "binance"
    capital_usdt: float = 10_000.0
    max_leverage: float = 2.0          # fallback; sizing vine din Kelly
    min_warmup_bars: int = 30
    log_interval_s: int = 60
    heartbeat_interval_s: int = 30
    max_daily_drawdown: float = 0.03
    exec_config: ExecutionConfig = field(default_factory=ExecutionConfig)
    state_bus_enabled: bool = True

    # Sprint 6 — Funding Monitor
    funding_poll_interval_s: float = 60.0
    funding_periods_per_year: float = 3.0 * 365.0
    funding_alert_threshold: float = 0.05

    # Sprint 6 — P&L Reconciler
    pnl_reconcile_interval_s: float = 30.0
    pnl_drift_alert_usd: float = 5.0

    # Sprint 6 — Credențiale monitoring (opțional — FIX-5: fallback la exec_config)
    monitor_api_key: str = ""
    monitor_api_secret: str = ""
    testnet: bool = False

    # Sprint 7 — WsWatchdog
    watchdog: WatchdogConfig = field(default_factory=WatchdogConfig)
    watchdog_gate_entries: bool = True  # blochează ENTRY (nu EXIT) când feed e STALE

    # Sprint 10 — Allocator config
    allocator_config: Optional[AllocatorConfig] = None

    # FIX-3: Queue backpressure
    queue_maxsize: int = 1_000
    queue_drop_halt_threshold: int = 100   # drops consecutive înainte de HALT

    # FIX-4: close_all retry
    close_all_max_retries: int = 3
    close_all_retry_delay_s: float = 1.0

    # FIX-7: Alerte externe
    alert: AlertConfig = field(default_factory=AlertConfig)

    # FIX-8: Persistent trade history
    trade_db_path: str = "trades.db"      # SQLite; "" dezactivează persistența
    spread_buffer_size: int = 500         # FIX-2: rolling spread buffer


# ---------------------------------------------------------------------------
# FIX-7: Alert helper (async, fire-and-forget)
# ---------------------------------------------------------------------------

async def _send_alert(cfg: AlertConfig, message: str) -> None:
    """
    Trimite alertă pe Telegram și/sau webhook.
    Silențios la eroare — nu blochează niciodată trading loop-ul.
    """
    logger.critical(f"[ALERT] {message}")
    if not cfg.telegram_bot_token and not cfg.webhook_url:
        return
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=cfg.timeout_s)
        ) as session:
            if cfg.telegram_bot_token and cfg.telegram_chat_id:
                url = (
                    f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
                )
                await session.post(
                    url,
                    json={"chat_id": cfg.telegram_chat_id, "text": f"🚨 QuantLuna\n{message}"},
                )
            if cfg.webhook_url:
                await session.post(
                    cfg.webhook_url,
                    json={"text": f"QuantLuna ALERT: {message}"},
                )
    except Exception as exc:
        logger.warning(f"Alert delivery failed (non-critical): {exc}")


# ---------------------------------------------------------------------------
# FIX-8: Persistent trade history (SQLite)
# ---------------------------------------------------------------------------

class TradeHistory:
    """
    Persistă tranzacțiile în SQLite local.
    Thread-safe pentru writes din asyncio (rulăm în executor).
    """

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        if db_path:
            self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          REAL    NOT NULL,
                    pair_y      TEXT    NOT NULL,
                    pair_x      TEXT    NOT NULL,
                    side_y      TEXT,
                    entry_y     REAL,
                    entry_x     REAL,
                    exit_y      REAL,
                    exit_x      REAL,
                    qty_y       REAL,
                    qty_x       REAL,
                    pnl_usdt    REAL,
                    fees_usdt   REAL,
                    pnl_frac    REAL,
                    zscore_entry REAL,
                    hedge_ratio  REAL
                )
            """)
            conn.commit()

    def append(self, record: dict) -> None:
        """Fire-and-forget insert; silențios la eroare."""
        if not self._path:
            return
        try:
            with sqlite3.connect(self._path) as conn:
                conn.execute("""
                    INSERT INTO trades
                        (ts, pair_y, pair_x, side_y, entry_y, entry_x,
                         exit_y, exit_x, qty_y, qty_x, pnl_usdt, fees_usdt,
                         pnl_frac, zscore_entry, hedge_ratio)
                    VALUES
                        (:ts, :pair_y, :pair_x, :side_y, :entry_y, :entry_x,
                         :exit_y, :exit_x, :qty_y, :qty_x, :pnl_usdt, :fees_usdt,
                         :pnl_frac, :zscore_entry, :hedge_ratio)
                """, record)
                conn.commit()
        except Exception as exc:
            logger.warning(f"TradeHistory write failed: {exc}")

    def load_pnl_fractions(self, limit: int = 200) -> List[float]:
        """Încarcă ultimele `limit` trade-uri la restart pentru Kelly warmup."""
        if not self._path:
            return []
        try:
            with sqlite3.connect(self._path) as conn:
                rows = conn.execute(
                    "SELECT pnl_frac FROM trades ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [r[0] for r in reversed(rows) if r[0] is not None]
        except Exception as exc:
            logger.warning(f"TradeHistory load failed: {exc}")
            return []


# ---------------------------------------------------------------------------
# LiveTrader
# ---------------------------------------------------------------------------

class LiveTrader:
    """
    Async live trading engine pentru un pair activ.

    Parametri constructor:
      config     — LiveConfig
      signal_gen — SignalGenerator sau LiveSignalAdapter
      allocator  — PortfolioAllocator (Sprint 10); None = creat intern
      state_bus  — StateBus optional pentru dashboard
    """

    def __init__(
        self,
        config: LiveConfig,
        signal_gen,
        allocator: Optional[PortfolioAllocator] = None,
        state_bus=None,
        portfolio_risk=None,   # backwards compat, ignorat
    ):
        self.cfg = config
        self._bus = state_bus

        # Sprint 6: wrap automat SignalGenerator → LiveSignalAdapter
        if isinstance(signal_gen, LiveSignalAdapter):
            self.signal_gen = signal_gen
        else:
            self.signal_gen = LiveSignalAdapter(signal_gen)

        # Sprint 10: PortfolioAllocator
        if allocator is not None:
            self.allocator = allocator
        else:
            alloc_cfg = config.allocator_config or AllocatorConfig(
                capital_usd=config.capital_usdt,
            )
            self.allocator = PortfolioAllocator(alloc_cfg)

        # Sprint 7: WsWatchdog
        self.watchdog = WsWatchdog(config.watchdog, state_bus)

        # State machine
        self._state: TraderState = TraderState.IDLE
        self._queue: asyncio.Queue[PriceTick] = asyncio.Queue(
            maxsize=config.queue_maxsize
        )

        # FIX-3: queue drop counter
        self._queue_drops: int = 0

        # Prices
        self._price_y: float = 0.0
        self._price_x: float = 0.0
        self._last_tick_y: Optional[PriceTick] = None
        self._last_tick_x: Optional[PriceTick] = None
        self._warming_bars: int = 0

        # FIX-2: Rolling spread buffer pentru Kelly
        self._spread_buffer: Deque[float] = deque(
            maxlen=config.spread_buffer_size
        )

        # Position state
        self._entry_side_y: Optional[str] = None
        self._entry_side_x: Optional[str] = None
        self._entry_qty_y: float = 0.0
        self._entry_qty_x: float = 0.0
        self._entry_fill: Optional[FillPair] = None
        self._entry_notional: float = 0.0
        self._entry_zscore: float = 0.0
        self._entry_hedge_ratio: float = 0.0

        # PnL tracking
        self._last_log_ts: pd.Timestamp = pd.Timestamp.min
        self._log_interval = pd.Timedelta(seconds=config.log_interval_s)
        self._daily_pnl: float = 0.0
        self._realized_pnl: float = 0.0
        self._total_fees: float = 0.0
        self._trade_count: int = 0
        self._daily_reset_date: Optional[pd.Timestamp] = None
        self._open_pnl: float = 0.0

        # Sprint 6 monitoring tasks
        self._funding_task: Optional[asyncio.Task] = None
        self._reconciler_task: Optional[asyncio.Task] = None
        self._funding_monitor_exchange = None

        # Sprint 7 watchdog task
        self._watchdog_task: Optional[asyncio.Task] = None

        # OrderManager (setat în run())
        self.orders: Optional[OrderManager] = None

        # FIX-8: Trade history persistent + Kelly warmup
        self._trade_history = TradeHistory(config.trade_db_path)
        self._trade_pnl_history: List[float] = self._trade_history.load_pnl_fractions(200)
        if self._trade_pnl_history:
            logger.info(
                f"TradeHistory loaded: {len(self._trade_pnl_history)} trades din '{config.trade_db_path}' "
                f"— Kelly warmup pre-setat"
            )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(self):
        logger.info(
            f"LiveTrader starting | pair={self.cfg.sym_y}/{self.cfg.sym_x} "
            f"exchange={self.cfg.exchange} | capital={self.cfg.capital_usdt:.0f} USDT"
        )
        self._state = TraderState.WARMING_UP
        if self._bus:
            self._bus.update({
                "sym_y": self.cfg.sym_y,
                "sym_x": self.cfg.sym_x,
                "exchange": self.cfg.exchange,
                "trader_state": TraderState.WARMING_UP.value,
            })

        await self._start_monitoring_tasks()

        async with OrderManager(self.cfg.exec_config) as orders:
            self.orders = orders
            try:
                await asyncio.gather(
                    self._ws_feed(),
                    self._consumer(),
                    self._heartbeat(),
                    self._run_watchdog(),
                )
            except asyncio.CancelledError:
                logger.info("LiveTrader cancelled — shutting down")
            except Exception as exc:
                logger.exception(f"LiveTrader fatal: {exc}")
                self._state = TraderState.HALTED
                if self._bus:
                    self._bus.update({"trader_state": TraderState.HALTED.value})
                await _send_alert(
                    self.cfg.alert,
                    f"LiveTrader FATAL exception: {exc} | pair={self._pair_id}",
                )
            finally:
                await self._cancel_monitoring_tasks()
                self.orders = None

    # ------------------------------------------------------------------
    # Sprint 7: WsWatchdog task wrapper
    # ------------------------------------------------------------------

    async def _run_watchdog(self):
        try:
            await self.watchdog.run()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(f"WsWatchdog error: {exc} — continuing without watchdog")

    # ------------------------------------------------------------------
    # FIX-5: Monitoring tasks — fallback la exec_config dacă monitor_api_key lipsă
    # ------------------------------------------------------------------

    async def _start_monitoring_tasks(self):
        # FIX-5: dacă monitor_api_key lipsă, folosim credențialele din exec_config
        api_key = self.cfg.monitor_api_key or getattr(
            self.cfg.exec_config, "api_key", ""
        )
        api_secret = self.cfg.monitor_api_secret or getattr(
            self.cfg.exec_config, "api_secret", ""
        )
        if not api_key:
            logger.warning(
                "FundingMonitor + PnLReconciler DISABLED: niciun API key disponibil "
                "(setați monitor_api_key sau exec_config.api_key pentru monitoring complet)"
            )
            return
        try:
            funding_cfg = FundingConfig(
                sym_y=self.cfg.sym_y,
                sym_x=self.cfg.sym_x,
                poll_interval_s=self.cfg.funding_poll_interval_s,
                funding_periods_per_year=self.cfg.funding_periods_per_year,
                exchange_id=self.cfg.exchange,
                testnet=self.cfg.testnet,
            )
            monitor, self._funding_monitor_exchange = await create_funding_monitor(
                funding_cfg,
                api_key,
                api_secret,
                self._bus,
            )
            self._funding_task = asyncio.create_task(
                monitor.run(), name="funding_monitor"
            )
            reconciler_cfg = ReconcilerConfig(
                sym_y=self.cfg.sym_y,
                sym_x=self.cfg.sym_x,
                poll_interval_s=self.cfg.pnl_reconcile_interval_s,
                drift_alert_usd=self.cfg.pnl_drift_alert_usd,
                exchange_id=self.cfg.exchange,
                testnet=self.cfg.testnet,
            )
            reconciler = PnLReconciler(
                reconciler_cfg,
                self._funding_monitor_exchange,
                self._bus,
            )
            self._reconciler_task = asyncio.create_task(
                reconciler.run(), name="pnl_reconciler"
            )
            logger.info(
                "FundingMonitor + PnLReconciler tasks started "
                f"(key={'monitor_api_key' if self.cfg.monitor_api_key else 'exec_config.api_key'})"
            )
        except Exception as exc:
            logger.warning(f"Monitoring tasks failed to start: {exc} — continuing without")

    async def _cancel_monitoring_tasks(self):
        for task in (self._funding_task, self._reconciler_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._funding_monitor_exchange:
            try:
                await self._funding_monitor_exchange.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # FIX-4: close_all() cu retry și alertă externă
    # ------------------------------------------------------------------

    async def close_all(self, reason: str = "HARD_STOP") -> None:
        """
        Închide forțat toate pozițiile active cu retry logic.
        La eșec după toate retry-urile: alertă externă + HALTED.
        POZIȚIA POATE RĂMÂNE DESCHISĂ dacă exchange-ul nu răspunde —
        alertă critică trimisă în acel caz.
        """
        if self._state != TraderState.IN_POSITION:
            logger.info(f"close_all({reason}): no open position, nothing to close")
            return

        logger.warning(f"close_all() triggered | reason={reason}")
        if self._bus:
            self._bus.update({
                "trader_state": TraderState.CLOSING.value,
                "close_all_reason": reason,
            })

        class _ForceExitSignal:
            exit = True
            reason = reason
            zscore = 0.0
            hedge_ratio = 1.0

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.cfg.close_all_max_retries + 1):
            try:
                await self._close_position(
                    _ForceExitSignal(), pd.Timestamp.now(tz="UTC")
                )
                logger.info(f"close_all() success on attempt {attempt}")
                # _close_position sets state to ACTIVE in finally; override to HALTED
                self._state = TraderState.HALTED
                if self._bus:
                    self._bus.update({"trader_state": TraderState.HALTED.value})
                return
            except Exception as exc:
                last_exc = exc
                logger.error(
                    f"close_all() attempt {attempt}/{self.cfg.close_all_max_retries} "
                    f"failed: {exc}"
                )
                if attempt < self.cfg.close_all_max_retries:
                    await asyncio.sleep(self.cfg.close_all_retry_delay_s)

        # FIX-4: toate retry-urile epuizate — alertă critică
        alert_msg = (
            f"‼️ CRITICAL: close_all({reason}) EȘUAT după "
            f"{self.cfg.close_all_max_retries} încercări! "
            f"VERIFICAȚI MANUAL POZIȚIILE PE EXCHANGE! "
            f"pair={self._pair_id} | last_error={last_exc}"
        )
        await _send_alert(self.cfg.alert, alert_msg)
        self._state = TraderState.HALTED
        if self._bus:
            self._bus.update({
                "trader_state": TraderState.HALTED.value,
                "close_all_failed": True,
            })

    # ------------------------------------------------------------------
    # WebSocket feeds
    # ------------------------------------------------------------------

    async def _ws_feed(self):
        if self.cfg.exchange == "binance":
            await self._ws_feed_binance()
        else:
            await self._ws_feed_bybit()

    async def _ws_feed_bybit(self):
        import websockets
        sym_y = self.cfg.sym_y.replace("/", "").replace(":USDT", "")
        sym_x = self.cfg.sym_x.replace("/", "").replace(":USDT", "")
        url = "wss://stream.bybit.com/v5/public/linear"
        sub_msg = json.dumps({
            "op": "subscribe",
            "args": [f"tickers.{sym_y}", f"tickers.{sym_x}"],
        })
        async for ws in websockets.connect(url, ping_interval=20, ping_timeout=10):
            await self._on_reconnect()
            try:
                await ws.send(sub_msg)
                async for raw in ws:
                    tick = self._parse_bybit_tick(raw)
                    if tick:
                        await self._enqueue_tick(tick)
            except Exception as exc:
                logger.warning(f"Bybit WS error: {exc} — reconnecting in 2s")
                await asyncio.sleep(2)

    async def _ws_feed_binance(self):
        import websockets
        def fmt(sym: str) -> str:
            return sym.replace("/", "").replace(":USDT", "").lower()
        sy, sx = fmt(self.cfg.sym_y), fmt(self.cfg.sym_x)
        url = f"wss://fstream.binance.com/stream?streams={sy}@ticker/{sx}@ticker"
        async for ws in websockets.connect(url, ping_interval=20, ping_timeout=10):
            await self._on_reconnect()
            try:
                async for raw in ws:
                    tick = self._parse_binance_tick(raw)
                    if tick:
                        await self._enqueue_tick(tick)
            except Exception as exc:
                logger.warning(f"Binance WS error: {exc} — reconnecting in 2s")
                await asyncio.sleep(2)

    # ------------------------------------------------------------------
    # FIX-3: Enqueue cu backpressure și HALT la drops consecutive
    # ------------------------------------------------------------------

    async def _enqueue_tick(self, tick: PriceTick) -> None:
        """Pune tick în coadă. La full: drop cu log + contor; la threshold → HALT."""
        if self._queue.full():
            self._queue_drops += 1
            logger.warning(
                f"Queue FULL — tick dropped "
                f"({self._queue_drops} consecutive drops) | "
                f"pair={self._pair_id} | qsize={self._queue.qsize()}"
            )
            if self._queue_drops >= self.cfg.queue_drop_halt_threshold:
                logger.critical(
                    f"Queue drops exceeded threshold "
                    f"({self._queue_drops} >= {self.cfg.queue_drop_halt_threshold}) — HALTING"
                )
                await _send_alert(
                    self.cfg.alert,
                    f"Queue overflow HALT | {self._queue_drops} drops consecutive | "
                    f"pair={self._pair_id}",
                )
                self._state = TraderState.HALTED
                if self._bus:
                    self._bus.update({"trader_state": TraderState.HALTED.value})
        else:
            self._queue_drops = 0  # reset contor la succes
            await self._queue.put(tick)

    # ------------------------------------------------------------------
    # FIX-1: Parsers cu symbol parser robust
    # ------------------------------------------------------------------

    def _parse_bybit_tick(self, raw: str) -> Optional[PriceTick]:
        try:
            msg = json.loads(raw)
            data = msg.get("data", {})
            symbol_raw = data.get("symbol", "")
            last = data.get("lastPrice")
            if not last or not symbol_raw:
                return None
            base = _extract_base(symbol_raw)          # FIX-1
            symbol = f"{base}/USDT:USDT"
            return PriceTick(
                symbol=symbol, price=float(last),
                ts=pd.Timestamp.now(tz="UTC"),
                bid=float(data.get("bid1Price") or last),
                ask=float(data.get("ask1Price") or last),
                volume_24h=float(data.get("volume24h") or 0),
            )
        except Exception:
            return None

    def _parse_binance_tick(self, raw: str) -> Optional[PriceTick]:
        try:
            msg = json.loads(raw)
            data = msg.get("data", {})
            if data.get("e") != "24hrTicker":
                return None
            symbol_raw = data.get("s", "")
            last = data.get("c")
            if not last or not symbol_raw:
                return None
            base = _extract_base(symbol_raw)          # FIX-1
            symbol = f"{base}/USDT:USDT"
            return PriceTick(
                symbol=symbol, price=float(last),
                ts=pd.Timestamp.now(tz="UTC"),
                bid=float(data.get("b") or last),
                ask=float(data.get("a") or last),
                volume_24h=float(data.get("v") or 0),
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Consumer loop
    # ------------------------------------------------------------------

    async def _consumer(self):
        while self._state != TraderState.HALTED:
            tick = await self._queue.get()
            self.watchdog.ping()      # Sprint 7: ping per tick
            self._update_prices(tick)
            if self._price_y > 0 and self._price_x > 0:
                await self._on_tick(tick.ts)
            self._queue.task_done()

    def _update_prices(self, tick: PriceTick):
        sym_base = tick.symbol.split("/")[0]
        y_base = self.cfg.sym_y.split("/")[0]
        x_base = self.cfg.sym_x.split("/")[0]
        if sym_base == y_base:
            self._price_y = tick.price
            self._last_tick_y = tick
        elif sym_base == x_base:
            self._price_x = tick.price
            self._last_tick_x = tick

    # ------------------------------------------------------------------
    # Core tick handler
    # ------------------------------------------------------------------

    async def _on_tick(self, ts: pd.Timestamp):
        self._reset_daily_pnl_if_needed(ts)

        if self._state == TraderState.WARMING_UP:
            self._warming_bars += 1
            if self._warming_bars >= self.cfg.min_warmup_bars:
                self._state = TraderState.ACTIVE
                logger.info(f"Warm-up complete ({self._warming_bars} bars) — ACTIVE")
                if self._bus:
                    self._bus.update({"trader_state": TraderState.ACTIVE.value})
            return

        if self._state not in (TraderState.ACTIVE, TraderState.IN_POSITION):
            return

        sig = self.signal_gen.on_tick(self._price_y, self._price_x, ts)
        if sig is None:
            return

        # FIX-2: actualizare spread buffer la fiecare tick
        spread_val = (
            sig.spread
            if hasattr(sig, "spread")
            else (self._price_y - sig.hedge_ratio * self._price_x)
        )
        self._spread_buffer.append(spread_val)

        # Sprint 10: actualizează allocator per tick
        self._open_pnl = (
            self._compute_open_pnl()
            if self._state == TraderState.IN_POSITION
            else 0.0
        )
        snap = self.allocator.update_state(
            open_pnl_per_pair={self._pair_id: self._open_pnl},
            spread_updates={self._pair_id: spread_val},
        )

        # HARD_STOP detection
        if snap.level == DDLevel.HARD_STOP:
            logger.critical(f"HARD_STOP detectat | notes={snap.notes}")
            await _send_alert(
                self.cfg.alert,
                f"HARD_STOP | DD limit atins | pair={self._pair_id} | notes={snap.notes}",
            )
            await self.close_all(reason="HARD_STOP")
            return

        # Pair-level DD force close
        if (
            self._pair_id in snap.pairs_force_close
            and self._state == TraderState.IN_POSITION
        ):
            logger.warning(f"PAIR DD exceeded — force close {self._pair_id}")
            await _send_alert(
                self.cfg.alert,
                f"PAIR_DD exceeded | pair={self._pair_id} | closing position",
            )
            await self.close_all(reason="PAIR_DD")
            return

        self._publish_state(sig, ts)

        if (ts - self._last_log_ts) >= self._log_interval:
            logger.info(
                f"[{ts}] state={self._state.value} | "
                f"z={sig.zscore:.3f} | beta={sig.hedge_ratio:.4f} | "
                f"Y={self._price_y:.4f} X={self._price_x:.4f} | "
                f"open_pnl={self._open_pnl:.2f} | daily={self._daily_pnl:.2f} | "
                f"spread_buf={len(self._spread_buffer)} | "
                f"dd={snap.level.value} | ws={self.watchdog.state}"
            )
            self._last_log_ts = ts

        # Exit
        if self._state == TraderState.IN_POSITION and getattr(sig, "exit", False):
            await self._close_position(sig, ts)
            return

        # Entry
        if self._state == TraderState.ACTIVE and getattr(sig, "entry", False):
            await self._open_position(sig, ts)

    @property
    def _pair_id(self) -> str:
        return f"{self.cfg.sym_y}/{self.cfg.sym_x}"

    # ------------------------------------------------------------------
    # StateBus publish
    # ------------------------------------------------------------------

    def _publish_state(self, sig, ts: pd.Timestamp):
        if not self._bus or not self.cfg.state_bus_enabled:
            return
        self._bus.update({
            "trader_state": self._state.value,
            "price_y": self._price_y,
            "price_x": self._price_x,
            "timestamp_utc": ts.isoformat(),
            "zscore": sig.zscore,
            "spread": sig.spread,
            "hedge_ratio": sig.hedge_ratio,
            "kalman_gain": sig.kalman_gain,
            "kalman_uncertainty": sig.kalman_uncertainty,
            "half_life": sig.half_life,
            "regime": sig.regime,
            "realized_pnl": self._realized_pnl,
            "open_pnl": self._open_pnl,
            "daily_pnl": self._daily_pnl,
            "total_fees_usdt": self._total_fees,
            "trade_count": self._trade_count,
            "in_position": self._state == TraderState.IN_POSITION,
            "entry_side_y": self._entry_side_y or "",
            "entry_side_x": self._entry_side_x or "",
            "entry_price_y": self._entry_fill.leg_y.fill_price if self._entry_fill else 0.0,
            "entry_price_x": self._entry_fill.leg_x.fill_price if self._entry_fill else 0.0,
            "qty_y": self._entry_qty_y,
            "qty_x": self._entry_qty_x,
            "dd_level": self.allocator.dd_level.value,
            "n_active_pairs": self.allocator._n_pairs,
            "ws_watchdog_state": self.watchdog.state,
            "spread_buffer_len": len(self._spread_buffer),  # FIX-2: vizibil în dashboard
            "queue_drops": self._queue_drops,               # FIX-3: vizibil în dashboard
        })

    def _compute_open_pnl(self) -> float:
        if not self._entry_fill:
            return 0.0
        return (
            (self._price_y - self._entry_fill.leg_y.fill_price)
            * self._entry_qty_y
            * (1 if self._entry_side_y == "buy" else -1)
        ) + (
            (self._price_x - self._entry_fill.leg_x.fill_price)
            * self._entry_qty_x
            * (1 if self._entry_side_x == "buy" else -1)
        )

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    async def _open_position(self, sig, ts: pd.Timestamp):
        # Daily DD gate
        if abs(self._daily_pnl) >= self.cfg.capital_usdt * self.cfg.max_daily_drawdown:
            logger.warning(f"Daily DD limit — halting | daily_pnl={self._daily_pnl:.2f}")
            self._state = TraderState.HALTED
            if self._bus:
                self._bus.update({"trader_state": TraderState.HALTED.value})
            await _send_alert(
                self.cfg.alert,
                f"Daily DD limit atins | daily_pnl={self._daily_pnl:.2f} | pair={self._pair_id}",
            )
            return

        # Sprint 7: WsWatchdog gate
        if self.cfg.watchdog_gate_entries and not self.watchdog.is_live:
            logger.warning(
                f"Entry BLOCKED: WsWatchdog state={self.watchdog.state} "
                f"(feed stale {self.watchdog.last_tick_age_s:.1f}s) — skip entry"
            )
            return

        # FIX-2: spread buffer real → Kelly primește volatilitate reală
        pnl_series = pd.Series(self._trade_pnl_history) if self._trade_pnl_history else None
        if len(self._spread_buffer) >= 10:
            spread_series = pd.Series(list(self._spread_buffer))
        else:
            # Buffer insuficient — folosim spread constant și logăm
            spread_series = pd.Series([sig.spread] * max(30, 1))
            logger.warning(
                f"Spread buffer insuficient ({len(self._spread_buffer)} ticks) — "
                f"Kelly va folosi vol_target fallback"
            )

        decision = self.allocator.request_entry(
            pair_id=self._pair_id,
            candidate_spread=spread_series,
            trade_pnl_history=pnl_series,
            current_zscore=sig.zscore,
            entry_beta=sig.hedge_ratio,
        )

        if not decision.allowed:
            logger.info(f"Entry BLOCKED by allocator: {decision.reject_reason}")
            return

        notional_y = decision.notional_usd
        hedge_ratio = sig.hedge_ratio
        qty_y = notional_y / self._price_y
        qty_x = qty_y * abs(hedge_ratio)

        side_y = "buy" if getattr(sig, "direction", 1) > 0 else "sell"
        side_x = "sell" if side_y == "buy" else "buy"

        logger.info(
            f"ENTRY | {side_y} {self.cfg.sym_y} {qty_y:.4f}@{self._price_y:.4f} "
            f"| {side_x} {self.cfg.sym_x} {qty_x:.4f}@{self._price_x:.4f} "
            f"| z={sig.zscore:.3f} | beta={hedge_ratio:.4f} "
            f"| notional=${notional_y:.0f} "
            f"| method={decision.kelly_result.method_used if decision.kelly_result else 'n/a'} "
            f"| spread_buf={len(self._spread_buffer)}"
        )

        self._state = TraderState.IN_POSITION
        self._entry_zscore = sig.zscore
        self._entry_hedge_ratio = hedge_ratio
        if self._bus:
            self._bus.update({"trader_state": TraderState.IN_POSITION.value})

        try:
            fill_pair = await self.orders.execute_pair(
                self.cfg.sym_y, side_y, qty_y, self._price_y,
                self.cfg.sym_x, side_x, qty_x, self._price_x,
            )
            self._entry_side_y = side_y
            self._entry_side_x = side_x
            self._entry_qty_y = qty_y
            self._entry_qty_x = qty_x
            self._entry_fill = fill_pair
            self._entry_notional = notional_y
            self._total_fees += fill_pair.total_fee_usdt
        except Exception as exc:
            logger.error(f"ENTRY failed: {exc} — reverting to ACTIVE")
            self._state = TraderState.ACTIVE
            self.allocator.record_exit(self._pair_id)
            if self._bus:
                self._bus.update({"trader_state": TraderState.ACTIVE.value})

    async def _close_position(self, sig, ts: pd.Timestamp):
        self._state = TraderState.CLOSING
        if self._bus:
            self._bus.update({"trader_state": TraderState.CLOSING.value})

        close_side_y = "sell" if self._entry_side_y == "buy" else "buy"
        close_side_x = "sell" if self._entry_side_x == "buy" else "buy"

        logger.info(
            f"EXIT | {close_side_y} {self.cfg.sym_y} {self._entry_qty_y:.4f} "
            f"| {close_side_x} {self.cfg.sym_x} {self._entry_qty_x:.4f} "
            f"| reason={getattr(sig, 'reason', 'signal')}"
        )

        pnl = 0.0
        try:
            exit_fill = await self.orders.execute_pair(
                self.cfg.sym_y, close_side_y, self._entry_qty_y, self._price_y,
                self.cfg.sym_x, close_side_x, self._entry_qty_x, self._price_x,
            )
            pnl = self._compute_pnl(exit_fill)
            self._daily_pnl += pnl
            self._realized_pnl += pnl
            self._total_fees += exit_fill.total_fee_usdt
            self._trade_count += 1

            pnl_fraction = pnl / max(self.cfg.capital_usdt, 1.0)
            self._trade_pnl_history.append(pnl_fraction)
            if len(self._trade_pnl_history) > 200:
                self._trade_pnl_history = self._trade_pnl_history[-200:]

            # FIX-8: persistă trade în SQLite
            self._trade_history.append({
                "ts": time.time(),
                "pair_y": self.cfg.sym_y,
                "pair_x": self.cfg.sym_x,
                "side_y": self._entry_side_y or "",
                "entry_y": self._entry_fill.leg_y.fill_price if self._entry_fill else 0.0,
                "entry_x": self._entry_fill.leg_x.fill_price if self._entry_fill else 0.0,
                "exit_y": exit_fill.leg_y.fill_price,
                "exit_x": exit_fill.leg_x.fill_price,
                "qty_y": self._entry_qty_y,
                "qty_x": self._entry_qty_x,
                "pnl_usdt": pnl,
                "fees_usdt": exit_fill.total_fee_usdt,
                "pnl_frac": pnl_fraction,
                "zscore_entry": self._entry_zscore,
                "hedge_ratio": self._entry_hedge_ratio,
            })

            if self._bus:
                self._bus.record_trade({
                    "ts": time.time(),
                    "side": self._entry_side_y or "",
                    "pnl": pnl,
                    "entry_y": self._entry_fill.leg_y.fill_price if self._entry_fill else 0.0,
                    "entry_x": self._entry_fill.leg_x.fill_price if self._entry_fill else 0.0,
                    "exit_y": exit_fill.leg_y.fill_price,
                    "exit_x": exit_fill.leg_x.fill_price,
                    "fees": exit_fill.total_fee_usdt,
                })

            logger.info(
                f"EXIT complete | PnL={pnl:.2f} USDT ({pnl_fraction:+.2%}) | "
                f"realized={self._realized_pnl:.2f} | trades={self._trade_count}"
            )
        except Exception as exc:
            logger.error(f"EXIT failed: {exc} — POSITION MAY STILL BE OPEN!")
            # FIX-4/7: alertă la exit eșuat — poziție potențial deschisă
            await _send_alert(
                self.cfg.alert,
                f"EXIT FAILED | pair={self._pair_id} | error={exc} | "
                f"VERIFICAȚI MANUAL POZIȚIILE!",
            )
            raise  # re-raise pentru close_all() retry logic
        finally:
            self.allocator.record_exit(self._pair_id)
            self._state = TraderState.ACTIVE
            if self._bus:
                self._bus.update({"trader_state": TraderState.ACTIVE.value})
            self._entry_side_y = self._entry_side_x = None
            self._entry_qty_y = self._entry_qty_x = 0.0
            self._entry_fill = None
            self._entry_notional = 0.0
            self._entry_zscore = 0.0
            self._entry_hedge_ratio = 0.0
            self._open_pnl = 0.0

    def _compute_pnl(self, exit_fill: FillPair) -> float:
        if self._entry_fill is None:
            return 0.0
        gross = (
            (exit_fill.leg_y.fill_price - self._entry_fill.leg_y.fill_price)
            * self._entry_qty_y
            * (1 if self._entry_side_y == "buy" else -1)
        ) + (
            (exit_fill.leg_x.fill_price - self._entry_fill.leg_x.fill_price)
            * self._entry_qty_x
            * (1 if self._entry_side_x == "buy" else -1)
        )
        fees = exit_fill.total_fee_usdt + self._entry_fill.total_fee_usdt
        return gross - fees

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def _heartbeat(self):
        while self._state != TraderState.HALTED:
            await asyncio.sleep(self.cfg.heartbeat_interval_s)
            logger.debug(
                f"Heartbeat | state={self._state.value} | "
                f"queue={self._queue.qsize()} | drops={self._queue_drops} | "
                f"Y={self._price_y:.4f} X={self._price_x:.4f} | "
                f"spread_buf={len(self._spread_buffer)} | "
                f"dd={self.allocator.dd_level.value} | "
                f"ws={self.watchdog.state} ({self.watchdog.last_tick_age_s:.1f}s ago)"
            )

    # ------------------------------------------------------------------
    # FIX-6: _on_reconnect cu Kalman reset explicit
    # ------------------------------------------------------------------

    async def _on_reconnect(self):
        if self._state not in (TraderState.IDLE, TraderState.HALTED):
            logger.warning(
                f"WS reconnected — reset warm-up + Kalman state | "
                f"entry inhibited for {self.cfg.min_warmup_bars} bars"
            )
            self._warming_bars = 0

            # FIX-6: reset Kalman filter la reconnect pentru a evita beta stale
            # reset_kalman() e no-op dacă metoda nu există (backwards compat)
            if hasattr(self.signal_gen, "reset_kalman"):
                try:
                    self.signal_gen.reset_kalman()
                    logger.info("Kalman state reset after WS reconnect")
                except Exception as exc:
                    logger.warning(f"Kalman reset failed (non-critical): {exc}")
            else:
                logger.warning(
                    "signal_gen nu are reset_kalman() — beta stale posibil după reconnect. "
                    "Adăugați reset_kalman() în LiveSignalAdapter pentru siguranță maximă."
                )

            if self._state != TraderState.IN_POSITION:
                self._state = TraderState.WARMING_UP
                if self._bus:
                    self._bus.update({"trader_state": TraderState.WARMING_UP.value})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reset_daily_pnl_if_needed(self, ts: pd.Timestamp):
        date = ts.normalize()
        if self._daily_reset_date is None or date > self._daily_reset_date:
            if self._daily_reset_date is not None:
                logger.info(
                    f"Daily PnL reset | prev={self._daily_pnl:.2f} USDT | date={date.date()}"
                )
            self._daily_pnl = 0.0
            self._daily_reset_date = date

    @property
    def is_trading_allowed(self) -> bool:
        return (
            self.allocator.is_trading_allowed
            and self.watchdog.is_live
            and self._state not in (TraderState.HALTED, TraderState.CLOSING)
        )
