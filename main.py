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
    WARMUP_BARS, KALMAN_WINDOW, HALF_LIFE_H
    MAX_CONSEC_LOSSES, MAX_DRAWDOWN_PCT, COOLDOWN_SECONDS
    INITIAL_CAPITAL
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SLACK_WEBHOOK_URL
    HEALTH_PORT, FUNDING_GATE_ENABLED, PNL_RECONCILER_ENABLED
    MARKET_TRADE_ENABLED, CHECKPOINT_PATH
    OPTIMIZER_ENABLED, WATCHDOG_ENABLED, ENABLE_SPOT, ENABLE_MARGIN
    LOG_DIR  (default: logs)   — directory for rotating log files
    STATE_DIR (default: state) — directory for checkpoint/state files
    RUNNER_TIMEOUT_SECONDS (default: 7200) — hard timeout before force-cancel
    See execution/runner_config.py BybitLiveRunnerConfig for full list.

Flow (core/WorkflowOrchestrator v2.2):
    1. Parse CLI args
    2. Load BybitLiveRunnerConfig from env (+ CLI overrides) — validated
       immediately in __post_init__ (fix #23)
    3. Build NotifierBus (Telegram + Slack)
    4. Wire RiskDashboardEngine into StateBus + api/risk singleton
    5. WorkflowOrchestrator(runner_cfg, notifier_bus, state_bus)
    6. orch.start_runner()  — blocks; internally runs:
         asyncio.gather(
             BybitLiveRunner.start(),     # trading loop
             AutoReoptimizer.run_loop(),  # WFO weekly
             MonitoringWatchdog.run_loop(),# monitoring 60s
         )
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

from loguru import logger

# ---------------------------------------------------------------------------
# Directory constants — overridable via env for Docker / custom mounts
# ---------------------------------------------------------------------------
LOG_DIR   = os.getenv("LOG_DIR",   "logs")
STATE_DIR = os.getenv("STATE_DIR", "state")

# Hard timeout (seconds) for the runner task before force-cancel.
# Default 2h — set RUNNER_TIMEOUT_SECONDS=0 to disable (infinite, not recommended).
_RUNNER_TIMEOUT: int = int(os.getenv("RUNNER_TIMEOUT_SECONDS", "7200"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="QuantLuna — Pair Trading Bot",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dry-run",   action="store_true", default=None)
    parser.add_argument("--pair",      type=str, default=None, metavar="Y/X")
    parser.add_argument("--interval",  type=str, default=None, metavar="MIN")
    parser.add_argument(
        "--skip-health",
        action="store_true",
        default=False,
        help="Skip pre-flight health check. Use only in CI or isolated tests.",
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


def _wire_dashboard_engine(cfg, state_bus) -> None:
    """Inject RiskDashboardEngine into StateBus and api/risk singleton.

    Failures are logged as WARNING (non-fatal) since the bot can operate
    without the dashboard, but operators MUST see the warning.
    """
    try:
        from risk.dashboard_engine import RiskDashboardEngine
        engine = RiskDashboardEngine(initial_capital=cfg.initial_capital)
        state_bus.set_risk_engine(engine)
        try:
            from api.risk import set_risk_engine
            set_risk_engine(engine)
        except Exception as exc:  # fix #17: was bare `pass`
            logger.warning(
                "main: api.risk.set_risk_engine failed — dashboard API will use "
                "StateBus fallback engine. Error: {}", exc
            )
        logger.info(
            "main: RiskDashboardEngine wired (capital={:.0f} USDT)",
            cfg.initial_capital,
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
        logger.warning(
            "main: NotifierBus unavailable — running without notifications: {}", exc
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


async def main() -> int:
    args = _parse_args()
    os.makedirs(LOG_DIR,   exist_ok=True)  # fix #21: env-overridable LOG_DIR
    os.makedirs(STATE_DIR, exist_ok=True)  # fix #21: env-overridable STATE_DIR
    _configure_logging(args.log_level)

    # ------------------------------------------------------------------
    # [1] Load config — validation happens immediately in __post_init__
    #     (fix #23: invalid env vars raise ValueError before touching exchange)
    # ------------------------------------------------------------------
    from execution.runner_config import BybitLiveRunnerConfig
    cfg = BybitLiveRunnerConfig.from_env()

    # CLI overrides (applied after env load; re-validate not needed for
    # these fields since they don't affect z-score or model params)
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
        try:
            cfg.interval = int(args.interval)
        except ValueError:
            logger.error("Invalid --interval: {!r} (must be integer)", args.interval)
            return 1
    # --skip-health wires into cfg so WorkflowOrchestrator can read it
    # fix #18: dry_run alone no longer skips health check
    if args.skip_health:
        cfg.skip_health_check = True  # noqa: read by core/WFOrch if present

    logger.info(
        "QuantLuna starting — {} dry_run={} log_dir={} state_dir={}",
        cfg.summary(), cfg.dry_run, LOG_DIR, STATE_DIR,
    )

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    _install_signal_handlers(loop, shutdown_event)

    # ------------------------------------------------------------------
    # [3] Build NotifierBus
    # ------------------------------------------------------------------
    notifier_bus = await _build_notifier_bus(cfg)

    # ------------------------------------------------------------------
    # [4] Wire RiskDashboardEngine → StateBus + api/risk singleton
    # ------------------------------------------------------------------
    from core.state_bus import bus as state_bus
    _wire_dashboard_engine(cfg, state_bus)

    # ------------------------------------------------------------------
    # [5] Build WorkflowOrchestrator (core/ v2.2 API)
    #
    #   core/WorkflowOrchestrator.__init__(runner_cfg, notifier_bus, state_bus)
    #   No from_runner_cfg(), no ws_feed injection, no skip_health param.
    #   WsFeed is built internally by BybitLiveRunner.from_env(cfg).
    # ------------------------------------------------------------------
    from core.workflow_orchestrator import WorkflowOrchestrator
    orch = WorkflowOrchestrator(
        runner_cfg=cfg,
        notifier_bus=notifier_bus,
        state_bus=state_bus,
    )

    # ------------------------------------------------------------------
    # [6] start_runner() — no ctx parameter, no run_startup_workflow().
    #   Internally builds context, registers services, runs gather():
    #     BybitLiveRunner.start() + AutoReoptimizer + MonitoringWatchdog
    # ------------------------------------------------------------------
    runner_task   = asyncio.create_task(orch.start_runner(),   name="orchestrator")
    shutdown_task = asyncio.create_task(shutdown_event.wait(), name="shutdown_sentinel")

    # fix #20: asyncio.wait with hard timeout to prevent infinite hang.
    # RUNNER_TIMEOUT_SECONDS=0 disables the timeout (infinite — not recommended).
    timeout = _RUNNER_TIMEOUT if _RUNNER_TIMEOUT > 0 else None
    done, _ = await asyncio.wait(
        [runner_task, shutdown_task],
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

    if not runner_task.done():
        logger.info("main: cancelling orchestrator task...")
        runner_task.cancel()
        try:
            await runner_task
        except asyncio.CancelledError:
            logger.info("main: orchestrator cancelled cleanly")
        # Graceful stop — notifies Telegram/Slack
        try:
            await orch.stop_runner()
        except Exception as exc:
            logger.warning("main: stop_runner error (non-fatal): {}", exc)

    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
