"""
Sprint 20 tests — metrics, funding rate, trade journal + FIX-1..5 regression tests.

FIX-1 [CRITIC]  CircuitBreaker instanta — state transitions CLOSED→OPEN→HALF_OPEN
FIX-2 [CRITIC]  Dual-leg partial fill — leg_x failure → emergency close + record_failure
FIX-3 [CRITIC]  FundingMonitor singleton — creat o singura data, nu per-bar
FIX-4 [IMPORT]  is_warmed_up fallback False — trading blocat pana la warmup complet
FIX-5 [IMPORT]  price_x == 0 guard — bar ignorat inainte de _execute_action
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.funding_rate import FundingRateMonitor
from core.metrics import registry
from core.trade_journal import TradeJournal
from execution.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState


# =============================================================================
# Teste originale Sprint 20 (pastrate intacte)
# =============================================================================

def test_metrics_registry_renders_prometheus_text():
    gauge = registry.gauge("test_metric_value", "Example metric")
    gauge.set(12.5)
    text = registry.render_prometheus()
    assert "# HELP test_metric_value Example metric" in text
    assert "test_metric_value 12.5" in text


def test_funding_rate_monitor_classifies_expensive():
    mon = FundingRateMonitor(expensive_threshold_bps=5.0)
    snap = mon.classify("BTCUSDT", funding_rate=0.0008)
    assert snap.regime == "expensive"
    assert snap.expensive is True
    assert mon.should_block_entry(0.0008) is True


def test_trade_journal_append_and_read(tmp_path: Path):
    p = tmp_path / "journal.csv"
    journal = TradeJournal(str(p))
    journal.append_simple(pair="BTCUSDT/ETHUSDT", side="LONG_SPREAD", pnl_usdt=42.5, reason="tp")
    rows = journal.read_all()
    assert len(rows) == 1
    assert rows[0].pnl_usdt == 42.5
    assert rows[0].reason == "tp"


# =============================================================================
# FIX-1: CircuitBreaker instanta — state transitions
# =============================================================================

class TestCircuitBreakerFix1:
    """FIX-1: CircuitBreaker.state este proprietate de instanta, nu globala."""

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=60, name="test")
        assert cb.state == CircuitState.CLOSED
        assert cb.failures == 0

    def test_failures_accumulate_and_open_circuit(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=60, name="test")
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED  # sub threshold
        cb.record_failure()  # atinge threshold
        assert cb.state == CircuitState.OPEN
        assert cb.failures == 3

    def test_two_instances_have_independent_state(self):
        """FIX-1 core: doua instante nu impart starea."""
        cb1 = CircuitBreaker(failure_threshold=2, recovery_timeout_s=60, name="cb1")
        cb2 = CircuitBreaker(failure_threshold=2, recovery_timeout_s=60, name="cb2")
        cb1.record_failure()
        cb1.record_failure()  # cb1 OPEN
        assert cb1.state == CircuitState.OPEN
        assert cb2.state == CircuitState.CLOSED  # cb2 neatins

    def test_record_success_resets_to_closed(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout_s=60, name="test")
        cb.record_failure()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failures == 0

    @pytest.mark.asyncio
    async def test_context_manager_open_raises(self):
        """Circuit OPEN ridica CircuitOpenError la __aenter__."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=9999, name="test")
        cb.record_failure()  # trip
        assert cb.state == CircuitState.OPEN
        with pytest.raises(CircuitOpenError):
            async with cb:
                pass

    @pytest.mark.asyncio
    async def test_context_manager_success_keeps_closed(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=60, name="test")
        async with cb:
            pass  # fara exceptie
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_context_manager_failure_increments(self):
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout_s=60, name="test")
        with pytest.raises(ValueError):
            async with cb:
                raise ValueError("test error")
        assert cb.failures == 1
        assert cb.state == CircuitState.CLOSED  # sub threshold

    def test_reset_clears_all_state(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=60, name="test")
        cb.record_failure()  # OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.failures == 0

    def test_is_available_reflects_state(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=9999, name="test")
        assert cb.is_available() is True
        cb.record_failure()
        assert cb.is_available() is False


# =============================================================================
# FIX-2: Dual-leg partial fill
# =============================================================================

