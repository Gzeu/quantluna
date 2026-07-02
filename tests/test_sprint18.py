"""
Sprint 18 tests:
  - SpreadMonitor: healthy, zscore drift, halflife slow, kalman divergence, stuck, cb break, reset
  - RegimeFilter: all pass, cb blocked, vol blocked, mtf blocked, spread blocked, size multiplier
  - NotifierBus: fan-out, disable, fail_silent, missing method skipped
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# SpreadMonitor
# ---------------------------------------------------------------------------

class TestSpreadMonitorConfig:
    def test_import(self):
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
        sm = SpreadMonitor(SpreadMonitorConfig(min_bars=0))
        assert sm is not None

    def test_healthy_under_limit(self):
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
        cfg = SpreadMonitorConfig(min_bars=0, zscore_control_limit=3.5)
        sm = SpreadMonitor(cfg)
        report = sm.update(spread=0.01, zscore=1.2, half_life=24.0)
        assert report.healthy is True
        assert len(report.alerts) == 0

    def test_zscore_drift_alert(self):
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig, AlertType
        cfg = SpreadMonitorConfig(min_bars=0, zscore_control_limit=3.0)
        sm = SpreadMonitor(cfg)
        report = sm.update(spread=0.05, zscore=3.8, half_life=20.0)
        types = [a.alert_type for a in report.alerts]
        assert AlertType.SPREAD_DRIFT in types
        assert report.healthy is False

    def test_halflife_slow_alert(self):
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig, AlertType
        cfg = SpreadMonitorConfig(min_bars=0, max_half_life_hours=48.0)
        sm = SpreadMonitor(cfg)
        report = sm.update(spread=0.0, zscore=1.0, half_life=100.0)
        types = [a.alert_type for a in report.alerts]
        assert AlertType.HALFLIFE_SLOW in types

    def test_kalman_divergence_alert(self):
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig, AlertType
        cfg = SpreadMonitorConfig(min_bars=0, kalman_p_divergence=0.3)
        sm = SpreadMonitor(cfg)
        report = sm.update(spread=0.0, zscore=0.5, half_life=24.0, kalman_p_diag=0.8)
        types = [a.alert_type for a in report.alerts]
        assert AlertType.KALMAN_DIVERGENCE in types

    def test_cointegration_break_after_consecutive_extreme(self):
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig, AlertType
        cfg = SpreadMonitorConfig(
            min_bars=0,
            zscore_control_limit=2.0,
            cointegration_break_bars=3,
        )
        sm = SpreadMonitor(cfg)
        for _ in range(3):
            sm.update(spread=0.1, zscore=3.0, half_life=24.0)
        report = sm.update(spread=0.1, zscore=3.0, half_life=24.0)
        types = [a.alert_type for a in report.alerts]
        assert AlertType.COINTEGRATION_BREAK in types

    def test_warm_up_suppresses_alerts(self):
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
        cfg = SpreadMonitorConfig(min_bars=10, zscore_control_limit=1.0)
        sm = SpreadMonitor(cfg)
        report = sm.update(spread=100.0, zscore=9.9, half_life=200.0)
        assert report.healthy is True

    def test_reset_clears_state(self):
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
        cfg = SpreadMonitorConfig(min_bars=0)
        sm = SpreadMonitor(cfg)
        sm.update(spread=1.0, zscore=4.0, half_life=24.0)
        sm.reset()
        assert sm.bar_count == 0
        assert sm._consecutive_extreme == 0

    def test_alert_callback_called(self):
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
        cfg = SpreadMonitorConfig(min_bars=0, zscore_control_limit=2.0)
        sm = SpreadMonitor(cfg)
        received = []
        sm.on_alert(lambda alert: received.append(alert))
        sm.update(spread=0.1, zscore=3.5, half_life=24.0)
        assert len(received) > 0

    def test_summary_healthy(self):
        from core.spread_monitor import SpreadHealthReport
        r = SpreadHealthReport(healthy=True, zscore=1.2, half_life=24.0)
        assert "healthy" in r.summary.lower()

    def test_summary_unhealthy(self):
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
        cfg = SpreadMonitorConfig(min_bars=0, zscore_control_limit=1.0)
        sm = SpreadMonitor(cfg)
        r = sm.update(spread=0.1, zscore=5.0, half_life=24.0)
        assert "UNHEALTHY" in r.summary


# ---------------------------------------------------------------------------
# RegimeFilter
# ---------------------------------------------------------------------------

class TestRegimeFilterImport:
    def test_import(self):
        from strategy.regime_filter import RegimeFilter, GateResult
        rf = RegimeFilter()
        assert rf is not None

    def test_no_components_allows_entry(self):
        from strategy.regime_filter import RegimeFilter
        rf = RegimeFilter()
        gate = rf.check(ltf_zscore=2.0)
        assert gate.allowed is True
        assert gate.size_multiplier == 1.0


class TestRegimeFilterBlocking:
    def test_circuit_breaker_blocks(self):
        from strategy.regime_filter import RegimeFilter
        cb = MagicMock()
        cb.is_open = False
        rf = RegimeFilter(circuit_breaker=cb)
        gate = rf.check(ltf_zscore=2.0)
        assert gate.allowed is False
        assert "circuit_breaker" in gate.blocked_by

    def test_vol_regime_extreme_blocks(self):
        from strategy.regime_filter import RegimeFilter
        vr = MagicMock()
        vr.size_multiplier = 0.0
        vr.entry_allowed = False
        vr.current_regime.value = "EXTREME"
        rf = RegimeFilter(vol_regime=vr)
        gate = rf.check(ltf_zscore=2.0)
        assert gate.allowed is False
        assert gate.size_multiplier == 0.0

    def test_mtf_misaligned_blocks(self):
        from strategy.regime_filter import RegimeFilter
        mtf = MagicMock()
        mtf.confirm.return_value = False
        rf = RegimeFilter(mtf=mtf)
        gate = rf.check(ltf_zscore=2.0, htf_zscore=-1.5)
        assert gate.allowed is False
        assert "mtf_misaligned" in gate.blocked_by

    def test_spread_unhealthy_blocks(self):
        from strategy.regime_filter import RegimeFilter
        rf = RegimeFilter()
        report = MagicMock()
        report.healthy = False
        report.alerts = []
        gate = rf.check(ltf_zscore=2.0, spread_report=report)
        assert gate.allowed is False
        assert any("spread_unhealthy" in b for b in gate.blocked_by)

    def test_all_pass_allows_with_vol_multiplier(self):
        from strategy.regime_filter import RegimeFilter
        cb = MagicMock()
        cb.is_open = True
        vr = MagicMock()
        vr.size_multiplier = 0.6
        vr.entry_allowed = True
        vr.current_regime.value = "HIGH"
        mtf = MagicMock()
        mtf.confirm.return_value = True
        rf = RegimeFilter(circuit_breaker=cb, vol_regime=vr, mtf=mtf)
        gate = rf.check(ltf_zscore=2.0, htf_zscore=1.8)
        assert gate.allowed is True
        assert gate.size_multiplier == pytest.approx(0.6)

    def test_entry_allowed_property(self):
        from strategy.regime_filter import RegimeFilter
        cb = MagicMock()
        cb.is_open = True
        vr = MagicMock()
        vr.entry_allowed = True
        rf = RegimeFilter(circuit_breaker=cb, vol_regime=vr)
        assert rf.entry_allowed is True

    def test_record_trade_forwards_to_cb(self):
        from strategy.regime_filter import RegimeFilter
        cb = MagicMock()
        cb.is_open = True
        rf = RegimeFilter(circuit_breaker=cb)
        rf.record_trade(pnl=-100.0)
        cb.record_trade.assert_called_once_with(-100.0)

    def test_gate_result_summary_blocked(self):
        from strategy.regime_filter import GateResult
        g = GateResult(allowed=False, size_multiplier=0.0, blocked_by=["circuit_breaker"])
        assert "BLOCKED" in g.summary

    def test_gate_result_summary_allowed(self):
        from strategy.regime_filter import GateResult
        g = GateResult(allowed=True, size_multiplier=1.0)
        assert "ALLOWED" in g.summary


# ---------------------------------------------------------------------------
# NotifierBus
# ---------------------------------------------------------------------------

class TestNotifierBusImport:
    def test_import(self):
        from notifications.notifier_bus import NotifierBus
        bus = NotifierBus()
        assert bus is not None

    def test_register(self):
        from notifications.notifier_bus import NotifierBus
        bus = NotifierBus()
        n = AsyncMock()
        bus.register("slack", n)
        assert "slack" in bus._notifiers


class TestNotifierBusFanOut:
    @pytest.mark.asyncio
    async def test_fan_out_calls_all_notifiers(self):
        from notifications.notifier_bus import NotifierBus
        bus = NotifierBus()
        n1 = AsyncMock(spec=["send_alert"])
        n2 = AsyncMock(spec=["send_alert"])
        bus.register("slack", n1)
        bus.register("telegram", n2)
        await bus.send_alert("test", level="warning")
        n1.send_alert.assert_called_once()
        n2.send_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_disabled_notifier_skipped(self):
        from notifications.notifier_bus import NotifierBus
        bus = NotifierBus()
        n = AsyncMock(spec=["send_alert"])
        bus.register("slack", n)
        bus.disable("slack")
        await bus.send_alert("test")
        n.send_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_fail_silent_does_not_raise(self):
        from notifications.notifier_bus import NotifierBus
        bus = NotifierBus(fail_silent=True)
        n = AsyncMock(spec=["send_alert"])
        n.send_alert.side_effect = Exception("network error")
        bus.register("slack", n)
        await bus.send_alert("test")  # should not raise

    @pytest.mark.asyncio
    async def test_missing_method_skipped(self):
        from notifications.notifier_bus import NotifierBus
        bus = NotifierBus()
        n = AsyncMock(spec=["send_entry_signal"])  # no send_daily_summary
        bus.register("partial", n)
        await bus.send_daily_summary(trades=5, total_pnl=100.0, win_rate=0.6)
        # no error expected

    @pytest.mark.asyncio
    async def test_entry_signal_fan_out(self):
        from notifications.notifier_bus import NotifierBus
        bus = NotifierBus()
        n = AsyncMock(spec=["send_entry_signal"])
        bus.register("slack", n)
        await bus.send_entry_signal("BTCUSDT", "LONG", 2.3)
        n.send_entry_signal.assert_called_once_with(
            symbol="BTCUSDT", side="LONG", zscore=2.3,
            confidence=0.0, venue="",
        )

    def test_active_notifiers_list(self):
        from notifications.notifier_bus import NotifierBus
        bus = NotifierBus()
        bus.register("slack", AsyncMock())
        bus.register("telegram", AsyncMock())
        bus.disable("telegram")
        assert bus.active_notifiers == ["slack"]
