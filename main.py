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
    DASHBOARD_API_URL, DASHBOARD_FRONTEND_URL
    See BybitLiveRunnerConfig.from_env() for full list.

Flow:
    1. Parse CLI args
    2. Load BybitLiveRunnerConfig from env (+ CLI overrides)
    3. Build NotifierBus (Telegram + Slack)
    4. Build BybitWsFeed
    5. Wire RiskDashboardEngine into StateBus + api/risk singleton
    6. WorkflowOrchestrator.from_runner_cfg(cfg, notifier_bus, ws_feed)
       Faza 0: HealthCheck -> halt on critical failure
       Faza 1: PositionScanner
       Faza 2: ResumeManager
       Faza 3: AdoptionEngine
       Faza 4: ProfitOptimizer
       Faza 6: Dashboard health ping
    7. WorkflowOrchestrator.start_runner() -> blocks
       Faza 5: BybitLiveRunner v3.6 (notifier_bus + ws_feed injectate)
               + optimizer loop in background

FIX (2026-07-11):
    main.py transmite acum notifier_bus si ws_feed la from_runner_cfg()
    astfel incat orchestratorul le injecteaza in BybitLiveRunner prin
    _build_runner() -> BybitLiveRunner(cfg=, notifier_bus=, ws_feed=)
    Evita reconstructia duplicata de NotifierBus si WsFeed in runner.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

from loguru import logger


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="QuantLuna — Pair Trading Bot",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Force dry-run mode (no real orders). Overrides DRY_RUN env var.",
    )
    parser.add_argument(
        "--pair",
        type=str,
        default=None,
        metavar="Y/X",
        help="Symbol pair, e.g. BTCUSDT/ETHUSDT",
    )
    parser.add_argument(
        "--interval",
        type=str,
        default=None,
        metavar="MIN",
        help="Bar interval in minutes, e.g. 5",
    )
    parser.add_argument(
        "--skip-health",
        action="store_true",
        default=False,
        help="Skip pre-flight HealthCheck (useful in dry/CI mode)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level",
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
        "logs/quantluna_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",
        retention="14 days",
        compression="gz",
        enqueue=True,
    )


def _wire_dashboard_engine(cfg, state_bus) -> None:
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
        except Exception:
            pass
        logger.info(
            "main: RiskDashboardEngine wired (capital={:.0f} USDT)",
            initial_capital,
        )
    except Exception as exc:
        logger.warning("main: RiskDashboardEngine wiring failed: {}", exc)


async def _build_notifier_bus(cfg):
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
                logger.warning("main: Telegram notifier failed: {}", exc)
        if cfg.slack_webhook_url:
            try:
                from notifications.slack_notifier import SlackNotifier, SlackConfig
                bus.register("slack", SlackNotifier(
                    SlackConfig(webhook_url=cfg.slack_webhook_url)
                ))
                logger.info("main: Slack notifier registered")
            except Exception as exc:
                logger.warning("main: Slack notifier failed: {}", exc)
        return bus
    except Exception as exc:
        logger.warning("main: NotifierBus unavailable: {}", exc)
        return None


async def _build_ws_feed(cfg):
    try:
        from execution.bybit_ws_feed import BybitWsFeed, BybitWsFeedConfig
        feed_cfg = BybitWsFeedConfig(
            symbol_y=cfg.symbol_y,
            symbol_x=cfg.symbol_x,
            interval=cfg.interval,
            testnet=os.getenv("BYBIT_TESTNET", "false").lower() == "true",
        )
        feed = BybitWsFeed(feed_cfg)
        logger.info(
            "main: BybitWsFeed built ({}/{} {}m)",
            cfg.symbol_y, cfg.symbol_x, cfg.interval,
        )
        return feed
    except Exception as exc:
        logger.warning("main: BybitWsFeed build failed: {}", exc)
        return None


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
) -> None:
    def _handle_signal(sig: signal.Signals) -> None:
        logger.warning("main: received {} — initiating graceful shutdown", sig.name)
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda s, _: _handle_signal(signal.Signals(s)))


async def main() -> int:
    args = _parse_args()

    os.makedirs("logs", exist_ok=True)
    os.makedirs("state", exist_ok=True)
    _configure_logging(args.log_level)

    # 1. Load config
    from execution.bybit_live_runner import BybitLiveRunnerConfig
    cfg = BybitLiveRunnerConfig.from_env()

    if args.dry_run:
        cfg.dry_run = True
    if args.pair:
        parts = args.pair.split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            cfg.symbol_y, cfg.symbol_x = parts[0].upper(), parts[1].upper()
        else:
            logger.error(
                "Invalid --pair format: {!r} (expected Y/X, both non-empty)",
                args.pair,
            )
            return 1
    if args.interval:
        cfg.interval = args.interval

    logger.info(
        "QuantLuna starting — {}/{} interval={}m dry_run={}",
        cfg.symbol_y, cfg.symbol_x, cfg.interval, cfg.dry_run,
    )

    # 2. SIGTERM / SIGINT
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    _install_signal_handlers(loop, shutdown_event)

    # 3. Build shared components — construite O SINGURA DATA si injectate
    notifier_bus = await _build_notifier_bus(cfg)
    ws_feed      = await _build_ws_feed(cfg)

    # 4. Wire RiskDashboardEngine
    from core.state_bus import bus as state_bus
    _wire_dashboard_engine(cfg, state_bus)

    # 5. Build orchestrator — transmite notifier_bus si ws_feed
    #    Orchestratorul le va injecta in BybitLiveRunner via _build_runner()
    #    Evita constructia duplicata in runner._build_components()
    from execution.workflow_orchestrator import WorkflowOrchestrator
    orch = WorkflowOrchestrator.from_runner_cfg(
        cfg=cfg,
        notifier_bus=notifier_bus,
        ws_feed=ws_feed,
        skip_health_check=args.skip_health or cfg.dry_run,
        dashboard_api_url=os.getenv("DASHBOARD_API_URL", ""),
        dashboard_frontend_url=os.getenv("DASHBOARD_FRONTEND_URL", ""),
    )

    # 6. Run startup workflow (Faze 0-4 + 6)
    ctx = await orch.run_startup_workflow()

    if ctx.should_halt:
        logger.error("Startup HALT: {}", ctx.halt_reason)
        if notifier_bus:
            try:
                await notifier_bus.send_alert(
                    f"\u274c QuantLuna HALT: {ctx.halt_reason}",
                    level="error",
                )
            except Exception:
                pass
        return 1

    # 7. Start runner (Faza 5) — blocks until stop or shutdown signal
    runner_task = asyncio.create_task(orch.start_runner(ctx))
    await asyncio.wait(
        [runner_task, asyncio.create_task(shutdown_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    if shutdown_event.is_set() and not runner_task.done():
        logger.info("main: shutdown signal received — cancelling runner")
        runner_task.cancel()
        try:
            await runner_task
        except asyncio.CancelledError:
            logger.info("main: runner cancelled cleanly")

    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