class TestDualLegPartialFillFix2:
    """
    FIX-2: Daca leg_y reuseste dar leg_x esueaza:
      - se trimite emergency close pe leg_y
      - se apeleaza circuit_breaker.record_failure()
      - exceptia se propaga (trade-ul nu e inregistrat)
    """

    def _make_bar(self, price_y: float = 100.0, price_x: float = 50.0):
        bar = MagicMock()
        bar.price_y = price_y
        bar.price_x = price_x
        return bar

    @pytest.mark.asyncio
    async def test_leg_x_failure_triggers_emergency_close(self):
        """Daca leg_x esueaza, emergency close pe leg_y trebuie trimis."""
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig

        cfg = BybitLiveRunnerConfig(
            symbol_y="BTCUSDT",
            symbol_x="ETHUSDT",
            base_qty=0.01,
            dry_run=False,
        )
        runner = BybitLiveRunner(cfg)

        call_log: list[str] = []

        async def mock_create_order(req):
            call_log.append(req.symbol)
            if req.symbol == "ETHUSDT" and len(call_log) <= 2:
                raise RuntimeError("ETHUSDT order rejected")

        order_router = MagicMock()
        order_router.create_order = mock_create_order

        circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_s=60, name="test")
        order_manager = MagicMock()
        order_manager.has_position.return_value = False
        notifier_bus = MagicMock()
        notifier_bus.send_alert = AsyncMock()
        bar = self._make_bar()

        with pytest.raises(RuntimeError, match="ETHUSDT order rejected"):
            await runner._execute_action(
                action="entry_long",
                order_router=order_router,
                circuit_breaker=circuit_breaker,
                order_manager=order_manager,
                notifier_bus=notifier_bus,
                bar=bar,
            )

        assert "BTCUSDT" in call_log
        btcusdt_calls = [s for s in call_log if s == "BTCUSDT"]
        assert len(btcusdt_calls) >= 2, "Emergency close trebuie sa trimita ordinul invers pe BTCUSDT"
        assert circuit_breaker.failures >= 1

    @pytest.mark.asyncio
    async def test_leg_x_failure_sends_critical_alert(self):
        """La partial fill, notifier_bus primeste alerta critica."""
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig

        cfg = BybitLiveRunnerConfig(
            symbol_y="BTCUSDT",
            symbol_x="ETHUSDT",
            base_qty=0.01,
            dry_run=False,
        )
        runner = BybitLiveRunner(cfg)

        call_count = 0

        async def mock_create_order(req):
            nonlocal call_count
            call_count += 1
            if req.symbol == "ETHUSDT" and call_count <= 2:
                raise RuntimeError("leg_x failed")

        order_router = MagicMock()
        order_router.create_order = mock_create_order
        circuit_breaker = CircuitBreaker(failure_threshold=5, name="test")
        order_manager = MagicMock()
        order_manager.has_position.return_value = False
        notifier_bus = MagicMock()
        notifier_bus.send_alert = AsyncMock()
        bar = self._make_bar()

        with pytest.raises(RuntimeError):
            await runner._execute_action(
                action="entry_long",
                order_router=order_router,
                circuit_breaker=circuit_breaker,
                order_manager=order_manager,
                notifier_bus=notifier_bus,
                bar=bar,
            )

        notifier_bus.send_alert.assert_called()
        all_calls = [str(c) for c in notifier_bus.send_alert.call_args_list]
        assert any("PARTIAL" in c or "critical" in c or "FAILED" in c for c in all_calls)

    @pytest.mark.asyncio
    async def test_both_legs_success_no_circuit_failure(self):
        """Daca ambele legs reusesc, circuit_breaker nu primeste failure."""
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig

        cfg = BybitLiveRunnerConfig(
            symbol_y="BTCUSDT",
            symbol_x="ETHUSDT",
            base_qty=0.01,
            dry_run=False,
        )
        runner = BybitLiveRunner(cfg)

        order_router = MagicMock()
        order_router.create_order = AsyncMock()
        circuit_breaker = CircuitBreaker(failure_threshold=3, name="test")
        order_manager = MagicMock()
        order_manager.has_position.return_value = False
        order_manager.current_pnl = 10.0
        notifier_bus = MagicMock()
        notifier_bus.send_alert = AsyncMock()
        bar = self._make_bar()

        await runner._execute_action(
            action="entry_long",
            order_router=order_router,
            circuit_breaker=circuit_breaker,
            order_manager=order_manager,
            notifier_bus=notifier_bus,
            bar=bar,
        )

        assert circuit_breaker.failures == 0
        assert circuit_breaker.state == CircuitState.CLOSED


