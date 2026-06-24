"""
QuantLuna — Global Configuration
All strategy, risk and execution parameters in one place.
"""
from dataclasses import dataclass, field
from typing import List, Optional
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class KalmanConfig:
    """Kalman Filter hyperparameters."""
    delta: float = 1e-4          # Process noise — controls adaptation speed
    observation_noise: float = 1e-2  # R — measurement noise
    initial_state_mean: float = 1.0
    initial_state_cov: float = 1.0
    # Tuning: higher delta → faster adaptation, noisier hedge ratio
    # lower delta → smoother but lags regime changes


@dataclass
class SignalConfig:
    """Z-score signal thresholds."""
    zscore_entry: float = 2.0
    zscore_exit: float = 0.5
    zscore_stop: float = 3.5       # Hard stop — spread diverging too far
    min_half_life_hours: float = 12.0
    max_half_life_hours: float = 168.0  # 7 days max mean reversion
    lookback_periods: int = 500    # Periods for rolling stats


@dataclass
class RiskConfig:
    """Position sizing and risk management."""
    max_capital_usdt: float = float(os.getenv("MAX_CAPITAL_USDT", 10000))
    max_leverage: float = float(os.getenv("MAX_LEVERAGE", 3))
    risk_per_trade: float = float(os.getenv("MAX_RISK_PER_TRADE", 0.01))
    max_drawdown: float = float(os.getenv("MAX_DRAWDOWN", 0.15))
    vol_target_annual: float = 0.20   # 20% annualised vol target
    kelly_fraction: float = 0.25      # Fractional Kelly (25%)
    max_position_pct: float = 0.20    # Max 20% of capital per pair
    correlation_break_threshold: float = 0.60  # Below this — invalidate pair


@dataclass
class ExecutionConfig:
    """Exchange and execution settings."""
    exchange: str = "binance"
    market_type: str = "future"    # spot | future | swap
    maker_fee: float = 0.0002      # 2bps
    taker_fee: float = 0.0005      # 5bps
    slippage_bps: float = 3.0      # Assumed slippage in basis points
    min_order_usdt: float = 10.0
    max_order_usdt: float = 5000.0
    order_type: str = "limit"      # limit | market


@dataclass
class CointegrationConfig:
    """Cointegration test settings."""
    significance_level: float = 0.05
    min_adf_pvalue: float = 0.05   # ADF must reject unit root
    min_periods: int = 252         # Minimum periods for test (1 year of daily)
    johansen_max_rank: int = 1
    retest_interval_hours: int = 24  # Re-validate cointegration daily


@dataclass
class QuantLunaConfig:
    """Master config."""
    kalman: KalmanConfig = field(default_factory=KalmanConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    coint: CointegrationConfig = field(default_factory=CointegrationConfig)
    trading_mode: str = os.getenv("TRADING_MODE", "paper")  # paper | live
    log_level: str = "INFO"
    telegram_alerts: bool = bool(os.getenv("TELEGRAM_BOT_TOKEN"))


# Singleton
config = QuantLunaConfig()
