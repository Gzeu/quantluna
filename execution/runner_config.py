"""
execution/runner_config.py  —  BybitLiveRunnerConfig

Extracted from bybit_live_runner.py (Sprint 28 SRP refactor).
All env-var defaults live here; bybit_live_runner.py imports this.

Usage::

    from execution.runner_config import BybitLiveRunnerConfig
    cfg = BybitLiveRunnerConfig.from_env()
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class BybitLiveRunnerConfig:
    """Complete runtime configuration — loaded from env vars with safe defaults."""

    # Symbol pair
    symbol_y: str = os.getenv("SYMBOL_Y", "BTCUSDT")
    symbol_x: str = os.getenv("SYMBOL_X", "ETHUSDT")
    interval: int = int(os.getenv("INTERVAL", "5"))

    # Dry-run / paper trading
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"

    # API Keys
    api_key: str    = os.getenv("BYBIT_API_KEY", "")
    api_secret: str = os.getenv("BYBIT_API_SECRET", "")
    testnet: bool   = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

    # Entry / Exit thresholds
    entry_zscore: float = float(os.getenv("ENTRY_ZSCORE", "2.0"))
    exit_zscore:  float = float(os.getenv("EXIT_ZSCORE",  "0.5"))
    base_qty:     float = float(os.getenv("BASE_QTY",     "0.01"))

    # Model parameters
    warmup_bars:   int   = int(os.getenv("WARMUP_BARS",   "100"))
    kalman_window: int   = int(os.getenv("KALMAN_WINDOW", "200"))
    half_life_h:   float = float(os.getenv("HALF_LIFE_H", "24.0"))

    # Capital (used by RiskDashboardEngine wiring in main.py)
    initial_capital: float = float(os.getenv("INITIAL_CAPITAL", "10000"))

    # Risk management
    max_consec_losses: int   = int(os.getenv("MAX_CONSEC_LOSSES", "3"))
    max_drawdown_pct:  float = float(os.getenv("MAX_DRAWDOWN_PCT", "10.0"))
    cooldown_seconds:  int   = int(os.getenv("COOLDOWN_SECONDS",  "300"))

    # Notifications
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id:   str = os.getenv("TELEGRAM_CHAT_ID",   "")
    slack_webhook_url:  str = os.getenv("SLACK_WEBHOOK_URL",  "")

    # Health server
    health_port: int = int(os.getenv("HEALTH_PORT", "8081"))

    # Subsystem toggles (Sprint 28)
    funding_gate_enabled:   bool = os.getenv("FUNDING_GATE_ENABLED",   "true").lower() == "true"
    pnl_reconciler_enabled: bool = os.getenv("PNL_RECONCILER_ENABLED", "true").lower() == "true"
    market_trade_enabled:   bool = os.getenv("MARKET_TRADE_ENABLED",   "true").lower() == "true"

    # Checkpoint
    checkpoint_path: str = os.getenv("CHECKPOINT_PATH", "state/position_checkpoint.db")

    @classmethod
    def from_env(cls) -> "BybitLiveRunnerConfig":
        """Construct config purely from environment variables."""
        return cls()
