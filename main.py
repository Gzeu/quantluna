"""
QuantLuna — main.py

Usage:
    # Live mode (reads all config from env vars):
    python main.py

    # Dry run override:
    python main.py --dry-run

    # Custom pair + interval:
    python main.py --pair BTCUSDT/ETHUSDT --interval 5

    # Skip pre-flight health check (useful in CI/testing):
    python main.py --dry-run --skip-health

Env vars (all optional, have defaults):
    BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_TESTNET
    SYMBOL_Y, SYMBOL_X, INTERVAL
    DRY_RUN, ENTRY_ZSCORE, EXIT_ZSCORE, BASE_QTY
    WARMUP_BARS, KALMAN_WINDOW
    MAX_CONSEC_LOSSES, MAX_DRAWDOWN_PCT, COOLDOWN_SECONDS
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SLACK_WEBHOOK_URL
    HEALTH_PORT, FUNDING_GATE_ENABLED, PNL_RECONCILER_ENABLED
    MARKET_TRADE_ENABLED, CHECKPOINT_PATH, INITIAL_CAPITAL
    LOG_DIR  (default: logs)   — directory for rotating log files
    STATE_DIR (default: state) — directory for checkpoint/state files
    RUNNER_TIMEOUT_SECONDS (default: 7200) — hard timeout before force-cancel
    See BybitLiveRunnerConfig.from_env() for full list.

Flow:
    1. Parse CLI args
    2. Load BybitLiveRunnerConfig from env (+ CLI overrides)
    3. Validate config (ConfigValidator + __post_init__ guards)
    4. Build NotifierBus (Telegram + Slack)
    5. Build BybitWsFeed
    6. Wire RiskDashboardEngine into StateBus + api/risk singleton
    7. WorkflowOrchestrator.run_startup_workflow()
       Faza 0: HealthCheck -> halt on critical failure
       Faza 1: PositionScanner
       Faza 2: ResumeManager
       Faza 3: AdoptionEngine
       Faza 4: ProfitOptimizer
    8. WorkflowOrchestrator.start_runner() -> blocks
       Faza 5: BybitLiveRunner + optimizer loop in background
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

# Load .env before configuration reads env vars
try:
    from dotenv import load_dotenv
    _env_dir = Path(__file__).resolve().parent
    _env_file = _env_dir / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except ImportError:
    pass

from loguru import logger

# ---------------------------------------------------------------------------
# Directory constants — overridable via env for Docker / custom mounts
# ---------------------------------------------------------------------------
LOG_DIR = os.getenv("LOG_DIR", "logs")
STATE_DIR = os.getenv("STATE_DIR", "state")

# Hard timeout (seconds) for the runner task before force-cancel.
# Default 2h — set RUNNER_TIMEOUT_SECONDS=0 to disable (infinite, not recommended).
_RUNNER_TIMEOUT: int = int(os.getenv("RUNNER_TIMEOUT_SECONDS", "7200"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="QuantLuna — Pair Trading Bot",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", default=None)
    parser.add_argument("--pair", type=str, default=None, metavar="Y/X")
    parser.add_argument("--interval", type=str, default=None, metavar="MIN")
    parser.add_argument(
        "--skip-health",
        action="store_true",
        default=False,
        help="Skip pre-flight health check. Use only in CI or isolated tests.",
    )
    parser.add_argument(
        "--force", action="store_true", default=False,
        help="Force singleton lock takeover if another runner is active.",
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def _configure_logging(level: str) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )
    logger.add(
        f"{LOG_DIR}/quantluna_{{time:YYYY-MM-DD}}.log",
        level="DEBUG",
        rotation="00:00",
        retention="14 days",
        compression="gz",
        enqueue=True,
    )


def _validate_config(cfg, mode: str) -> None:
    """Run ConfigValidator and log results. Fatal on errors in live mode.

    Notes:
        - __post_init__ in BybitLiveRunnerConfig already raises ValueError
          for hard out-of-range values (entry_zscore=0, warmup_bars<20, etc.).
        - This function adds env-level checks (API keys, notifications) and
          trading-param warnings that are advisory rather than fatal in paper mode.
    """
    try:
        from core.config_validator import ConfigValidator
        validator = ConfigValidator(
            exchange="bybit",
            mode=mode,
            capital_usdt=cfg.initial_capital,
            max_drawdown_pct=cfg.max_drawdown_pct / 100.0,  # pct -> fraction
        )
        result = validator.validate()
        trading_result = validator.validate_trading_params(
            entry_zscore=cfg.entry_zscore,
            exit_zscore=cfg.exit_zscore,
            base_qty=cfg.base_qty,
            warmup_bars=cfg.warmup_bars,
            kalman_window=cfg.kalman_window,
            max_drawdown_pct=cfg.max_drawdown_pct,
        )

        all_errors   = result.errors   + trading_result.errors
        all_warnings = result.warnings + trading_result.warnings

        for w in all_warnings:
            logger.warning("Config warning: {}", w)

        if all_errors:
            for e in all_errors:
                logger.error("Config error: {}", e)
            if mode == "live":
                logger.error(
                    "main: {} config error(s) detected in live mode — aborting. "
                    "Fix env vars and restart.",
                    len(all_errors),
                )
                sys.exit(1)
            else:
                logger.warning(
                    "main: {} config error(s) detected in {} mode — continuing "
                    "(dry_run=True protects from real orders).",
                    len(all_errors), mode,
                )
        else:
            logger.info("main: config validation passed ({} warnings)", len(all_warnings))

    except ImportError as exc:
        logger.warning("main: ConfigValidator unavailable — skipping soft validation: {}", exc)


def _wire_dashboard_engine(cfg, state_bus) -> None:
    """Inject RiskDashboardEngine into StateBus and api/risk singleton.

    Failures are logged as WARNING (non-fatal) since the bot can operate
    without the dashboard, but operators MUST see the warning.
    """
    try:
        from risk.dashboard_engine import RiskDashboardEngine
        initial_capital = float(
            getattr(cfg, "initial_capital", None)
            or os.getenv("INITIAL_CAPITAL", "10000")
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


async def _build_notifier_bus(cfg):
    """Build and register notification channels.

    All failures are WARNING-logged; the bot can run without notifications.
    """
    try:
        from notifications.notifier_bus import NotifierBus
        bus = NotifierBus(fail_silent=True)
        if cfg.telegram_bot_token and cfg.telegram_chat_id:
            try:
                from notifications.telegram import TelegramNotifier
                bus.register("telegram", TelegramNotifier(
                    token=cfg.telegram_bot_token,
                    chat_id=cfg.telegram_chat_id,
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


async def _build_ws_feed(cfg):
    """Build BybitWsFeed. Failure is WARNING-logged; runner may fall back to REST polling."""
    try:
        from execution.bybit_ws_feed import BybitWsFeed, BybitWsFeedConfig
        feed_cfg = BybitWsFeedConfig(
            symbol_y=cfg.symbol_y,
            symbol_x=cfg.symbol_x,
            interval=str(cfg.interval),
            testnet=os.getenv("BYBIT_TESTNET", "false").lower() == "true",
        )
        feed = BybitWsFeed.from_config(feed_cfg)
        logger.info(
            "main: BybitWsFeed built ({}/{} {}m)",
            cfg.symbol_y, cfg.symbol_x, cfg.interval,
        )
        return feed
    except Exception as exc:
        logger.warning(
            "main: BybitWsFeed build failed — runner will use REST fallback: {}", exc
        )
        return None


def _install_signal_handlers(loop, shutdown_event) -> None:
    def _handle_signal(sig) -> None:
        logger.warning("main: received {} — initiating graceful shutdown", sig.name)
        shutdown_event.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda s, _: _handle_signal(signal.Signals(s)))


def _inject_api_state(orch, ctx, notifier_bus) -> None:
    """Inject orchestrator state into API routers so dashboard gets live data."""
    try:
        # Build sizing engine (same logic as api/main.py lifespan)
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


async def main() -> int:
    args = _parse_args()
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)
    _configure_logging(args.log_level)

    from execution.bybit_live_runner import BybitLiveRunnerConfig
    from core.position_store import PositionStore

    # Initialize position store for persistence
    position_store = PositionStore()

    try:
        cfg = BybitLiveRunnerConfig.from_env()
    except ValueError as exc:
        logger.error("Config validation failed at startup:\n{}", exc)
        return 1

    if args.dry_run:
        cfg.dry_run = True
    if args.pair:
        parts = args.pair.split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            cfg.symbol_y, cfg.symbol_x = parts[0].upper(), parts[1].upper()
        else:
            logger.error("Invalid --pair format: {!r} (expected Y/X)", args.pair)
            return 1
    if args.interval:
        cfg.interval = args.interval

    # Auto-detect initial capital from Bybit unless manually set
    cfg.initial_capital = await BybitLiveRunnerConfig.resolve_initial_capital(cfg)

    logger.info(
        "QuantLuna starting — {}/{} interval={}m dry_run={} log_dir={} state_dir={}",
        cfg.symbol_y, cfg.symbol_x, cfg.interval, cfg.dry_run, LOG_DIR, STATE_DIR,
    )

    mode = "live" if not cfg.dry_run else "paper"
    _validate_config(cfg, mode)

    # ── S48 P0: Singleton lock — prevent duplicate runners ────────────
    from core.singleton_lock import SingletonLock
    singleton_lock = SingletonLock("state/quantluna.lock")
    force_takeover = getattr(args, "force", False)
    if not singleton_lock.acquire(app_version="0.33.0", mode=mode, force=force_takeover):
        logger.error("Another QuantLuna runner is already active. Exiting.")
        return 1

    # ── S48 P0: Centralized Bybit traffic controller ──────────────────
    from core.bybit_traffic_controller import (
        BybitTrafficController, TrafficConfig, get_traffic_controller, set_traffic_controller,
    )
    traffic_cfg = TrafficConfig()
    traffic_ctrl = BybitTrafficController(traffic_cfg)
    set_traffic_controller(traffic_ctrl)
    logger.info(
        "main: Traffic controller ready ({} RPM, {} concurrent, circuit={})",
        traffic_cfg.max_rest_rpm, traffic_cfg.max_concurrent,
        traffic_cfg.circuit_breaker_enabled,
    )

    # Inject traffic controller into diagnostics API
    from api.diagnostics import set_traffic_state
    set_traffic_state({"controller": traffic_ctrl, "lock": singleton_lock})

    # ── S48 P0: Account snapshot service ─────────────────────────────
    from core.account_snapshot import AccountSyncService
    account_sync = AccountSyncService(exchange=None, traffic_ctrl=traffic_ctrl)
    from api.account import set_account_state
    set_account_state({
        "sync_service": account_sync,
        "traffic_ctrl": traffic_ctrl,
        "orchestrator": None,  # filled after orchestrator build
    })
    logger.info("main: AccountSyncService initialized")

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    _install_signal_handlers(loop, shutdown_event)

    notifier_bus = await _build_notifier_bus(cfg)
    ws_feed      = await _build_ws_feed(cfg)

    from core.state_bus import bus as state_bus
    _wire_dashboard_engine(cfg, state_bus)

    # Canonical import: execution.workflow_orchestrator is the startup-workflow
    # orchestrator (5 phases: HealthCheck, PositionScanner, ResumeManager,
    # AdoptionEngine, ProfitOptimizer -> BybitLiveRunner).
    # core/workflow_orchestrator.py is a deprecated shim — do NOT import from there.
    from execution.workflow_orchestrator import WorkflowOrchestrator
    orch = WorkflowOrchestrator.from_runner_cfg(
        cfg=cfg,
        notifier_bus=notifier_bus,
        ws_feed=ws_feed,
        skip_health_check=args.skip_health,
        position_store=position_store,
    )

    ctx = await orch.run_startup_workflow()

    if ctx.should_halt:
        logger.error("Startup HALT: {}", ctx.halt_reason)
        if notifier_bus:
            try:
                await notifier_bus.send_alert(
                    f"\u274c QuantLuna HALT: {ctx.halt_reason}",
                    level="error",
                )
            except Exception as exc:
                logger.warning("main: failed to send HALT notification: {}", exc)
        return 1

    runner_task = asyncio.create_task(orch.start_runner(ctx))

    # ── S48: Start API server alongside the runner ────────────────────
    api_port = int(os.getenv("API_PORT", "8000"))
    api_host = os.getenv("API_HOST", "0.0.0.0")
    api_task = None
    try:
        # Inject orchestrator state into API routers (same as api/main.py lifespan)
        _inject_api_state(orch, ctx, notifier_bus)

        import uvicorn
        from api.main import app

        api_config = uvicorn.Config(
            app, host=api_host, port=api_port,
            log_level="info", lifespan="off",  # we manage lifecycle ourselves
        )
        api_server = uvicorn.Server(api_config)
        api_task = asyncio.create_task(api_server.serve(), name="api_server")
        logger.info("main: API server starting on {}:{}", api_host, api_port)
    except Exception as exc:
        logger.warning("main: Could not start API server: {}", exc)

    shutdown_task = asyncio.create_task(shutdown_event.wait())

    tasks = [runner_task, shutdown_task]
    if api_task is not None:
        tasks.append(api_task)

    timeout = _RUNNER_TIMEOUT if _RUNNER_TIMEOUT > 0 else None
    done, _ = await asyncio.wait(
        tasks,
        return_when=asyncio.FIRST_COMPLETED,
        timeout=timeout,
    )

    if not done:
        logger.error(
            "main: runner hard timeout reached ({:.0f}s) — forcing shutdown. "
            "Check for deadlocks or increase RUNNER_TIMEOUT_SECONDS.",
            _RUNNER_TIMEOUT,
        )
        shutdown_event.set()

    if not shutdown_task.done():
        shutdown_task.cancel()

    if api_task is not None and not api_task.done():
        api_task.cancel()

    if not runner_task.done():
        logger.info("main: cancelling runner task...")
        runner_task.cancel()
        try:
            await runner_task
        except asyncio.CancelledError:
            logger.info("main: runner cancelled cleanly")

    # Release singleton lock
    singleton_lock.release()
    logger.info("main: shutdown complete")
    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
