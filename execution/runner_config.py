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
from pathlib import Path

# Load .env so os.getenv returns correct values
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass


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
    ml_enabled:             bool = os.getenv("ML_ENABLED",             "false").lower() == "true"
    kalman_enabled:         bool = os.getenv("KALMAN_ENABLED",         "true").lower() == "true"
    profit_guard_enabled:   bool = os.getenv("PROFIT_GUARD_ENABLED",   "true").lower() == "true"

    # Checkpoint
    checkpoint_path: str = os.getenv("CHECKPOINT_PATH", "state/position_checkpoint.db")

    # Venue / Exchange name
    venue: str = "bybit"

    def __post_init__(self) -> None:
        """Validate all config values at construction time.

        Raises ValueError with a descriptive message if any parameter
        is out of range, preventing silent misconfiguration at startup.
        """
        errors = []

        # --- Entry / Exit thresholds ---
        if self.entry_zscore <= 0:
            errors.append(
                f"ENTRY_ZSCORE={self.entry_zscore} invalid: trebuie > 0. "
                "La 0 se genereaza semnal la orice valoare de spread."
            )
        if self.exit_zscore <= 0:
            errors.append(
                f"EXIT_ZSCORE={self.exit_zscore} invalid: trebuie > 0."
            )
        if self.entry_zscore > 0 and self.exit_zscore >= self.entry_zscore:
            errors.append(
                f"EXIT_ZSCORE={self.exit_zscore} trebuie < ENTRY_ZSCORE={self.entry_zscore}. "
                "Altfel pozitiile nu se vor inchide niciodata."
            )
        if self.base_qty <= 0:
            errors.append(
                f"BASE_QTY={self.base_qty} invalid: trebuie > 0. "
                "Comenzile cu cantitate 0 sunt respinse de exchange."
            )

        # --- Model parameters ---
        if self.warmup_bars < 20:
            errors.append(
                f"WARMUP_BARS={self.warmup_bars} prea mic: minim 20 pentru Kalman stabil. "
                "Sub 20, z-score-urile initiale sunt nereprezentative."
            )
        if self.kalman_window < self.warmup_bars:
            errors.append(
                f"KALMAN_WINDOW={self.kalman_window} trebuie >= WARMUP_BARS={self.warmup_bars}."
            )

        # --- Risk management ---
        if not (0 < self.max_drawdown_pct <= 100):
            errors.append(
                f"MAX_DRAWDOWN_PCT={self.max_drawdown_pct} invalid: trebuie in (0, 100]. "
                "La 200 nu exista stop loss efectiv."
            )
        if self.cooldown_seconds < 0:
            errors.append(
                f"COOLDOWN_SECONDS={self.cooldown_seconds} invalid: trebuie >= 0."
            )
        if self.max_consec_losses < 1:
            errors.append(
                f"MAX_CONSEC_LOSSES={self.max_consec_losses} invalid: trebuie >= 1."
            )

        # --- Capital ---
        if self.initial_capital <= 0:
            errors.append(
                f"INITIAL_CAPITAL={self.initial_capital} invalid: trebuie > 0."
            )

        if errors:
            msg = "BybitLiveRunnerConfig invalid:\n" + "\n".join(f"  - {e}" for e in errors)
            raise ValueError(msg)

    @classmethod
    def from_env(cls) -> "BybitLiveRunnerConfig":
        """Construct config purely from environment variables."""
        return cls()

    @classmethod
    async def resolve_initial_capital(cls, cfg: "BybitLiveRunnerConfig") -> float:
        """Fetch live wallet balance from Bybit and override initial_capital.
        
        Priority: MANUAL env override > LIVE wallet balance > fallback 10000 USDT
        """
        from loguru import logger
        
        env_val = os.getenv("INITIAL_CAPITAL")
        if env_val:
            logger.info("Using manual INITIAL_CAPITAL override: {} USDT", env_val)
            return float(env_val)
        
        try:
            from execution.bybit_order_router import BybitOrderRouter, BybitOrderRouterConfig
            router = BybitOrderRouter(BybitOrderRouterConfig(
                api_key=cfg.api_key,
                api_secret=cfg.api_secret,
                testnet=cfg.testnet,
                dry_run=False,  # must be real to read balance
            ))
            balance = await router.get_wallet_balance()
            logger.info("Auto-detected capital from Bybit: {:.2f} USDT", balance)
            return float(balance)
        except Exception as exc:
            logger.warning(
                "Failed to read Bybit balance — falling back to 10000 USDT. Error: {}",
                exc
            )
            return 10000.0
