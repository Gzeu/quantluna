"""
Smoke tests Sprint 18 — integration: RegimeFilter + SpreadMonitor + NotifierBus
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


class TestSmokeS18:
    def test_regime_filter_with_spread_monitor_pipeline(self):
        """SpreadMonitor feeds alert → RegimeFilter blocks entry."""
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
        from strategy.regime_filter import RegimeFilter

        cfg = SpreadMonitorConfig(min_bars=0, zscore_control_limit=2.0)
        sm = SpreadMonitor(cfg)

        # Feed 7 extreme bars — triggers COINTEGRATION_BREAK
        for _ in range(7):
            report = sm.update(spread=0.1, zscore=3.5, half_life=24.0)

        rf = RegimeFilter()
        gate = rf.check(ltf_zscore=2.5, spread_report=report)
        assert gate.allowed is False
        assert any("spread_unhealthy" in b for b in gate.blocked_by)

    def test_healthy_spread_allows_entry(self):
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
        from strategy.regime_filter import RegimeFilter

        cfg = SpreadMonitorConfig(min_bars=0, zscore_control_limit=4.0)
        sm = SpreadMonitor(cfg)
        report = sm.update(spread=0.001, zscore=1.5, half_life=30.0)

        rf = RegimeFilter()
        gate = rf.check(ltf_zscore=1.5, spread_report=report)
        assert gate.allowed is True

    @pytest.mark.asyncio
    async def test_notifier_bus_with_slack_like_mock(self):
        from notifications.notifier_bus import NotifierBus

        bus = NotifierBus(fail_silent=True)
        slack = AsyncMock(spec=["send_entry_signal", "send_alert", "send_circuit_breaker_trip"])
        bus.register("slack", slack)

        await bus.send_entry_signal("ETHUSDT", "SHORT", zscore=-2.3)
        await bus.send_alert("Test alert", level="critical")
        await bus.send_circuit_breaker_trip("drawdown", "5% drawdown", cooldown_s=3600)

        assert slack.send_entry_signal.call_count == 1
        assert slack.send_alert.call_count == 1
        assert slack.send_circuit_breaker_trip.call_count == 1

    def test_full_regime_filter_all_components(self):
        """All 4 components active, all green → entry allowed with vol multiplier."""
        from strategy.regime_filter import RegimeFilter
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig

        cb = MagicMock()
        cb.is_open = True

        vr = MagicMock()
        vr.size_multiplier = 0.75
        vr.entry_allowed = True
        vr.current_regime.value = "HIGH"

        mtf = MagicMock()
        mtf.confirm.return_value = True

        cfg = SpreadMonitorConfig(min_bars=0, zscore_control_limit=4.0)
        sm = SpreadMonitor(cfg)
        report = sm.update(spread=0.0, zscore=2.0, half_life=20.0)

        rf = RegimeFilter(circuit_breaker=cb, vol_regime=vr, mtf=mtf)
        gate = rf.check(ltf_zscore=2.0, htf_zscore=1.5, spread_report=report)

        assert gate.allowed is True
        assert gate.size_multiplier == pytest.approx(0.75)
        assert gate.vol_regime == "HIGH"
        assert gate.mtf_confirmed is True
        assert gate.spread_healthy is True
