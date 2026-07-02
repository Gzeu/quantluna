"""
Sprint 17 tests:
  - OrderManager: submit, dry-run, cancel, timeout, summary
  - CircuitBreaker: consecutive losses, drawdown, error rate, manual trip, auto-reset
  - SlackNotifier: disabled, no config, level filtering
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# OrderManager
# ---------------------------------------------------------------------------

class TestOrderManagerConfig:
    def test_import(self):
        from execution.order_manager import OrderManager, OrderManagerConfig
        cfg = OrderManagerConfig(dry_run=True)
        mgr = OrderManager(cfg)
        assert mgr is not None

    def test_register_router(self):
        from execution.order_manager import OrderManager
        mgr = OrderManager()
        mock_router = MagicMock()
        mgr.register_router("bybit", mock_router)
        assert "bybit" in mgr.routers


class TestOrderManagerSubmit:
    @pytest.mark.asyncio
    async def test_dry_run_fills_immediately(self):
        from execution.order_manager import OrderManager, OrderManagerConfig, OrderRequest, OrderStatus
        cfg = OrderManagerConfig(dry_run=True)
        mgr = OrderManager(cfg)
        req = OrderRequest(symbol="BTCUSDT", side="buy", qty=0.01, venue="bybit")
        lid = await mgr.submit(req)
        assert mgr.get_status(lid) == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_no_router_fails_order(self):
        from execution.order_manager import OrderManager, OrderRequest, OrderStatus
        mgr = OrderManager()
        req = OrderRequest(symbol="BTCUSDT", side="buy", qty=0.01, venue="missing_venue")
        lid = await mgr.submit(req)
        assert mgr.get_status(lid) == OrderStatus.FAILED
        assert mgr.get_record(lid).error is not None

    @pytest.mark.asyncio
    async def test_market_order_routes_to_router(self):
        from execution.order_manager import OrderManager, OrderRequest, OrderStatus
        mock_router = AsyncMock()
        mock_router.place_market_order.return_value = {"id": "exch123", "filled": 0.01, "average": 50000.0}
        mgr = OrderManager(routers={"bybit": mock_router})
        req = OrderRequest(symbol="BTCUSDT", side="buy", qty=0.01, venue="bybit")
        lid = await mgr.submit(req)
        assert mgr.get_status(lid) == OrderStatus.FILLED
        mock_router.place_market_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_router_exception_marks_failed(self):
        from execution.order_manager import OrderManager, OrderRequest, OrderStatus
        mock_router = AsyncMock()
        mock_router.place_market_order.side_effect = Exception("connection error")
        mgr = OrderManager(routers={"bybit": mock_router})
        req = OrderRequest(symbol="BTCUSDT", side="buy", qty=0.01, venue="bybit")
        lid = await mgr.submit(req)
        assert mgr.get_status(lid) == OrderStatus.FAILED

    @pytest.mark.asyncio
    async def test_submit_pair_returns_both_ids(self):
        from execution.order_manager import OrderManager, OrderManagerConfig, OrderRequest
        cfg = OrderManagerConfig(dry_run=True)
        mgr = OrderManager(cfg)
        req_y = OrderRequest(symbol="BTCUSDT", side="buy",  qty=0.01, venue="bybit")
        req_x = OrderRequest(symbol="ETHUSDT", side="sell", qty=0.1,  venue="bybit")
        result = await mgr.submit_pair(req_y, req_x)
        assert "leg_y" in result
        assert "leg_x" in result

    @pytest.mark.asyncio
    async def test_cancel_submitted_order(self):
        from execution.order_manager import OrderManager, OrderRequest, OrderStatus
        mock_router = AsyncMock()
        mock_router.place_market_order.return_value = {"id": "exch999"}
        mock_router.cancel_order = AsyncMock(return_value={})
        mgr = OrderManager(routers={"bybit": mock_router})
        req = OrderRequest(symbol="BTCUSDT", side="buy", qty=0.01, venue="bybit", order_type="limit", price=50000.0)
        # Manually put order in SUBMITTED state
        mock_router.place_limit_order = AsyncMock(return_value={"id": "exch999"})
        lid = await mgr.submit(req)
        # Force SUBMITTED status for cancel test
        from execution.order_manager import OrderStatus
        mgr.get_record(lid).status = OrderStatus.SUBMITTED
        mgr.get_record(lid).exchange_id = "exch999"
        cancelled = await mgr.cancel(lid)
        assert cancelled is True
        assert mgr.get_status(lid) == OrderStatus.CANCELLED

    def test_summary_counts(self):
        from execution.order_manager import OrderManager, OrderManagerConfig, OrderRequest, OrderRecord, OrderStatus
        cfg = OrderManagerConfig(dry_run=True)
        mgr = OrderManager(cfg)
        # Inject fake records
        from execution.order_manager import OrderRecord, OrderRequest
        req = OrderRequest(symbol="X", side="buy", qty=1, venue="bybit")
        r1 = OrderRecord(local_id="a", request=req, status=OrderStatus.FILLED)
        r2 = OrderRecord(local_id="b", request=req, status=OrderStatus.FAILED)
        mgr._orders = {"a": r1, "b": r2}
        s = mgr.summary()
        assert s["total"] == 2
        assert s["by_status"]["filled"] == 1


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------

class TestCircuitBreakerConfig:
    def test_import(self):
        from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
        cfg = CircuitBreakerConfig(max_consecutive_losses=3)
        cb = CircuitBreaker(cfg)
        assert cb is not None

    def test_default_is_open(self):
        from risk.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker()
        assert cb.is_open is True
        assert cb.is_tripped is False


class TestCircuitBreakerTrip:
    def test_consecutive_losses_trip(self):
        from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, TripReason
        cfg = CircuitBreakerConfig(max_consecutive_losses=3, cooldown_seconds=0)
        cb = CircuitBreaker(cfg)
        for _ in range(3):
            cb.record_trade(pnl=-10.0)
        assert cb.is_tripped is True
        assert cb._trip_event.reason == TripReason.CONSECUTIVE_LOSSES

    def test_win_resets_consecutive_counter(self):
        from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
        cfg = CircuitBreakerConfig(max_consecutive_losses=5, cooldown_seconds=0)
        cb = CircuitBreaker(cfg)
        for _ in range(4):
            cb.record_trade(pnl=-10.0)
        cb.record_trade(pnl=+20.0)  # win resets counter
        assert cb.is_open is True
        assert cb._consecutive_losses == 0

    def test_drawdown_trip(self):
        from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, TripReason
        cfg = CircuitBreakerConfig(
            max_consecutive_losses=100,
            max_drawdown_pct=-0.05,
            capital_reference=1000.0,
            cooldown_seconds=0,
        )
        cb = CircuitBreaker(cfg)
        cb.record_trade(pnl=-30.0)
        cb.record_trade(pnl=-30.0)  # -60 total = -6% of 1000
        assert cb.is_tripped is True
        assert cb._trip_event.reason == TripReason.DRAWDOWN

    def test_error_rate_trip(self):
        from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, TripReason
        cfg = CircuitBreakerConfig(
            max_error_rate=0.5,
            error_window_trades=6,
            max_consecutive_losses=100,
            cooldown_seconds=0,
        )
        cb = CircuitBreaker(cfg)
        # 4 errors out of 6 = 66% > 50%
        for ok in [False, False, False, True, False, True]:
            cb.record_order_result(ok)
        assert cb.is_tripped is True
        assert cb._trip_event.reason == TripReason.ERROR_RATE

    def test_manual_trip(self):
        from risk.circuit_breaker import CircuitBreaker, TripReason
        cb = CircuitBreaker()
        cb.trip_manual("operator test")
        assert cb.is_tripped is True
        assert cb._trip_event.reason == TripReason.MANUAL

    def test_reset_reopens(self):
        from risk.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker()
        cb.trip_manual()
        assert cb.is_tripped is True
        cb.reset()
        assert cb.is_open is True

    def test_auto_reset_after_cooldown(self):
        from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
        cfg = CircuitBreakerConfig(max_consecutive_losses=1, cooldown_seconds=0.01)
        cb = CircuitBreaker(cfg)
        cb.record_trade(pnl=-1.0)
        assert cb.is_tripped is True
        time.sleep(0.05)  # wait for cooldown
        assert cb.is_open is True  # auto-reset

    def test_status_dict_keys(self):
        from risk.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker()
        s = cb.status()
        expected = {
            "is_open", "tripped", "trip_reason", "trip_detail",
            "consecutive_losses", "rolling_pnl", "error_rate",
            "cooldown_remaining_s", "trip_count",
        }
        assert set(s.keys()) == expected

    def test_full_reset_clears_history(self):
        from risk.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker()
        cb.trip_manual()
        cb.reset()
        cb.trip_manual()
        cb.full_reset()
        assert len(cb._trip_history) == 0
        assert len(cb._pnl_window) == 0


# ---------------------------------------------------------------------------
# SlackNotifier
# ---------------------------------------------------------------------------

class TestSlackNotifierConfig:
    def test_import(self):
        from notifications.slack_notifier import SlackNotifier, SlackConfig
        cfg = SlackConfig(webhook_url="https://hooks.slack.com/test")
        n = SlackNotifier(cfg)
        assert n is not None

    def test_defaults(self):
        from notifications.slack_notifier import SlackConfig
        cfg = SlackConfig()
        assert cfg.enabled is True
        assert cfg.min_level == "info"
        assert cfg.username == "QuantLuna"


class TestSlackNotifierBehaviour:
    @pytest.mark.asyncio
    async def test_disabled_does_not_send(self):
        from notifications.slack_notifier import SlackNotifier, SlackConfig
        cfg = SlackConfig(enabled=False, webhook_url="https://hooks.slack.com/test")
        n = SlackNotifier(cfg)
        # Should complete without error
        await n.send_alert("test", level="critical")

    @pytest.mark.asyncio
    async def test_no_credentials_does_not_raise(self):
        from notifications.slack_notifier import SlackNotifier, SlackConfig
        cfg = SlackConfig(webhook_url="", bot_token="")
        n = SlackNotifier(cfg)
        await n.send_entry_signal("BTCUSDT", "LONG", 2.3)

    @pytest.mark.asyncio
    async def test_below_min_level_skipped(self):
        from notifications.slack_notifier import SlackNotifier, SlackConfig
        cfg = SlackConfig(min_level="critical", webhook_url="https://hooks.slack.com/x")
        n = SlackNotifier(cfg)
        # 'info' is below 'critical' — should be silently skipped
        sent = []
        async def fake_webhook(text):
            sent.append(text)
        n._webhook_send = fake_webhook
        await n.send_entry_signal("BTC", "LONG", 2.0)  # info level
        assert len(sent) == 0

    @pytest.mark.asyncio
    async def test_level_order_critical_passes(self):
        from notifications.slack_notifier import SlackNotifier, SlackConfig
        cfg = SlackConfig(min_level="warning", webhook_url="https://hooks.slack.com/x")
        n = SlackNotifier(cfg)
        sent = []
        async def fake_webhook(text):
            sent.append(text)
        n._webhook_send = fake_webhook
        await n.send_alert("TRIP", "circuit breaker", level="critical")
        assert len(sent) == 1

    def test_below_min_level_helper(self):
        from notifications.slack_notifier import SlackNotifier, SlackConfig
        n = SlackNotifier(SlackConfig(min_level="warning"))
        assert n._below_min_level("info") is True
        assert n._below_min_level("warning") is False
        assert n._below_min_level("critical") is False