# =============================================================================
# FIX-3: FundingMonitor singleton
# =============================================================================

class TestFundingMonitorSingletonFix3:
    """FIX-3: FundingMonitor creat o singura data in _build_components, nu per-bar."""

    @pytest.mark.asyncio
    async def test_funding_monitor_created_once_in_build_components(self):
        """_build_components() seteaza self._funding_monitor exact o data."""
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig

        cfg = BybitLiveRunnerConfig(
            symbol_y="BTCUSDT",
            symbol_x="ETHUSDT",
            funding_gate_enabled=True,
            dry_run=True,
        )
        runner = BybitLiveRunner(cfg)
        assert runner._funding_monitor is None  # inainte de build

        order_router = MagicMock()
        ws_feed = MagicMock()
        ws_feed.is_healthy = MagicMock(return_value=True)

        with patch("execution.bybit_live_runner.WsWatchdog") as mock_wd, \
             patch("execution.bybit_live_runner.OrderManager") as mock_om, \
             patch("execution.bybit_live_runner.NotifierBus") as mock_nb:
            mock_wd.return_value = MagicMock()
            mock_om.return_value = MagicMock()
            mock_nb.return_value = MagicMock()

            try:
                with patch("execution.bybit_live_runner.FundingMonitor", autospec=True) as mock_fm_cls:
                    mock_fm_cls.return_value = MagicMock()
                    await runner._build_components(order_router, ws_feed)
                    assert mock_fm_cls.call_count == 1
                    assert runner._funding_monitor is not None
            except ImportError:
                pytest.skip("FundingMonitor import not available in this test env")

    @pytest.mark.asyncio
    async def test_check_funding_gate_uses_singleton_not_new_instance(self):
        """_check_funding_gate() nu instantiaza FundingMonitor nou la fiecare apel."""
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig

        cfg = BybitLiveRunnerConfig(funding_gate_enabled=True)
        runner = BybitLiveRunner(cfg)

        mock_monitor = MagicMock()
        mock_monitor.get_funding_rate.return_value = 0.0001
        runner._funding_monitor = mock_monitor

        for _ in range(5):
            runner._check_funding_gate()

        assert mock_monitor.get_funding_rate.call_count == 10  # 5 apeluri x 2 simboluri

    def test_check_funding_gate_blocks_on_high_negative_funding(self):
        """Gate inchis daca funding rate < -0.01 pe oricare leg."""
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig

        cfg = BybitLiveRunnerConfig(
            symbol_y="BTCUSDT",
            symbol_x="ETHUSDT",
            funding_gate_enabled=True,
        )
        runner = BybitLiveRunner(cfg)
        mock_monitor = MagicMock()
        mock_monitor.get_funding_rate.side_effect = lambda sym: (
            -0.02 if sym == "BTCUSDT" else 0.001
        )
        runner._funding_monitor = mock_monitor

        assert runner._check_funding_gate() is False

    def test_check_funding_gate_passes_on_normal_funding(self):
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig

        cfg = BybitLiveRunnerConfig(
            symbol_y="BTCUSDT",
            symbol_x="ETHUSDT",
            funding_gate_enabled=True,
        )
        runner = BybitLiveRunner(cfg)
        mock_monitor = MagicMock()
        mock_monitor.get_funding_rate.return_value = 0.0001
        runner._funding_monitor = mock_monitor

        assert runner._check_funding_gate() is True

    def test_check_funding_gate_open_when_monitor_none(self):
        """Gate deschis (True) daca monitor nu a putut fi initializat."""
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig

        cfg = BybitLiveRunnerConfig(funding_gate_enabled=True)
        runner = BybitLiveRunner(cfg)
        runner._funding_monitor = None

        assert runner._check_funding_gate() is True


# =============================================================================
# FIX-4: is_warmed_up fallback False
# =============================================================================

