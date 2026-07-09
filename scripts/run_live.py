"""
scripts/run_live.py  —  Live / shadow-live trading entry point.

Can be run standalone::

    python scripts/run_live.py --pair BTCUSDT ETHUSDT --mode live

Or called from main.py dispatch (confirmation already handled there)::

    from scripts.run_live import main
    await main(pair=["BTCUSDT", "ETHUSDT"], exchange="bybit", mode="live")

Note: live-mode confirmation prompt has been removed from this module.
Confirmation is now handled by _confirm_live() in main.py before the
coroutine is ever called, so tracebacks propagate cleanly.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from config.settings import QuantLunaConfig
from execution.alert_config import AlertConfig
from execution.live_trader import LiveTrader

logger = logging.getLogger(__name__)


def _build_alert_cfg(cfg: QuantLunaConfig) -> AlertConfig | None:
    token = cfg.notifications.telegram_token
    chat  = cfg.notifications.telegram_chat_id
    if token and chat:
        return AlertConfig(telegram_token=token, telegram_chat_id=chat)
    return None


async def _run(
    sym_y: str,
    sym_x: str,
    cfg: QuantLunaConfig,
    checkpoint: str,
    skip_orphan_scan: bool,
) -> None:
    alert_cfg = _build_alert_cfg(cfg)
    trader = LiveTrader(
        sym_y=sym_y,
        sym_x=sym_x,
        config=cfg,
        checkpoint_path=checkpoint,
        skip_orphan_scan=skip_orphan_scan,
        alert_config=alert_cfg,
    )
    await trader.run()


async def main(
    pair: list[str] | None = None,
    exchange: str | None = None,
    mode: str = "paper",
    checkpoint: str = "checkpoints/live_state.pkl",
    params_file: str | None = None,
    skip_orphan_scan: bool = False,
    **_,
) -> None:
    """
    Live/shadow-live entry point callable from main.py dispatch.
    Live-mode confirmation is the caller's responsibility (main.py
    calls _confirm_live() before invoking this coroutine).
    """
    if pair is None:
        import argparse, sys
        ap = argparse.ArgumentParser(prog="run_live")
        ap.add_argument("--pair", nargs=2, metavar=("SYM_Y", "SYM_X"), required=True)
        ap.add_argument("--exchange", default=os.environ.get("QUANTLUNA_EXCHANGE", "bybit"))
        ap.add_argument("--mode", default="paper", choices=["live", "paper"])
        ap.add_argument("--checkpoint", default="checkpoints/live_state.pkl")
        ap.add_argument("--params", default=None)
        ap.add_argument("--skip-orphan-scan", dest="skip_orphan_scan", action="store_true")
        args = ap.parse_args()
        pair             = args.pair
        exchange         = args.exchange
        mode             = args.mode
        checkpoint       = args.checkpoint
        params_file      = args.params
        skip_orphan_scan = args.skip_orphan_scan

        if mode == "live":
            import sys
            print("\u26a0\ufe0f  LIVE MODE — REAL ORDERS WILL BE PLACED.")
            ans = input("Type 'YES' to confirm: ").strip()
            if ans != "YES":
                print("Aborted.")
                sys.exit(0)

    sym_y_raw, sym_x_raw = pair
    sym_y = sym_y_raw.replace("USDT", "/USDT:USDT") if "/" not in sym_y_raw else sym_y_raw
    sym_x = sym_x_raw.replace("USDT", "/USDT:USDT") if "/" not in sym_x_raw else sym_x_raw

    cfg = QuantLunaConfig()
    cfg.trading_mode = mode
    cfg.execution.exchange = exchange or cfg.execution.exchange

    if mode == "live":
        logger.warning("LIVE MODE — Real orders will be placed on %s", cfg.execution.exchange)

    if params_file:
        import json
        with open(params_file) as f:
            extra = json.load(f).get("params", {})
        # Apply recognised overrides
        if "delta" in extra:
            cfg.kalman.delta = extra["delta"]

    await _run(sym_y, sym_x, cfg, checkpoint, skip_orphan_scan)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
