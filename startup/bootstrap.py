"""
startup/bootstrap.py — Subsystem initialization for QuantLuna (extracted from main.py).

All functions have the same signature and logging pattern as the original
main.py helpers.  Failures are WARNING-logged — the bot can operate without
any single subsystem.

Usage::

    from startup.bootstrap import inject_api_state, wire_dashboard_engine
    inject_api_state(orch, ctx, notifier_bus)
"""
from __future__ import annotations

import os
from typing import Any, Optional

from loguru import logger


def wire_dashboard_engine(cfg, state_bus) -> None:
    """Inject RiskDashboardEngine into StateBus and api/risk singleton.

    Failures are logged as WARNING (non-fatal) since the bot can operate
    without the dashboard, but operators MUST see the warning.
    """
    try:
        from risk.dashboard_engine import RiskDashboardEngine
        raw_cfg = getattr(cfg, "initial_capital", None)
        # Priority: cfg.initial_capital (auto-detected from Bybit) > env var > default
        initial_capital = float(
            (str(raw_cfg) if raw_cfg is not None else None)
            or os.getenv("INITIAL_CAPITAL_USD")
            or os.getenv("INITIAL_CAPITAL")
            or "10000"
        )
        engine = RiskDashboardEngine(initial_capital=initial_capital)
        state_bus.set_risk_engine(engine)
        try:
            from api.risk import set_risk_engine
            set_risk_engine(engine)
        except Exception as exc:
            logger.warning(
                "main: api.risk.set_risk_engine failed — dashboard API will use "
                "StateBus fallback engine. Error: {}", exc
            )
        logger.info(
            "main: RiskDashboardEngine wired (capital={:.0f} USDT)",
            initial_capital,
        )
    except Exception as exc:
        logger.warning(
            "main: RiskDashboardEngine wiring failed — bot will run without "
            "risk dashboard. Error: {}", exc
        )


async def build_notifier_bus(cfg) -> Optional[Any]:
    """Build and register notification channels.

    All failures are WARNING-logged; the bot can run without notifications.
    """
    try:
        from notifications.notifier_bus import NotifierBus
        bus = NotifierBus(fail_silent=True)
        if cfg.telegram_bot_token and cfg.telegram_chat_id:
            try:
                from notifications.telegram_notifier import TelegramNotifier
                bus.register("telegram", TelegramNotifier(
                    token=cfg.telegram_bot_token,
                    chat_ids=[cfg.telegram_chat_id],
                ))
                logger.info("main: Telegram notifier registered")
            except Exception as exc:
                logger.warning("main: Telegram notifier registration failed: {}", exc)
        if cfg.slack_webhook_url:
            try:
                from notifications.slack_notifier import SlackNotifier, SlackConfig
                bus.register("slack", SlackNotifier(
                    SlackConfig(webhook_url=cfg.slack_webhook_url)
                ))
                logger.info("main: Slack notifier registered")
            except Exception as exc:
                logger.warning("main: Slack notifier registration failed: {}", exc)
        return bus
    except Exception as exc:
        logger.warning("main: NotifierBus unavailable — running without notifications: {}", exc)
        return None


async def build_ws_feed(cfg) -> Optional[Any]:
    """Build dual-symbol WS feed (BybitWsBarsAdapter) for pair trading.

    Falls back to single-symbol BybitWsFeed if symbol_x is missing.
    Failure is WARNING-logged; runner may fall back to REST polling.
    """
    try:
        from execution.exchange_factory import get_dual_ws_feed, get_ws_feed

        symbol_y = getattr(cfg, "symbol_y", None) or os.getenv("SYMBOL_Y", "")
        symbol_x = getattr(cfg, "symbol_x", None) or os.getenv("SYMBOL_X", "")
        interval = str(getattr(cfg, "interval", 5))

        if symbol_y and symbol_x:
            feed = get_dual_ws_feed(
                symbol_y=symbol_y,
                symbol_x=symbol_x,
                interval=interval,
            )
            logger.info(
                "main: Dual WS feed built ({}/{} {}m)",
                symbol_y, symbol_x, interval,
            )
        else:
            feed = get_ws_feed(symbol=symbol_y or symbol_x, interval=interval)
            logger.info(
                "main: Single WS feed built ({} {}m)",
                symbol_y or symbol_x, interval,
            )
        return feed
    except Exception as exc:
        logger.warning(
            "main: WS feed build failed — runner will use REST fallback: {}", exc
        )
        return None


def inject_api_state(orch, ctx, notifier_bus) -> None:
    """Inject orchestrator state into API routers so dashboard gets live data."""
    try:
        from risk.bybit_position_sizer import BybitPositionSizer
        from risk.sizing_engine import SizingEngine

        raw_engine = getattr(ctx, "sizing_engine", None)
        if isinstance(raw_engine, SizingEngine):
            sizing_engine = raw_engine
        elif raw_engine is not None:
            try:
                sizing_engine = SizingEngine(sizer=raw_engine)
            except Exception:
                sizing_engine = SizingEngine(sizer=BybitPositionSizer(
                    capital_usdt=float(os.getenv("INITIAL_CAPITAL_USD", "10000")),
                    max_leverage=float(os.getenv("MAX_LEVERAGE", "3.0")),
                    kelly_fraction=os.getenv("KELLY_FRACTION", "half"),
                    max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.25")),
                ))
        else:
            sizing_engine = SizingEngine(sizer=BybitPositionSizer(
                capital_usdt=float(os.getenv("INITIAL_CAPITAL_USD", "10000")),
                max_leverage=float(os.getenv("MAX_LEVERAGE", "3.0")),
                kelly_fraction=os.getenv("KELLY_FRACTION", "half"),
                max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.25")),
            ))

        decision_engine = getattr(ctx, "decision_engine", None)
        watchdog = getattr(orch, "watchdog", None)

        from api.sizing import set_sizing_state
        set_sizing_state({
            "sizing_engine": sizing_engine,
            "decision_engine": decision_engine,
        })

        from api.decision import set_decision_state
        set_decision_state({"decision_engine": decision_engine})

        from api.watchdog import set_watchdog_state
        set_watchdog_state({
            "watchdog": watchdog,
            "dispatcher": notifier_bus,
        })

        from api.optimizer import set_optimizer_state
        set_optimizer_state({
            "running": False,
            "last_run": None,
            "last_results": {},
            "pairs": orch.pairs if hasattr(orch, "pairs") else [],
            "auto_reoptimizer": orch.reoptimizer if hasattr(orch, "reoptimizer") else None,
        })

        from api.notifications import set_dispatcher
        set_dispatcher(notifier_bus)

        logger.info("main: API state injected — dashboard should see live data")
    except Exception as exc:
        logger.warning("main: API state injection failed: {}", exc)