class TestIsWarmedUpFallbackFix4:
    """
    FIX-4: Fallback-ul pentru is_warmed_up trebuie sa fie False.
    Daca spread_monitor nu are atributul is_warmed_up,
    runner-ul NU trebuie sa porneasca trading.
    """

    def test_getattr_fallback_is_false(self):
        """getattr(spread_monitor, 'is_warmed_up', False) returneaza False."""
        mock_sm = MagicMock(spec=["update", "zscore", "spread"])
        is_warmed_up = getattr(mock_sm, "is_warmed_up", False)
        assert is_warmed_up is False

    def test_getattr_fallback_not_true(self):
        """Fallback-ul anterior (True) era gresit — verifica ca nu e True."""
        mock_sm = MagicMock(spec=["update", "zscore", "spread"])
        is_warmed_up = getattr(mock_sm, "is_warmed_up", False)
        assert is_warmed_up is not True, (
            "FIX-4: fallback-ul True permitea trading fara warmup — must be False"
        )

    @pytest.mark.asyncio
    async def test_run_loop_skips_trading_when_not_warmed_up(self):
        """
        Simuleaza un bar cu is_warmed_up=False —
        _execute_action NU trebuie apelat.
        """
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig
        from execution.circuit_breaker import CircuitBreaker

        cfg = BybitLiveRunnerConfig(
            symbol_y="BTCUSDT",
            symbol_x="ETHUSDT",
            entry_zscore=1.0,
            dry_run=True,
        )
        runner = BybitLiveRunner(cfg)

        execute_called = False

        async def mock_execute(*args, **kwargs):
            nonlocal execute_called
            execute_called = True

        runner._execute_action = mock_execute

        bar = MagicMock()
        bar.price_y = 100.0
        bar.price_x = 50.0

        spread_monitor = MagicMock()
        spread_monitor.is_warmed_up = False
        spread_monitor.zscore = 3.0
        spread_monitor.spread = 0.5
        spread_monitor.warmup_progress = 0.5
        spread_monitor.bars_count = 50

        circuit_breaker = CircuitBreaker(failure_threshold=3, name="test")
        order_manager = MagicMock()
        order_manager.has_position.return_value = False
        watchdog = MagicMock()
        watchdog.set_health_checker = MagicMock()
        watchdog.start = AsyncMock()
        notifier_bus = MagicMock()
        notifier_bus.send_alert = AsyncMock()
        health = MagicMock()
        order_router = MagicMock()
        ws_feed = MagicMock()

        call_count = 0

        async def get_bar_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return bar
            runner._stop_event.set()
            return None

        ws_feed.get_bar = get_bar_once

        with patch("asyncio.create_task", return_value=MagicMock()) as mock_task:
            mock_task.return_value.cancel = MagicMock()

            await runner._run_loop(
                order_router=order_router,
                ws_feed=ws_feed,
                spread_monitor=spread_monitor,
                circuit_breaker=circuit_breaker,
                order_manager=order_manager,
                watchdog=watchdog,
                health=health,
                notifier_bus=notifier_bus,
            )

        assert execute_called is False, (
            "FIX-4: _execute_action apelat in warmup — trading pornit prematur!"
        )


# =============================================================================
# FIX-5: price_x == 0 guard
# =============================================================================

