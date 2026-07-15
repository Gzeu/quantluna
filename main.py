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
from datetime import datetime, timedelta, timezone
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
from startup.bootstrap import (
    build_notifier_bus,
    build_ws_feed,
    inject_api_state,
    wire_dashboard_engine,
)

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
    parser.add_argument("--pairs", type=str, default=None, metavar="Y/X,Y/X",
                        help="Comma-separated pairs: ETHUSDT/SOLUSDT,BTCUSDT/ETHUSDT")
    parser.add_argument("--pair", type=str, default=None, metavar="Y/X",
                        help="[deprecated] Single pair — use --pairs instead")
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

    # ── Parse pairs ─────────────────────────────────────────────────────
    raw_pairs = args.pairs or args.pair or os.getenv("PAIRS", "ETHUSDT/SOLUSDT")
    if "/" in raw_pairs and "," not in raw_pairs:
        pair_list = [raw_pairs]
    else:
        pair_list = [p.strip() for p in raw_pairs.split(",") if p.strip()]

    parsed_pairs = []
    for raw in pair_list:
        parts = raw.split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            parsed_pairs.append((parts[0].upper(), parts[1].upper()))
        else:
            logger.error("Invalid pair format: {!r} (expected Y/X)", raw)
            return 1

    if not parsed_pairs:
        logger.error("No valid pairs specified. Use --pairs Y/X,Y/X or PAIRS env var.")
        return 1

    # ── Resolve capital once (shared across all pairs) ──────────────────
    total_capital = await BybitLiveRunnerConfig.resolve_initial_capital(cfg)
    cfg.initial_capital = total_capital
    capital_per_pair = total_capital / len(parsed_pairs)

    logger.info(
        "QuantLuna multi-pair starting — {} pairs capital={:.2f} USDT dry_run={}",
        len(parsed_pairs), total_capital, cfg.dry_run,
    )
    for sy, sx in parsed_pairs:
        logger.info("  → {}/{}", sy, sx)

    mode = "live" if not cfg.dry_run else "paper"
    _validate_config(cfg, mode)

    # ── Set default symbol on cfg (used by non-pair-specific code) ──────
    cfg.symbol_y, cfg.symbol_x = parsed_pairs[0]

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

    notifier_bus = await build_notifier_bus(cfg)

    from core.state_bus import bus as state_bus
    wire_dashboard_engine(cfg, state_bus)

    # --- Build one BybitLiveRunner per pair ---
    from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig
    from execution.exchange_factory import get_order_router

    runners = []
    pair_labels = []

    for sy, sx in parsed_pairs:
        try:
            pair_cfg = BybitLiveRunnerConfig.from_env()
            pair_cfg.symbol_y = sy
            pair_cfg.symbol_x = sx
            pair_cfg.interval = args.interval or pair_cfg.interval
            if args.dry_run:
                pair_cfg.dry_run = True
            pair_cfg.initial_capital = capital_per_pair

            ws = await build_ws_feed(pair_cfg)
            if ws is None:
                logger.warning("Skipping {}/{} -- WS feed build failed", sy, sx)
                continue

            runner = BybitLiveRunner(
                cfg=pair_cfg,
                exchange=get_order_router(mode="live" if not pair_cfg.dry_run else "paper"),
                ws_feed=ws,
                notifier_bus=notifier_bus,
            )
            runners.append(runner)
            pair_labels.append(f"{sy}/{sx}")
            logger.info("Runner created: {}/{} (capital={:.2f})", sy, sx, capital_per_pair)
        except Exception as exc:
            logger.error("Failed to build runner for {}/{}: {}", sy, sx, exc)

    if not runners:
        logger.error("No runners could be created -- aborting")
        return 1

    # --- Start all runners via asyncio.gather + CapitalAllocator ---
    from execution.capital_allocator import CapitalAllocator, StrategyAllocation
    from execution.daily_pnl_tracker import DailyPnLTracker

    capital_allocator = None
    try:
        tracker = DailyPnLTracker(db_path="state/daily_pnl.db")
        allocations = [
            StrategyAllocation(name="pairs_futures", target_pct=1.0, profit_take_pct=0.03),
        ]
        capital_allocator = CapitalAllocator(
            tracker=tracker, allocations=allocations, notifier_bus=notifier_bus,
        )
        logger.info("CapitalAllocator initialized")
    except Exception as exc:
        logger.warning("CapitalAllocator init failed: {}", exc)

    async def _run_all_pairs():
        tasks = []
        for r, label in zip(runners, pair_labels):
            tasks.append(asyncio.create_task(r.start(), name=label))
        if capital_allocator is not None:
            tasks.append(asyncio.create_task(capital_allocator.run_loop(), name="capital_allocator"))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("All runners cancelled")

    runner_task = asyncio.create_task(_run_all_pairs(), name="multi_runner")

    for sy, sx in parsed_pairs:
        logger.info("  -> {}/{}", sy, sx)
    await notifier_bus.send_alert(
        "\U0001f680 QuantLuna Multi-Pair | " + str(len(runners)) + " perechi | "
        "$" + "{:.2f}".format(total_capital) + " | " + ", ".join(pair_labels),
        level="info",
    )

    # ── Daily summary reporter ─────────────────────────────────────────
    async def _daily_summary_loop():
        """Trimite un raport zilnic la ora ~00:05 UTC."""
        while True:
            now = datetime.now(timezone.utc)
            midnight = now.replace(hour=0, minute=5, second=0, microsecond=0)
            if now >= midnight:
                midnight += timedelta(days=1)
            wait_s = (midnight - now).total_seconds()
            await asyncio.sleep(wait_s)
            try:
                if notifier_bus:
                    # Gather stats from state bus
                    try:
                        from core.state_bus import bus as sb
                        eng = sb.risk_engine
                        snap = eng.snapshot() if eng else {}
                    except Exception:
                        snap = {}
                    await notifier_bus.send_daily_summary(
                        trades=snap.get("total_trades", 0),
                        total_pnl=snap.get("total_pnl_usd", 0),
                        win_rate=snap.get("win_rate", 0),
                    )
                    logger.info("main: daily summary sent")
            except Exception as exc:
                logger.warning("main: daily summary failed: {}", exc)

    daily_task = asyncio.create_task(_daily_summary_loop(), name="daily_summary")

    # ── S48: Start API server alongside the runner ────────────────────
    api_port = int(os.getenv("API_PORT", "8000"))
    api_host = os.getenv("API_HOST", "0.0.0.0")
    api_task = None
    try:
        # Inject risk engine into API (simplified for multi-pair)
        try:
            from api.risk import set_risk_engine
            from core.state_bus import bus as sb
            eng = sb.risk_engine
            if eng:
                set_risk_engine(eng)
        except Exception:
            pass

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

    tasks = [runner_task, shutdown_task, daily_task]
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
