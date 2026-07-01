"""
config/strategy_config.py  —  QuantLuna Master Strategy Config

Sprint 14 — dataclass master care agregă toți parametrii într-un singur
loc, folosit de live trader, paper trader, backtest și optimizer.

Usage:
    from config.strategy_config import StrategyConfig

    cfg = StrategyConfig()  # toate default-urile

    # override din JSON (output optimizer):
    cfg = StrategyConfig.from_optimizer_json("best_params.json")

    # override din .env:
    cfg = StrategyConfig.from_env()
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from config.cointegration_config import CointegrationConfig


@dataclass
class StrategyConfig:
    """
    Master config pentru întreaga strategie QuantLuna.

    Parametrii sunt grupați în secțiuni logice:
      - Pair / Exchange
      - Kalman Filter
      - Signal (z-score)
      - Risk / Sizing
      - Regime filter (half-life gates)
      - Cointegration validation
      - Execution
      - Capital
    """

    # ------------------------------------------------------------------ #
    # Pair / Exchange
    # ------------------------------------------------------------------ #
    sym_y: str = "BTCUSDT"
    sym_x: str = "ETHUSDT"
    exchange: str = "bybit"
    bar_freq: str = "1h"

    # ------------------------------------------------------------------ #
    # Kalman Filter
    # ------------------------------------------------------------------ #
    delta: float = 1e-4           # process noise (adapt speed)
    observation_noise: float = 1e-2  # measurement noise R
    warm_up_bars: int = 30        # bars before trading allowed

    # ------------------------------------------------------------------ #
    # Signal
    # ------------------------------------------------------------------ #
    zscore_entry: float = 2.0     # open trade threshold
    zscore_exit: float = 0.5      # close trade threshold
    zscore_window: int = 100      # rolling window for spread z-score

    # ------------------------------------------------------------------ #
    # Risk / Sizing
    # ------------------------------------------------------------------ #
    kelly_fraction: float = 0.25  # fractional Kelly (1.0 = full Kelly)
    vol_target: float = 0.01      # target daily volatility (1%)
    max_notional_usdt: float = 5_000.0
    max_position_pct: float = 0.20  # max single pair as % of capital
    portfolio_hard_dd: float = 0.10  # 10% portfolio DD — HARD STOP
    portfolio_soft_dd: float = 0.05  # 5% — reduce sizing
    pair_dd_limit: float = 0.05   # 5% per-pair DD limit
    daily_dd_limit: float = 0.03  # 3% daily DD limit

    # ------------------------------------------------------------------ #
    # Regime filter
    # ------------------------------------------------------------------ #
    half_life_min_h: float = 2.0   # min acceptable half-life (hours)
    half_life_max_h: float = 168.0 # max acceptable half-life (7 days)
    stability_window: int = 50     # bars for regime stability check
    min_correlation: float = 0.60  # minimum Pearson correlation

    # ------------------------------------------------------------------ #
    # Cointegration
    # ------------------------------------------------------------------ #
    cointegration: CointegrationConfig = field(
        default_factory=CointegrationConfig
    )

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    order_type: str = "market"    # 'market' | 'limit'
    slippage_pct: float = 0.0005  # assumed slippage for sizing
    fee_rate: float = 0.00055     # Bybit taker
    min_trade_interval_s: float = 30.0  # min seconds between orders

    # ------------------------------------------------------------------ #
    # Capital
    # ------------------------------------------------------------------ #
    capital_usdt: float = 10_000.0
    min_trade_notional_usdt: float = 50.0

    def __post_init__(self) -> None:
        if self.zscore_exit >= self.zscore_entry:
            raise ValueError(
                f"zscore_exit ({self.zscore_exit}) must be < zscore_entry ({self.zscore_entry})"
            )
        if self.half_life_min_h >= self.half_life_max_h:
            raise ValueError(
                f"half_life_min_h ({self.half_life_min_h}) must be < "
                f"half_life_max_h ({self.half_life_max_h})"
            )
        if not (0.0 < self.kelly_fraction <= 1.0):
            raise ValueError(f"kelly_fraction must be in (0, 1], got {self.kelly_fraction}")
        if not (0.0 < self.capital_usdt):
            raise ValueError(f"capital_usdt must be > 0")

    @classmethod
    def from_optimizer_json(cls, path: str) -> "StrategyConfig":
        """
        Construiește config din output-ul OptimizerResult.save_json().
        Parametrii din JSON suprascriu default-urile.
        Parametrii necunoscuți sunt ignorați (nu ridică eroare).
        """
        with open(path) as f:
            data = json.load(f)
        params = data.get("params", data)  # suportă și dict direct
        known_fields = StrategyConfig.__dataclass_fields__.keys()
        filtered = {k: v for k, v in params.items() if k in known_fields}
        return cls(**filtered)

    @classmethod
    def from_env(cls) -> "StrategyConfig":
        """
        Override parametri din variabile de mediu.
        Prefix: QUANTLUNA_
        Exemple:
            QUANTLUNA_SYM_Y=BTCUSDT
            QUANTLUNA_DELTA=0.0001
            QUANTLUNA_ZSCORE_ENTRY=2.0
            QUANTLUNA_CAPITAL_USDT=10000
        """
        def _get(key: str, default, cast):
            v = os.environ.get(f"QUANTLUNA_{key.upper()}")
            return cast(v) if v is not None else default

        return cls(
            sym_y=_get("SYM_Y", "BTCUSDT", str),
            sym_x=_get("SYM_X", "ETHUSDT", str),
            exchange=_get("EXCHANGE", "bybit", str),
            bar_freq=_get("BAR_FREQ", "1h", str),
            delta=_get("DELTA", 1e-4, float),
            observation_noise=_get("OBSERVATION_NOISE", 1e-2, float),
            warm_up_bars=_get("WARM_UP_BARS", 30, int),
            zscore_entry=_get("ZSCORE_ENTRY", 2.0, float),
            zscore_exit=_get("ZSCORE_EXIT", 0.5, float),
            kelly_fraction=_get("KELLY_FRACTION", 0.25, float),
            vol_target=_get("VOL_TARGET", 0.01, float),
            capital_usdt=_get("CAPITAL_USDT", 10_000.0, float),
            portfolio_hard_dd=_get("PORTFOLIO_HARD_DD", 0.10, float),
            half_life_min_h=_get("HALF_LIFE_MIN_H", 2.0, float),
            half_life_max_h=_get("HALF_LIFE_MAX_H", 168.0, float),
            cointegration=CointegrationConfig.from_env(),
        )

    def to_dict(self) -> dict:
        """Flat dict pentru logging / export (fără obiectele nested)."""
        import dataclasses
        d = dataclasses.asdict(self)
        return d

    def summary(self) -> str:
        """Human-readable one-liner cu parametrii principali."""
        return (
            f"StrategyConfig({self.sym_y}/{self.sym_x} {self.exchange} {self.bar_freq} | "
            f"delta={self.delta} z={self.zscore_entry}/{self.zscore_exit} "
            f"kelly={self.kelly_fraction} capital={self.capital_usdt:.0f})"
        )