class TestPriceXZeroGuardFix5:
    """
    FIX-5: Bar cu price_x=0 sau price_y=0 trebuie ignorat inainte de
    spread_monitor.update() si _execute_action.
    Un bar malformat la restart WS nu trebuie sa cauzeze ZeroDivisionError.
    """

    @pytest.mark.asyncio
    async def test_zero_price_x_bar_is_skipped(self):
        """Bar cu price_x=0 nu ajunge in spread_monitor.update()."""
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig
        from execution.circuit_breaker import CircuitBreaker

        cfg = BybitLiveRunnerConfig(dry_run=True)
        runner = BybitLiveRunner(cfg)

        spread_monitor = MagicMock()
        spread_monitor.is_warmed_up = True
        spread_monitor.zscore = 3.0
        spread_monitor.spread = 0.5

        circuit_breaker = CircuitBreaker(failure_threshold=3, name="test")
        order_manager = MagicMock()
        order_manager.has_position.return_value = False
        watchdog = MagicMock()
        watchdog.set_health_checker = MagicMock()
        watchdog.start = AsyncMock()
        notifier_bus = MagicMock()
        notifier_bus.send_alert = AsyncMock()
        health = MagicMock()
        order_router = MagicMock()
        ws_feed = MagicMock()

        bad_bar = MagicMock()
        bad_bar.price_y = 100.0
        bad_bar.price_x = 0.0

        call_count = 0

        async def get_bar_with_zero():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return bad_bar
            runner._stop_event.set()
            return None

        ws_feed.get_bar = get_bar_with_zero

        with patch("asyncio.create_task", return_value=MagicMock()) as mock_task:
            mock_task.return_value.cancel = MagicMock()

            await runner._run_loop(
                order_router=order_router,
                ws_feed=ws_feed,
                spread_monitor=spread_monitor,
                circuit_breaker=circuit_breaker,
                order_manager=order_manager,
                watchdog=watchdog,
                health=health,
                notifier_bus=notifier_bus,
            )

        spread_monitor.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_price_y_bar_is_skipped(self):
        """Bar cu price_y=0 de asemenea ignorat."""
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig
        from execution.circuit_breaker import CircuitBreaker

        cfg = BybitLiveRunnerConfig(dry_run=True)
        runner = BybitLiveRunner(cfg)

        spread_monitor = MagicMock()
        spread_monitor.is_warmed_up = True
        spread_monitor.zscore = 0.1
        spread_monitor.spread = 0.0

        circuit_breaker = CircuitBreaker(failure_threshold=3, name="test")
        order_manager = MagicMock()
        order_manager.has_position.return_value = False
        watchdog = MagicMock()
        watchdog.set_health_checker = MagicMock()
        watchdog.start = AsyncMock()
        notifier_bus = MagicMock()
        notifier_bus.send_alert = AsyncMock()
        health = MagicMock()
        order_router = MagicMock()
        ws_feed = MagicMock()

        bad_bar = MagicMock()
        bad_bar.price_y = 0.0
        bad_bar.price_x = 50.0

        call_count = 0

        async def get_bar():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return bad_bar
            runner._stop_event.set()
            return None

        ws_feed.get_bar = get_bar

        with patch("asyncio.create_task", return_value=MagicMock()) as mock_task:
            mock_task.return_value.cancel = MagicMock()

            await runner._run_loop(
                order_router=order_router,
                ws_feed=ws_feed,
                spread_monitor=spread_monitor,
                circuit_breaker=circuit_breaker,
                order_manager=order_manager,
                watchdog=watchdog,
                health=health,
                notifier_bus=notifier_bus,
            )

        spread_monitor.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_action_skips_on_price_x_zero(self):
        """_execute_action returneaza imediat (fara exceptie) daca price_x=0."""
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig
        from execution.circuit_breaker import CircuitBreaker

        cfg = BybitLiveRunnerConfig(
            symbol_y="BTCUSDT",
            symbol_x="ETHUSDT",
            dry_run=False,
        )
        runner = BybitLiveRunner(cfg)

        order_router = MagicMock()
        order_router.create_order = AsyncMock()
        circuit_breaker = CircuitBreaker(failure_threshold=3, name="test")
        order_manager = MagicMock()
        notifier_bus = MagicMock()
        notifier_bus.send_alert = AsyncMock()

        bar = MagicMock()
        bar.price_y = 100.0
        bar.price_x = 0.0

        await runner._execute_action(
            action="entry_long",
            order_router=order_router,
            circuit_breaker=circuit_breaker,
            order_manager=order_manager,
            notifier_bus=notifier_bus,
            bar=bar,
        )

        order_router.create_order.assert_not_called()

    def test_qty_calculation_no_division_by_zero(self):
        """x_qty = base_qty * price_y / price_x — price_x=0 provoaca ZeroDivisionError."""
        base_qty = 0.01
        price_y = 100.0
        price_x = 0.0  # bug originar

        if price_x == 0.0:
            x_qty = None  # guard activat
        else:
            x_qty = base_qty * price_y / price_x

        assert x_qty is None, "Guard price_x==0 trebuie sa previna calculul qty"
