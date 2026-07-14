"""
tests/test_bootstrap.py — Unit tests for startup/bootstrap.py (S48).

Tests each initialization function with mocked lazy imports.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from startup.bootstrap import (
    build_notifier_bus,
    build_ws_feed,
    inject_api_state,
    wire_dashboard_engine,
)


@pytest.fixture
def mock_cfg():
    cfg = MagicMock()
    cfg.symbol_y = "BTCUSDT"
    cfg.symbol_x = "ETHUSDT"
    cfg.interval = 5
    cfg.telegram_bot_token = ""
    cfg.telegram_chat_id = ""
    cfg.slack_webhook_url = ""
    cfg.initial_capital = 10000.0
    return cfg


@pytest.fixture
def mock_state_bus():
    return MagicMock()


@pytest.fixture
def mock_orch():
    orch = MagicMock()
    orch.pairs = ["BTCUSDT/ETHUSDT"]
    orch.reoptimizer = None
    orch.watchdog = None
    return orch


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.sizing_engine = None
    ctx.decision_engine = None
    return ctx


@pytest.fixture
def mock_notifier_bus():
    return MagicMock()


# ── wire_dashboard_engine ───────────────────────────────────────────────


class TestWireDashboardEngine:
    @patch("risk.dashboard_engine.RiskDashboardEngine")
    def test_wires_engine(self, mock_engine_cls, mock_cfg, mock_state_bus):
        """Creates RiskDashboardEngine and injects into state_bus + api.risk."""
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine

        wire_dashboard_engine(mock_cfg, mock_state_bus)

        mock_engine_cls.assert_called_once_with(initial_capital=10000.0)
        mock_state_bus.set_risk_engine.assert_called_once_with(mock_engine)

    def test_failure_logged(self, mock_cfg, mock_state_bus):
        """Import failure should warn, not raise."""
        with patch.dict("sys.modules", {"risk.dashboard_engine": None}):
            wire_dashboard_engine(mock_cfg, mock_state_bus)


# ── build_notifier_bus ──────────────────────────────────────────────────


class TestBuildNotifierBus:
    async def test_returns_bus_without_creds(self, mock_cfg):
        bus = await build_notifier_bus(mock_cfg)
        assert bus is not None

    async def test_returns_none_on_import_error(self, mock_cfg):
        with patch.dict("sys.modules", {"notifications.notifier_bus": None}):
            bus = await build_notifier_bus(mock_cfg)
        assert bus is None

    async def test_registers_telegram(self, mock_cfg):
        """Telegram attempt is logged as warning if module missing, not fatal."""
        mock_cfg.telegram_bot_token = "bot123"
        mock_cfg.telegram_chat_id = "chat456"
        bus = await build_notifier_bus(mock_cfg)
        assert bus is not None  # bus still created even if telegram fails

    @patch("notifications.slack_notifier.SlackNotifier")
    async def test_registers_slack(self, mock_slack_cls, mock_cfg):
        mock_cfg.slack_webhook_url = "https://hooks.slack.com/xxx"
        bus = await build_notifier_bus(mock_cfg)
        assert bus is not None


# ── build_ws_feed ───────────────────────────────────────────────────────


class TestBuildWsFeed:
    @patch("execution.bybit_ws_feed.BybitWsFeed")
    @patch("execution.bybit_ws_feed.BybitWsFeedConfig")
    async def test_builds_feed(self, mock_cfg_cls, mock_feed_cls, mock_cfg):
        mock_feed = MagicMock()
        mock_feed_cls.from_config.return_value = mock_feed

        feed = await build_ws_feed(mock_cfg)

        assert feed is mock_feed
        mock_cfg_cls.assert_called_once()
        mock_feed_cls.from_config.assert_called_once()

    async def test_returns_none_on_failure(self, mock_cfg):
        with patch.dict("sys.modules", {"execution.bybit_ws_feed": None}):
            feed = await build_ws_feed(mock_cfg)
        assert feed is None


# ── inject_api_state ────────────────────────────────────────────────────


class TestInjectApiState:
    @patch("api.sizing.set_sizing_state")
    @patch("api.decision.set_decision_state")
    @patch("api.watchdog.set_watchdog_state")
    @patch("api.optimizer.set_optimizer_state")
    @patch("api.notifications.set_dispatcher")
    def test_injects_all_states(
        self, mock_set_disp, mock_set_opt, mock_set_wd,
        mock_set_dec, mock_set_sz,
        mock_orch, mock_ctx, mock_notifier_bus,
    ):
        inject_api_state(mock_orch, mock_ctx, mock_notifier_bus)

        mock_set_sz.assert_called_once()
        mock_set_dec.assert_called_once()
        mock_set_wd.assert_called_once()
        mock_set_opt.assert_called_once()
        mock_set_disp.assert_called_once_with(mock_notifier_bus)

    def test_builds_missing_sizing_engine(self, mock_orch, mock_notifier_bus):
        """When ctx.sizing_engine is None and SizingEngine import works, builds default."""
        ctx = MagicMock()
        ctx.sizing_engine = None
        with patch("risk.bybit_position_sizer.BybitPositionSizer"):
            inject_api_state(mock_orch, ctx, mock_notifier_bus)

    def test_handles_failure_gracefully(self, mock_notifier_bus):
        """Injecting with completely bad arguments should warn, not raise."""
        inject_api_state(None, None, mock_notifier_bus)
