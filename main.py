"""
QuantLuna — main.py  (Sprint 28 entrypoint)

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
    MARKET_TRADE_ENABLED, CHECKPOINT_PATH
    See BybitLiveRunnerConfig.from_env() for full list.

Flow:
    1. Parse CLI args
    2. Load BybitLiveRunnerConfig from env (+ CLI overrides)
    3. Build NotifierBus (Telegram + Slack)
    4. Build BybitWsFeed
    5. WorkflowOrchestrator.run_startup_workflow()
       Faza 0: HealthCheck -> halt on critical failure
       Faza 1: PositionScanner
       Faza 2: ResumeManager
       Faza 3: AdoptionEngine
       Faza 4: ProfitOptimizer
    6. WorkflowOrchestrator.start_runner() -> blocks
       Faza 5: BybitLiveRunner + optimizer loop in background
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
load_dotenv()

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
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
               "<level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
               "<level>{message}</level>",
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
                logger.warning(f"main: Telegram notifier failed: {exc}")
        if cfg.slack_webhook_url:
            try:
                from notifications.slack_notifier import SlackNotifier, SlackConfig
                bus.register("slack", SlackNotifier(
                    SlackConfig(webhook_url=cfg.slack_webhook_url)
                ))
                logger.info("main: Slack notifier registered")
            except Exception as exc:
                logger.warning(f"main: Slack notifier failed: {exc}")
        return bus
    except Exception as exc:
        logger.warning(f"main: NotifierBus unavailable: {exc}")
        return None


async def _build_ws_feed(cfg):
    try:
        from execution.bybit_ws_feed import BybitWsFeed, BybitWsFeedConfig
        feed_cfg = BybitWsFeedConfig(
            symbol=cfg.symbol_y,
            interval=cfg.interval,
            testnet=os.getenv("BYBIT_TESTNET", "false").lower() == "true",
        )
        feed = BybitWsFeed.from_config(feed_cfg)
        logger.info(
            f"main: BybitWsFeed built "
            f"({cfg.symbol_y}/{cfg.symbol_x} {cfg.interval}m)"
        )
        return feed
    except Exception as exc:
        logger.warning(f"main: BybitWsFeed build failed: {exc}")
        return None


async def main() -> int:
    args = _parse_args()
    _configure_logging(args.log_level)
    os.makedirs("logs", exist_ok=True)

    from execution.bybit_live_runner import BybitLiveRunnerConfig
    cfg = BybitLiveRunnerConfig.from_env()

    if args.dry_run:
        cfg.dry_run = True
    if args.pair:
        parts = args.pair.split("/")
        if len(parts) == 2:
            cfg.symbol_y, cfg.symbol_x = parts[0].upper(), parts[1].upper()
        else:
            logger.error(f"Invalid --pair format: {args.pair!r} (expected Y/X)")
            return 1
    if args.interval:
        cfg.interval = args.interval

    logger.info(
        f"QuantLuna starting — "
        f"{cfg.symbol_y}/{cfg.symbol_x} "
        f"interval={cfg.interval}m "
        f"dry_run={cfg.dry_run}"
    )

    notifier_bus = await _build_notifier_bus(cfg)
    ws_feed = await _build_ws_feed(cfg)

    from execution.workflow_orchestrator import WorkflowOrchestrator
    orch = WorkflowOrchestrator.from_runner_cfg(
        cfg=cfg,
        notifier_bus=notifier_bus,
        ws_feed=ws_feed,
        skip_health_check=args.skip_health or cfg.dry_run,
    )

    ctx = await orch.run_startup_workflow()

    if ctx.should_halt:
        logger.error(f"Startup HALT: {ctx.halt_reason}")
        if notifier_bus:
            try:
                await notifier_bus.send_alert(
                    f"\u274c QuantLuna HALT: {ctx.halt_reason}",
                    level="error",
                )
            except Exception:
                pass
        return 1

    await orch.start_runner(ctx)
    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
