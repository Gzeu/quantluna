"""
QuantLuna — Live Trading CLI  (v2 — cu WorkflowOrchestrator)

Startup sequence:
  0. preflight_check (deja exista in LiveTrader)
  1. Position Scan     ← PositionScanner
  2. Reconciliere      ← ResumeManager
  3. Adoptie orfane    ← AdoptionEngine
  4. ProfitOptimizer   ← registrare pozitii adoptate
  5. LiveTrader.run()  + optimizer_loop (background)

Usage:
  python scripts/run_live.py --pair ETHUSDT BTCUSDT --mode paper
  python scripts/run_live.py --pair ETHUSDT BTCUSDT --mode live
  python scripts/run_live.py --pair ETHUSDT BTCUSDT --mode live --skip-orphan-scan
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import QuantLunaConfig
from execution.live_trader import LiveTrader, AlertConfig
from execution.workflow_orchestrator import WorkflowOrchestrator, AdoptionConfig


def _build_alert_cfg(cfg: QuantLunaConfig) -> AlertConfig | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    if token and chat:
        return AlertConfig(bot_token=token, chat_id=chat)
    return None


async def _run(
    sym_y: str,
    sym_x: str,
    cfg: QuantLunaConfig,
    checkpoint_path: str = "position_checkpoint.db",
    skip_orphan_scan: bool = False,
) -> None:
    alert_cfg = _build_alert_cfg(cfg)

    # Instantiem LiveTrader — initializeaza exchange intern
    trader = LiveTrader(sym_y=sym_y, sym_x=sym_x, cfg=cfg)

    # Extragem exchange-ul initializat pentru a evita doua conexiuni separate
    exchange = getattr(trader, '_exchange', None)

    # ----------------------------------------------------------------
    # FAZA 1-4: WorkflowOrchestrator (scan → reconcile → adopt → register)
    # ----------------------------------------------------------------
    if exchange is not None and not skip_orphan_scan:
        adopt_cfg = AdoptionConfig(
            adopt_min_pnl_pct=float(os.getenv("ADOPT_MIN_PNL_PCT", -0.02)),
            close_loss_pct=float(os.getenv("CLOSE_LOSS_PCT", -0.05)),
            min_liq_distance_pct=float(os.getenv("MIN_LIQ_DISTANCE_PCT", 0.08)),
            tp_target_pct=float(os.getenv("TP_TARGET_PCT", 0.04)),
            sl_max_loss_pct=float(os.getenv("SL_MAX_LOSS_PCT", 0.03)),
            trailing_activation_pct=float(os.getenv("TRAILING_ACTIVATION_PCT", 0.02)),
            trailing_distance_pct=float(os.getenv("TRAILING_DISTANCE_PCT", 0.015)),
        )

        orch = WorkflowOrchestrator(
            exchange=exchange,
            checkpoint_path=checkpoint_path,
            alert_cfg=alert_cfg,
            adoption_config=adopt_cfg,
        )

        ctx = await orch.run_startup_workflow()

        if ctx.should_halt:
            logger.critical(f"[Startup] HALT cerut de orchestrator: {ctx.halt_reason}")
            sys.exit(1)

        if ctx.has_adopted_positions:
            logger.info(
                f"[Startup] {ctx.adopted_count} pozitii adoptate, "
                f"{ctx.closed_count} inchise automat — pornire optimizer loop"
            )

            async def _get_prices() -> dict:
                """Price feed pentru optimizer loop."""
                prices = {}
                if ctx.optimizer:
                    for sym in list(ctx.optimizer._positions.keys()):
                        try:
                            ticker = await exchange.fetch_ticker(sym)
                            prices[sym] = float(ticker.get('last', 0))
                        except Exception as exc:
                            logger.warning(f"[PriceFeed] {sym}: {exc}")
                return prices

            poll_interval = float(os.getenv("OPTIMIZER_POLL_INTERVAL_S", 1.0))
            asyncio.create_task(
                orch.run_optimizer_loop(ctx, _get_prices, poll_interval_s=poll_interval)
            )
    else:
        if skip_orphan_scan:
            logger.info("[Startup] Orphan scan dezactivat (--skip-orphan-scan)")
        else:
            logger.warning("[Startup] Exchange neinitializat — skip orphan scan")

    # ----------------------------------------------------------------
    # FAZA 5: LiveTrader normal
    # ----------------------------------------------------------------
    await trader.run()


@click.command()
@click.option("--pair", nargs=2, required=True, help="Doua simboluri: Y X")
@click.option("--mode", default="paper",
              type=click.Choice(["paper", "live", "testnet"]), help="Trading mode")
@click.option("--exchange", default="binance")
@click.option("--params", default=None, help="JSON file cu parametri optimizati")
@click.option("--checkpoint", default="position_checkpoint.db",
              help="Cale checkpoint SQLite")
@click.option("--skip-orphan-scan", is_flag=True, default=False,
              help="Sari peste scanarea pozitiilor orfane la startup")
def main(pair, mode, exchange, params, checkpoint, skip_orphan_scan):
    sym_y_raw, sym_x_raw = pair
    sym_y = sym_y_raw.replace("USDT", "/USDT:USDT") if "/" not in sym_y_raw else sym_y_raw
    sym_x = sym_x_raw.replace("USDT", "/USDT:USDT") if "/" not in sym_x_raw else sym_x_raw

    cfg = QuantLunaConfig()
    cfg.trading_mode = mode
    cfg.execution.exchange = exchange

    if mode == "live":
        logger.warning("LIVE MODE — Ordine REALE vor fi plasate!")
        confirm = input("Scrie 'YES' pentru confirmare: ")
        if confirm.strip() != "YES":
            logger.info("Anulat de utilizator")
            return

    asyncio.run(_run(sym_y, sym_x, cfg, checkpoint, skip_orphan_scan))


if __name__ == "__main__":
    main()
