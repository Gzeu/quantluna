"""
execution/runner_config.py  —  BybitLiveRunnerConfig

Extracted from bybit_live_runner.py (Sprint 28 SRP refactor).
All env-var defaults live here; bybit_live_runner.py imports this.

Sprint next (fix #23):
  Added __post_init__ validation so invalid config raises ValueError
  at startup rather than silently misbehaving in production.

Valid ranges documented in .env.example.

Usage::

    from execution.runner_config import BybitLiveRunnerConfig
    cfg = BybitLiveRunnerConfig.from_env()
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


_VALID_INTERVALS = {1, 3, 5, 15, 30, 60, 240, 1440}


@dataclass
class BybitLiveRunnerConfig:
    """Complete runtime configuration — loaded from env vars with safe defaults.

    All fields are validated in __post_init__. A ValueError at startup is
    far better than a silent misconfiguration causing live trading losses.
    """

    # Symbol pair
    symbol_y: str = field(default_factory=lambda: os.getenv("SYMBOL_Y", "BTCUSDT"))
    symbol_x: str = field(default_factory=lambda: os.getenv("SYMBOL_X", "ETHUSDT"))
    interval: int = field(default_factory=lambda: int(os.getenv("INTERVAL", "5")))

    # Dry-run / paper trading
    dry_run: bool = field(default_factory=lambda: os.getenv("DRY_RUN", "true").lower() == "true")

    # API Keys
    api_key:    str  = field(default_factory=lambda: os.getenv("BYBIT_API_KEY", ""))
    api_secret: str  = field(default_factory=lambda: os.getenv("BYBIT_API_SECRET", ""))
    testnet:    bool = field(default_factory=lambda: os.getenv("BYBIT_TESTNET", "false").lower() == "true")

    # Entry / Exit thresholds
    entry_zscore: float = field(default_factory=lambda: float(os.getenv("ENTRY_ZSCORE", "2.0")))
    exit_zscore:  float = field(default_factory=lambda: float(os.getenv("EXIT_ZSCORE",  "0.5")))
    base_qty:     float = field(default_factory=lambda: float(os.getenv("BASE_QTY",     "0.01")))

    # Model parameters
    warmup_bars:   int   = field(default_factory=lambda: int(os.getenv("WARMUP_BARS",   "100")))
    kalman_window: int   = field(default_factory=lambda: int(os.getenv("KALMAN_WINDOW", "200")))
    half_life_h:   float = field(default_factory=lambda: float(os.getenv("HALF_LIFE_H", "24.0")))

    # Capital
    initial_capital: float = field(default_factory=lambda: float(os.getenv("INITIAL_CAPITAL", "10000")))

    # Risk management
    max_consec_losses: int   = field(default_factory=lambda: int(os.getenv("MAX_CONSEC_LOSSES", "3")))
    max_drawdown_pct:  float = field(default_factory=lambda: float(os.getenv("MAX_DRAWDOWN_PCT", "10.0")))
    cooldown_seconds:  int   = field(default_factory=lambda: int(os.getenv("COOLDOWN_SECONDS",  "300")))

    # Notifications
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id:   str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID",   ""))
    slack_webhook_url:  str = field(default_factory=lambda: os.getenv("SLACK_WEBHOOK_URL",  ""))

    # Health server
    health_port: int = field(default_factory=lambda: int(os.getenv("HEALTH_PORT", "8081")))

    # Subsystem toggles
    funding_gate_enabled:   bool = field(default_factory=lambda: os.getenv("FUNDING_GATE_ENABLED",   "true").lower() == "true")
    pnl_reconciler_enabled: bool = field(default_factory=lambda: os.getenv("PNL_RECONCILER_ENABLED", "true").lower() == "true")
    market_trade_enabled:   bool = field(default_factory=lambda: os.getenv("MARKET_TRADE_ENABLED",   "true").lower() == "true")

    # Core/WorkflowOrchestrator v2.2 toggles
    enable_reoptimizer: bool = field(default_factory=lambda: os.getenv("OPTIMIZER_ENABLED",  "true").lower() == "true")
    enable_watchdog:    bool = field(default_factory=lambda: os.getenv("WATCHDOG_ENABLED",    "true").lower() == "true")
    enable_spot:        bool = field(default_factory=lambda: os.getenv("ENABLE_SPOT",         "false").lower() == "true")
    enable_margin:      bool = field(default_factory=lambda: os.getenv("ENABLE_MARGIN",       "false").lower() == "true")

    # Checkpoint
    checkpoint_path: str = field(default_factory=lambda: os.getenv("CHECKPOINT_PATH", "state/position_checkpoint.db"))

    # ------------------------------------------------------------------ #
    # Validation (fix #23)                                                #
    # ------------------------------------------------------------------ #

    def __post_init__(self) -> None:
        """Validate all fields immediately after construction.

        Raises ValueError with a clear message so the operator knows
        exactly which env var is wrong before the bot touches the exchange.
        """
        errors: list[str] = []

        # --- Z-score thresholds ---
        if self.entry_zscore <= 0:
            errors.append(
                f"ENTRY_ZSCORE must be > 0 (got {self.entry_zscore}). "
                "A zero entry threshold fires a signal on every bar."
            )
        if self.exit_zscore < 0:
            errors.append(
                f"EXIT_ZSCORE must be >= 0 (got {self.exit_zscore})."
            )
        if self.entry_zscore > 0 and self.exit_zscore >= self.entry_zscore:
            errors.append(
                f"EXIT_ZSCORE ({self.exit_zscore}) must be < ENTRY_ZSCORE "
                f"({self.entry_zscore}). Positions would never close."
            )

        # --- Order size ---
        if self.base_qty <= 0:
            errors.append(
                f"BASE_QTY must be > 0 (got {self.base_qty}). "
                "Zero-quantity orders are rejected by the exchange."
            )

        # --- Kalman warmup ---
        if self.warmup_bars < 20:
            errors.append(
                f"WARMUP_BARS must be >= 20 (got {self.warmup_bars}). "
                "Fewer bars produce unreliable Kalman filter estimates and NaN z-scores."
            )
        if self.kalman_window < self.warmup_bars:
            errors.append(
                f"KALMAN_WINDOW ({self.kalman_window}) should be >= WARMUP_BARS "
                f"({self.warmup_bars}) to avoid lookback underflow."
            )

        # --- Risk ---
        if not (0 < self.max_drawdown_pct <= 100):
            errors.append(
                f"MAX_DRAWDOWN_PCT must be in (0, 100] (got {self.max_drawdown_pct}). "
                "Values > 100 disable the drawdown stop."
            )
        if self.cooldown_seconds < 0:
            errors.append(
                f"COOLDOWN_SECONDS must be >= 0 (got {self.cooldown_seconds})."
            )
        if self.max_consec_losses < 1:
            errors.append(
                f"MAX_CONSEC_LOSSES must be >= 1 (got {self.max_consec_losses})."
            )

        # --- Capital ---
        if self.initial_capital <= 0:
            errors.append(
                f"INITIAL_CAPITAL must be > 0 (got {self.initial_capital})."
            )

        # --- Interval ---
        if self.interval not in _VALID_INTERVALS:
            errors.append(
                f"INTERVAL must be one of {sorted(_VALID_INTERVALS)} (got {self.interval})."
            )

        # --- Half-life ---
        if self.half_life_h <= 0:
            errors.append(
                f"HALF_LIFE_H must be > 0 (got {self.half_life_h})."
            )

        if errors:
            msg = "BybitLiveRunnerConfig validation failed:\n" + "\n".join(
                f"  [{i+1}] {e}" for i, e in enumerate(errors)
            )
            raise ValueError(msg)

    # ------------------------------------------------------------------ #
    # Factory                                                             #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_env(cls) -> "BybitLiveRunnerConfig":
        """Construct config from environment variables and validate immediately."""
        return cls()

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def summary(self) -> str:
        """One-line config summary for startup logs."""
        return (
            f"{self.symbol_y}/{self.symbol_x} interval={self.interval}m "
            f"entry_z={self.entry_zscore} exit_z={self.exit_zscore} "
            f"qty={self.base_qty} warmup={self.warmup_bars} "
            f"dd_max={self.max_drawdown_pct}% dry={self.dry_run}"
        )

    def is_live(self) -> bool:
        """True if running against real exchange with real keys."""
        return not self.dry_run and bool(self.api_key) and bool(self.api_secret)
