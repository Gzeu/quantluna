"""
tests/test_sprint22.py — teste Sprint 22.

Acopera: CircuitBreaker, DynamicStop, CorrelationMatrix, WSReconnectManager.
"""
import asyncio
import pytest


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def _make(self, **kw):
        from risk.circuit_breaker import CircuitBreaker
        return CircuitBreaker(**kw)

    def test_initial_state_closed(self):
        cb = self._make()
        assert cb.allow() is True

    def test_trips_on_consecutive_losses(self):
        cb = self._make(max_consecutive_losses=3)
        cb.record_loss(10)
        cb.record_loss(10)
        assert cb.allow() is True
        cb.record_loss(10)
        assert cb.allow() is False
        assert "consecutive" in cb.last_reason

    def test_trips_on_drawdown(self):
        cb = self._make(max_drawdown_pct=0.10)
        cb.set_equity(1000.0)
        cb.record_loss(110.0)  # 11% drawdown
        assert cb.allow() is False

    def test_record_win_resets_consecutive(self):
        cb = self._make(max_consecutive_losses=3)
        cb.record_loss(10)
        cb.record_loss(10)
        cb.record_win(20)
        cb.record_loss(10)  # should be 1 now
        assert cb.allow() is True

    def test_manual_reset(self):
        cb = self._make(max_consecutive_losses=1)
        cb.record_loss(10)
        assert cb.allow() is False
        cb.reset()
        assert cb.allow() is True

    def test_error_trips(self):
        cb = self._make(max_errors=2)
        cb.record_error()
        assert cb.allow() is True
        cb.record_error()
        assert cb.allow() is False

    def test_events_logged(self):
        cb = self._make(max_consecutive_losses=1)
        cb.record_loss(5)
        assert len(cb.events) == 1


# ---------------------------------------------------------------------------
# DynamicStop
# ---------------------------------------------------------------------------

class TestDynamicStop:
    def _make(self, **kw):
        from risk.dynamic_stop import DynamicStop
        return DynamicStop(**kw)

    def test_long_levels(self):
        ds = self._make(atr_multiplier=2.0, rr_ratio=2.0)
        lv = ds.calculate(entry=100.0, atr=1.0, direction="LONG")
        assert lv.stop_loss < lv.entry
        assert lv.take_profit > lv.entry
        assert lv.rr_ratio == 2.0

    def test_short_levels(self):
        ds = self._make(atr_multiplier=2.0, rr_ratio=2.0)
        lv = ds.calculate(entry=100.0, atr=1.0, direction="SHORT")
        assert lv.stop_loss > lv.entry
        assert lv.take_profit < lv.entry

    def test_trailing_stop_long_advances(self):
        ds = self._make(atr_multiplier=2.0)
        new_sl = ds.trailing_stop(
            current_price=110.0, current_stop=95.0, atr=1.0, direction="LONG"
        )
        assert new_sl > 95.0

    def test_trailing_stop_long_does_not_go_back(self):
        ds = self._make(atr_multiplier=2.0)
        sl = ds.trailing_stop(
            current_price=96.0, current_stop=95.0, atr=1.0, direction="LONG"
        )
        assert sl >= 95.0

    def test_breakeven_triggers(self):
        ds = self._make(atr_multiplier=2.0, breakeven_atr=1.0)
        new_sl = ds.breakeven_stop(
            entry=100.0, current_price=102.0,
            current_stop=98.0, atr=1.0, direction="LONG"
        )
        assert new_sl >= 100.0

    def test_breakeven_not_triggered_too_early(self):
        ds = self._make(atr_multiplier=2.0, breakeven_atr=1.0)
        new_sl = ds.breakeven_stop(
            entry=100.0, current_price=100.3,
            current_stop=98.0, atr=1.0, direction="LONG"
        )
        assert new_sl == 98.0


# ---------------------------------------------------------------------------
# CorrelationMatrix
# ---------------------------------------------------------------------------

class TestCorrelationMatrix:
    def _make(self, **kw):
        from core.correlation_matrix import CorrelationMatrix
        return CorrelationMatrix(**kw)

    def test_insufficient_data_returns_none(self):
        cm = self._make()
        cm.update("A", 1.0)
        cm.update("B", 1.0)
        assert cm.get_correlation("A", "B") is None

    def test_perfect_positive_correlation(self):
        cm = self._make(window=20)
        for i in range(20):
            cm.update("A", float(i))
            cm.update("B", float(i))
        corr = cm.get_correlation("A", "B")
        assert corr is not None
        assert abs(corr - 1.0) < 1e-6

    def test_perfect_negative_correlation(self):
        cm = self._make(window=20)
        for i in range(20):
            cm.update("A", float(i))
            cm.update("B", float(-i))
        corr = cm.get_correlation("A", "B")
        assert corr is not None
        assert abs(corr + 1.0) < 1e-6

    def test_unknown_symbol_returns_none(self):
        cm = self._make()
        assert cm.get_correlation("X", "Y") is None

    def test_high_correlation_alert(self):
        cm = self._make(window=20, high_corr_threshold=0.90)
        for i in range(20):
            cm.update("A", float(i))
            cm.update("B", float(i) + 0.001)
        alerts = cm.get_high_correlation_pairs()
        assert len(alerts) == 1
        assert alerts[0][0] in ("A", "B")

    def test_reset_single_symbol(self):
        cm = self._make()
        cm.update("A", 1.0)
        cm.reset("A")
        assert "A" not in cm.symbols


# ---------------------------------------------------------------------------
# WSReconnectManager
# ---------------------------------------------------------------------------

class TestWSReconnectManager:
    def test_stops_after_max_retries(self):
        from core.ws_reconnect import WSReconnectManager

        call_count = 0

        async def failing_connect():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("simulated")

        async def run():
            mgr = WSReconnectManager(
                connect_fn=failing_connect,
                max_retries=3,
                initial_delay=0.01,
                max_delay=0.05,
            )
            await mgr.run()
            return mgr

        mgr = asyncio.run(run())
        assert call_count == 3

    def test_resets_attempt_on_success(self):
        from core.ws_reconnect import WSReconnectManager

        calls = []

        async def connect_once():
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("first fail")
            # success on second call — return normally

        async def run():
            mgr = WSReconnectManager(
                connect_fn=connect_once,
                max_retries=5,
                initial_delay=0.01,
                max_delay=0.05,
            )
            await mgr.run()
            return mgr

        mgr = asyncio.run(run())
        assert mgr.attempt == 0
