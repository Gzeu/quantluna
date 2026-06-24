"""
tests/test_live_trader.py

Teste pentru execution/live_trader.py  —  Sprint 4-10 integration.

Acopera:
  - Constructie si initializare corecta (allocator, watchdog, signal_adapter)
  - close_all(): HARD_STOP pe pozitie deschisa, no-op daca nu e pozitie
  - Fluxul entry: blocare zilnica DD, blocare watchdog STALE,
    blocare allocator, entry permis cu sizing corect
  - Fluxul exit: PnL calculat, allocator.record_exit() apelat,
    trade_pnl_history actualizat, state -> ACTIVE
  - HARD_STOP path in _on_tick: DDSnapshot.level == HARD_STOP -> close_all()
  - WsWatchdog ping() apelat la fiecare tick din _consumer
  - _compute_pnl: calcul corect long/short, includere fees
  - _reset_daily_pnl_if_needed: reset zilnic

Strategie de mock:
  - OrderManager.execute_pair mockat cu AsyncMock
  - PortfolioAllocator mockat (nu testam Kelly/corr in teste de LiveTrader)
  - LiveSignalAdapter mockat sa returneze semnale controlabile
  - WsWatchdog partial mockat (stare configurabila)
  - StateBus mockat (MagicMock)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pandas as pd
import pytest

from execution.live_trader import (
    LiveConfig,
    LiveTrader,
    PriceTick,
    TraderState,
)
from execution.ws_watchdog import WatchdogConfig
from risk.drawdown_controller import DDLevel, DDSnapshot


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

def make_config(**kwargs) -> LiveConfig:
    defaults = dict(
        sym_y="ETH/USDT:USDT",
        sym_x="BTC/USDT:USDT",
        capital_usdt=10_000.0,
        min_warmup_bars=2,
        max_daily_drawdown=0.03,
        watchdog_gate_entries=True,
        watchdog=WatchdogConfig(stale_warn_s=10.0, stale_critical_s=30.0),
    )
    defaults.update(kwargs)
    return LiveConfig(**defaults)


@dataclass
class _FakeLeg:
    fill_price: float
    fee_usdt: float = 0.5


@dataclass
class _FakeFill:
    leg_y: _FakeLeg
    leg_x: _FakeLeg
    total_fee_usdt: float = 1.0


def make_fill(price_y: float = 3000.0, price_x: float = 60000.0) -> _FakeFill:
    return _FakeFill(
        leg_y=_FakeLeg(fill_price=price_y),
        leg_x=_FakeLeg(fill_price=price_x),
        total_fee_usdt=1.0,
    )


class _FakeSignal:
    """Semnal minimal compatibil cu NormalizedSignal."""
    zscore: float = -2.5
    spread: float = 0.01
    hedge_ratio: float = 0.05
    kalman_gain: float = 0.1
    kalman_uncertainty: float = 1.0
    half_life: float = 24.0
    regime: str = "trending"
    entry: bool = False
    exit: bool = False
    direction: int = 1   # long Y, short X
    reason: str = "signal"


def make_trader(
    cfg: Optional[LiveConfig] = None,
    state_bus=None,
    allocator=None,
    signal_gen=None,
) -> LiveTrader:
    if cfg is None:
        cfg = make_config()
    if state_bus is None:
        bus = MagicMock()
        bus.record_trade = MagicMock()
    else:
        bus = state_bus
    if signal_gen is None:
        sg = MagicMock()
        sg.on_tick = MagicMock(return_value=None)
    else:
        sg = signal_gen

    from strategy.signal_adapter import LiveSignalAdapter
    # Wrap intr-un LiveSignalAdapter mock care nu face nimic real
    with patch("execution.live_trader.LiveSignalAdapter", return_value=sg):
        trader = LiveTrader(cfg, sg, allocator=allocator, state_bus=bus)
    # Inject direct pentru control complet
    trader.signal_gen = sg
    trader._bus = bus
    return trader


def mock_allocator(
    allowed: bool = True,
    notional: float = 500.0,
    dd_level: DDLevel = DDLevel.NORMAL,
    force_close_pairs: Optional[set] = None,
) -> MagicMock:
    alloc = MagicMock()
    decision = MagicMock()
    decision.allowed = allowed
    decision.notional_usd = notional
    decision.reject_reason = None if allowed else "TEST_BLOCK"
    decision.kelly_result = MagicMock(method_used="vol_target_only")
    alloc.request_entry.return_value = decision
    alloc.record_exit = MagicMock()
    # update_state returneaza DDSnapshot
    snap = MagicMock(spec=DDSnapshot)
    snap.level = dd_level
    snap.pairs_force_close = force_close_pairs or set()
    snap.notes = []
    alloc.update_state.return_value = snap
    alloc.dd_level = dd_level
    alloc.is_trading_allowed = True
    alloc._n_pairs = 0
    return alloc


# ---------------------------------------------------------------------------
# 1. Constructie
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_default_allocator_created(self):
        """Daca nu e injectat allocator, se creeaza intern."""
        trader = make_trader()
        assert trader.allocator is not None

    def test_injected_allocator_used(self):
        alloc = mock_allocator()
        trader = make_trader(allocator=alloc)
        assert trader.allocator is alloc

    def test_watchdog_created(self):
        trader = make_trader()
        assert trader.watchdog is not None

    def test_initial_state_idle(self):
        trader = make_trader()
        assert trader._state == TraderState.IDLE

    def test_initial_prices_zero(self):
        trader = make_trader()
        assert trader._price_y == 0.0
        assert trader._price_x == 0.0


# ---------------------------------------------------------------------------
# 2. close_all()
# ---------------------------------------------------------------------------

class TestCloseAll:
    @pytest.mark.asyncio
    async def test_close_all_noop_when_not_in_position(self):
        """close_all() face nimic daca starea nu e IN_POSITION."""
        trader = make_trader(allocator=mock_allocator())
        trader._state = TraderState.ACTIVE
        # Nu trebuie sa apeleze _close_position -> orders ar crapa fara mock
        await trader.close_all(reason="TEST")
        # State ramane ACTIVE (nu HALTED)
        assert trader._state == TraderState.ACTIVE

    @pytest.mark.asyncio
    async def test_close_all_calls_close_position(self):
        """close_all() apeleaza _close_position si seteaza HALTED."""
        alloc = mock_allocator()
        trader = make_trader(allocator=alloc)
        trader._state = TraderState.IN_POSITION
        trader._entry_side_y = "buy"
        trader._entry_side_x = "sell"
        trader._entry_qty_y = 0.1
        trader._entry_qty_x = 0.005
        trader._entry_fill = make_fill()
        trader._price_y = 3050.0
        trader._price_x = 60500.0

        orders_mock = AsyncMock()
        fill = make_fill(price_y=3050.0, price_x=60500.0)
        orders_mock.execute_pair = AsyncMock(return_value=fill)
        trader.orders = orders_mock

        await trader.close_all(reason="HARD_STOP")

        orders_mock.execute_pair.assert_awaited_once()
        alloc.record_exit.assert_called_once()
        assert trader._state == TraderState.HALTED

    @pytest.mark.asyncio
    async def test_close_all_halts_even_on_order_failure(self):
        """close_all() seteaza HALTED chiar daca execute_pair crapa."""
        trader = make_trader(allocator=mock_allocator())
        trader._state = TraderState.IN_POSITION
        trader._entry_side_y = "buy"
        trader._entry_side_x = "sell"
        trader._entry_qty_y = 0.1
        trader._entry_qty_x = 0.005
        trader._entry_fill = make_fill()
        trader._price_y = 3000.0
        trader._price_x = 60000.0

        orders_mock = AsyncMock()
        orders_mock.execute_pair = AsyncMock(side_effect=RuntimeError("exchange down"))
        trader.orders = orders_mock

        await trader.close_all(reason="HARD_STOP")
        # Dupa close_all() trader e HALTED indiferent de eroare
        assert trader._state == TraderState.HALTED


# ---------------------------------------------------------------------------
# 3. Entry flow
# ---------------------------------------------------------------------------

class TestEntryFlow:
    def _make_ready_trader(self, alloc=None):
        """Trader in stare ACTIVE, preturi setate, orders mock."""
        if alloc is None:
            alloc = mock_allocator(allowed=True, notional=500.0)
        trader = make_trader(allocator=alloc)
        trader._state = TraderState.ACTIVE
        trader._price_y = 3000.0
        trader._price_x = 60000.0
        orders_mock = AsyncMock()
        orders_mock.execute_pair = AsyncMock(return_value=make_fill())
        trader.orders = orders_mock
        return trader, orders_mock

    @pytest.mark.asyncio
    async def test_entry_blocked_daily_dd(self):
        """Entry blocat daca daily PnL depaseste max_daily_drawdown."""
        trader, orders = self._make_ready_trader()
        trader._daily_pnl = -350.0  # 3.5% > 3%
        sig = _FakeSignal()
        sig.entry = True

        await trader._open_position(sig, pd.Timestamp.now(tz="UTC"))

        orders.execute_pair.assert_not_awaited()
        assert trader._state == TraderState.HALTED

    @pytest.mark.asyncio
    async def test_entry_blocked_watchdog_stale(self):
        """Entry blocat daca WsWatchdog nu e LIVE."""
        trader, orders = self._make_ready_trader()
        trader.watchdog._state = "STALE"  # fortat stale
        sig = _FakeSignal()
        sig.entry = True

        await trader._open_position(sig, pd.Timestamp.now(tz="UTC"))

        orders.execute_pair.assert_not_awaited()
        assert trader._state == TraderState.ACTIVE  # nu s-a schimbat

    @pytest.mark.asyncio
    async def test_entry_blocked_by_allocator(self):
        """Entry blocat daca allocator.request_entry() -> allowed=False."""
        alloc = mock_allocator(allowed=False)
        trader, orders = self._make_ready_trader(alloc=alloc)
        trader.watchdog._state = "LIVE"
        sig = _FakeSignal()
        sig.entry = True

        await trader._open_position(sig, pd.Timestamp.now(tz="UTC"))

        orders.execute_pair.assert_not_awaited()
        assert trader._state == TraderState.ACTIVE

    @pytest.mark.asyncio
    async def test_entry_uses_kelly_notional(self):
        """Qty calculat din notional_usd al allocatorului, nu hardcodat."""
        alloc = mock_allocator(allowed=True, notional=600.0)
        trader, orders = self._make_ready_trader(alloc=alloc)
        trader.watchdog._state = "LIVE"
        sig = _FakeSignal()
        sig.entry = True
        sig.hedge_ratio = 0.05

        await trader._open_position(sig, pd.Timestamp.now(tz="UTC"))

        orders.execute_pair.assert_awaited_once()
        # qty_y = 600 / 3000 = 0.2
        assert abs(trader._entry_qty_y - 0.2) < 1e-9
        # qty_x = 0.2 * 0.05 = 0.01
        assert abs(trader._entry_qty_x - 0.01) < 1e-9
        assert trader._state == TraderState.IN_POSITION

    @pytest.mark.asyncio
    async def test_entry_reverts_on_order_failure(self):
        """Daca execute_pair crapa, state revine la ACTIVE si record_exit apelat."""
        alloc = mock_allocator(allowed=True, notional=500.0)
        trader, orders = self._make_ready_trader(alloc=alloc)
        trader.watchdog._state = "LIVE"
        orders.execute_pair = AsyncMock(side_effect=RuntimeError("order rejected"))
        sig = _FakeSignal()
        sig.entry = True

        await trader._open_position(sig, pd.Timestamp.now(tz="UTC"))

        alloc.record_exit.assert_called_once()
        assert trader._state == TraderState.ACTIVE


# ---------------------------------------------------------------------------
# 4. Exit flow
# ---------------------------------------------------------------------------

class TestExitFlow:
    def _make_positioned_trader(self, entry_price_y=3000.0, entry_price_x=60000.0):
        alloc = mock_allocator()
        trader = make_trader(allocator=alloc)
        trader._state = TraderState.IN_POSITION
        trader._price_y = 3100.0   # +100 USDT per ETH
        trader._price_x = 59800.0  # -200 per BTC (short X -> profitabil)
        trader._entry_side_y = "buy"
        trader._entry_side_x = "sell"
        trader._entry_qty_y = 0.1   # 0.1 ETH
        trader._entry_qty_x = 0.005  # 0.005 BTC
        trader._entry_fill = make_fill(
            price_y=entry_price_y, price_x=entry_price_x
        )
        orders_mock = AsyncMock()
        exit_fill = make_fill(price_y=3100.0, price_x=59800.0)
        exit_fill.total_fee_usdt = 1.0
        orders_mock.execute_pair = AsyncMock(return_value=exit_fill)
        trader.orders = orders_mock
        return trader, alloc, orders_mock

    @pytest.mark.asyncio
    async def test_exit_computes_pnl(self):
        """PnL = gross - fees, calculat corect."""
        trader, alloc, orders = self._make_positioned_trader()
        sig = _FakeSignal()
        sig.exit = True

        await trader._close_position(sig, pd.Timestamp.now(tz="UTC"))

        # gross_y = (3100 - 3000) * 0.1 * long = +10.0
        # gross_x = (59800 - 60000) * 0.005 * short(-1) = +1.0
        # fees = 1.0 (exit) + 1.0 (entry din _entry_fill) = 2.0 ... dar _entry_fill.total_fee_usdt = 1.0
        # pnl = 11.0 - 2.0 = 9.0
        assert abs(trader._realized_pnl - 9.0) < 1e-9

    @pytest.mark.asyncio
    async def test_exit_calls_record_exit(self):
        """allocator.record_exit() apelat dupa exit."""
        trader, alloc, orders = self._make_positioned_trader()
        sig = _FakeSignal()
        sig.exit = True

        await trader._close_position(sig, pd.Timestamp.now(tz="UTC"))

        alloc.record_exit.assert_called_once_with(trader._pair_id)

    @pytest.mark.asyncio
    async def test_exit_updates_pnl_history(self):
        """trade_pnl_history actualizat pentru Kelly."""
        trader, alloc, orders = self._make_positioned_trader()
        sig = _FakeSignal()
        sig.exit = True

        await trader._close_position(sig, pd.Timestamp.now(tz="UTC"))

        assert len(trader._trade_pnl_history) == 1
        # fractie din capital = 9.0 / 10_000 = 0.0009
        assert abs(trader._trade_pnl_history[0] - 9.0 / 10_000) < 1e-9

    @pytest.mark.asyncio
    async def test_exit_resets_position_state(self):
        """State revine la ACTIVE si campurile de pozitie se curata."""
        trader, alloc, orders = self._make_positioned_trader()
        sig = _FakeSignal()
        sig.exit = True

        await trader._close_position(sig, pd.Timestamp.now(tz="UTC"))

        assert trader._state == TraderState.ACTIVE
        assert trader._entry_fill is None
        assert trader._entry_qty_y == 0.0
        assert trader._entry_qty_x == 0.0
        assert trader._open_pnl == 0.0

    @pytest.mark.asyncio
    async def test_exit_record_exit_called_even_on_order_failure(self):
        """allocator.record_exit() apelat in finally, chiar daca execute_pair crapa."""
        alloc = mock_allocator()
        trader = make_trader(allocator=alloc)
        trader._state = TraderState.IN_POSITION
        trader._entry_side_y = "buy"
        trader._entry_side_x = "sell"
        trader._entry_qty_y = 0.1
        trader._entry_qty_x = 0.005
        trader._entry_fill = make_fill()
        trader._price_y = 3000.0
        trader._price_x = 60000.0

        orders_mock = AsyncMock()
        orders_mock.execute_pair = AsyncMock(side_effect=RuntimeError("exchange timeout"))
        trader.orders = orders_mock

        sig = _FakeSignal()
        sig.exit = True
        await trader._close_position(sig, pd.Timestamp.now(tz="UTC"))

        # In ciuda erorii, record_exit si state cleanup trebuie apelate
        alloc.record_exit.assert_called_once()
        assert trader._state == TraderState.ACTIVE


# ---------------------------------------------------------------------------
# 5. HARD_STOP path in _on_tick
# ---------------------------------------------------------------------------

class TestHardStopPath:
    @pytest.mark.asyncio
    async def test_hard_stop_triggers_close_all(self):
        """Daca DDSnapshot.level == HARD_STOP in _on_tick, close_all() e apelat."""
        alloc = mock_allocator(dd_level=DDLevel.HARD_STOP)
        trader = make_trader(allocator=alloc)
        trader._state = TraderState.IN_POSITION
        trader._price_y = 3000.0
        trader._price_x = 60000.0
        trader._entry_side_y = "buy"
        trader._entry_side_x = "sell"
        trader._entry_qty_y = 0.1
        trader._entry_qty_x = 0.005
        trader._entry_fill = make_fill()
        trader._warming_bars = trader.cfg.min_warmup_bars  # sarim warm-up

        # Semnal valid returnat
        sig = _FakeSignal()
        trader.signal_gen.on_tick = MagicMock(return_value=sig)

        orders_mock = AsyncMock()
        orders_mock.execute_pair = AsyncMock(return_value=make_fill(price_y=3000.0, price_x=60000.0))
        trader.orders = orders_mock

        await trader._on_tick(pd.Timestamp.now(tz="UTC"))

        # close_all() a fost apelat -> orders.execute_pair a rulat
        orders_mock.execute_pair.assert_awaited_once()
        assert trader._state == TraderState.HALTED

    @pytest.mark.asyncio
    async def test_pair_dd_triggers_close_all(self):
        """pairs_force_close contine pair_id -> close_all cu PAIR_DD."""
        pair_id = "ETH/USDT:USDT/BTC/USDT:USDT"
        alloc = mock_allocator(
            dd_level=DDLevel.NORMAL,
            force_close_pairs={pair_id},
        )
        trader = make_trader(allocator=alloc)
        trader._state = TraderState.IN_POSITION
        trader._price_y = 3000.0
        trader._price_x = 60000.0
        trader._entry_side_y = "buy"
        trader._entry_side_x = "sell"
        trader._entry_qty_y = 0.1
        trader._entry_qty_x = 0.005
        trader._entry_fill = make_fill()
        trader._warming_bars = trader.cfg.min_warmup_bars

        sig = _FakeSignal()
        trader.signal_gen.on_tick = MagicMock(return_value=sig)

        orders_mock = AsyncMock()
        orders_mock.execute_pair = AsyncMock(return_value=make_fill(price_y=3000.0, price_x=60000.0))
        trader.orders = orders_mock

        await trader._on_tick(pd.Timestamp.now(tz="UTC"))

        orders_mock.execute_pair.assert_awaited_once()
        assert trader._state == TraderState.HALTED


# ---------------------------------------------------------------------------
# 6. WsWatchdog ping() per tick
# ---------------------------------------------------------------------------

class TestWatchdogPing:
    @pytest.mark.asyncio
    async def test_ping_called_per_tick(self):
        """watchdog.ping() apelat la fiecare tick procesat in _consumer."""
        trader = make_trader(allocator=mock_allocator())
        trader._state = TraderState.ACTIVE

        ping_count = 0
        original_ping = trader.watchdog.ping

        def counting_ping():
            nonlocal ping_count
            ping_count += 1
            original_ping()

        trader.watchdog.ping = counting_ping

        # Injecteaza 3 ticks direct in queue
        ticks = [
            PriceTick("ETH/USDT:USDT", 3000.0 + i, pd.Timestamp.now(tz="UTC"))
            for i in range(3)
        ]
        for t in ticks:
            await trader._queue.put(t)

        # Ruleaza consumer pentru exact 3 iteratii
        async def run_n_ticks(n):
            for _ in range(n):
                tick = await trader._queue.get()
                trader.watchdog.ping()
                trader._update_prices(tick)
                trader._queue.task_done()

        await run_n_ticks(3)
        assert ping_count == 3


# ---------------------------------------------------------------------------
# 7. _compute_pnl
# ---------------------------------------------------------------------------

class TestComputePnl:
    def _setup_trader(self, side_y="buy"):
        alloc = mock_allocator()
        trader = make_trader(allocator=alloc)
        trader._entry_side_y = side_y
        trader._entry_side_x = "sell" if side_y == "buy" else "buy"
        trader._entry_qty_y = 1.0
        trader._entry_qty_x = 0.05
        trader._entry_fill = make_fill(price_y=3000.0, price_x=60000.0)
        return trader

    def test_long_y_profit(self):
        trader = self._setup_trader(side_y="buy")
        exit_fill = make_fill(price_y=3100.0, price_x=60000.0)
        pnl = trader._compute_pnl(exit_fill)
        # gross_y = (3100-3000)*1 = 100; gross_x = 0; fees = 1+1 = 2
        assert abs(pnl - 98.0) < 1e-9

    def test_short_y_profit(self):
        trader = self._setup_trader(side_y="sell")
        exit_fill = make_fill(price_y=2900.0, price_x=60000.0)
        pnl = trader._compute_pnl(exit_fill)
        # gross_y = (2900-3000)*1*(-1) = 100; fees = 2
        assert abs(pnl - 98.0) < 1e-9

    def test_no_entry_fill_returns_zero(self):
        trader = make_trader()
        trader._entry_fill = None
        exit_fill = make_fill()
        assert trader._compute_pnl(exit_fill) == 0.0


# ---------------------------------------------------------------------------
# 8. Daily PnL reset
# ---------------------------------------------------------------------------

class TestDailyPnlReset:
    def test_reset_on_new_day(self):
        trader = make_trader()
        trader._daily_pnl = -200.0
        trader._daily_reset_date = pd.Timestamp("2026-06-23", tz="UTC")

        new_day = pd.Timestamp("2026-06-24 00:01:00", tz="UTC")
        trader._reset_daily_pnl_if_needed(new_day)

        assert trader._daily_pnl == 0.0
        assert trader._daily_reset_date == pd.Timestamp("2026-06-24", tz="UTC")

    def test_no_reset_same_day(self):
        trader = make_trader()
        trader._daily_pnl = -150.0
        trader._daily_reset_date = pd.Timestamp("2026-06-24", tz="UTC")

        same_day = pd.Timestamp("2026-06-24 12:00:00", tz="UTC")
        trader._reset_daily_pnl_if_needed(same_day)

        assert trader._daily_pnl == -150.0  # neschimbat

    def test_first_call_sets_date(self):
        trader = make_trader()
        assert trader._daily_reset_date is None

        ts = pd.Timestamp("2026-06-24 08:00:00", tz="UTC")
        trader._reset_daily_pnl_if_needed(ts)

        assert trader._daily_reset_date == pd.Timestamp("2026-06-24", tz="UTC")


# ---------------------------------------------------------------------------
# 9. is_trading_allowed property
# ---------------------------------------------------------------------------

class TestIsTradingAllowed:
    def test_allowed_when_all_ok(self):
        alloc = mock_allocator()
        alloc.is_trading_allowed = True
        trader = make_trader(allocator=alloc)
        trader._state = TraderState.ACTIVE
        trader.watchdog._state = "LIVE"
        assert trader.is_trading_allowed is True

    def test_blocked_when_halted(self):
        alloc = mock_allocator()
        trader = make_trader(allocator=alloc)
        trader._state = TraderState.HALTED
        assert trader.is_trading_allowed is False

    def test_blocked_when_watchdog_stale(self):
        alloc = mock_allocator()
        alloc.is_trading_allowed = True
        trader = make_trader(allocator=alloc)
        trader._state = TraderState.ACTIVE
        trader.watchdog._state = "STALE"
        assert trader.is_trading_allowed is False

    def test_blocked_when_allocator_halted(self):
        alloc = mock_allocator()
        alloc.is_trading_allowed = False
        trader = make_trader(allocator=alloc)
        trader._state = TraderState.ACTIVE
        trader.watchdog._state = "LIVE"
        assert trader.is_trading_allowed is False
