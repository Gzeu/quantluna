"""
QuantLuna — Position Sizing

Methodology: Volatility-targeting with fractional Kelly overlay.

1. Target annual volatility on spread: vol_target
2. Size = (vol_target / spread_vol_annual) * capital
3. Apply Kelly fraction (default 0.25 = quarter Kelly)
4. Apply max position cap
5. Account for funding rate drag on perp positions
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Tuple
from loguru import logger

from config.settings import RiskConfig


@dataclass
class SizingResult:
    qty_y: float           # Notional USDT in Y leg
    qty_x: float           # Notional USDT in X leg (= qty_y * beta)
    leverage_y: float
    leverage_x: float
    spread_vol_annual: float
    kelly_size: float
    vol_target_size: float
    final_size: float      # Notional USDT total pair exposure
    risk_per_trade_usdt: float
    warning: Optional[str] = None


class PositionSizer:
    """
    Calculates position sizes for a pairs trade.

    Parameters
    ----------
    cfg : RiskConfig
    """

    def __init__(self, cfg: RiskConfig = None):
        self.cfg = cfg or RiskConfig()

    def size(
        self,
        capital_usdt: float,
        beta: float,
        spread_series: pd.Series,
        price_y: float,
        price_x: float,
        sharpe_estimate: float = 0.5,
        funding_rate_8h: float = 0.0,
        freq_hours: float = 1.0,
    ) -> SizingResult:
        """
        Calculate position sizes for Y / X legs.

        Parameters
        ----------
        capital_usdt    : Available capital
        beta            : Current Kalman hedge ratio
        spread_series   : Recent spread history (for vol estimate)
        price_y/x       : Current prices
        sharpe_estimate : Expected Sharpe for Kelly calculation
        funding_rate_8h : Current funding rate per 8h period
        freq_hours      : Bar frequency
        """
        # --- Spread volatility (annualised) ---
        bars_per_year = (365 * 24) / freq_hours
        spread_vol_bar = spread_series.std()
        spread_vol_annual = spread_vol_bar * np.sqrt(bars_per_year)

        if spread_vol_annual < 1e-10:
            logger.warning("Spread vol near zero — skipping sizing")
            return SizingResult(0, 0, 0, 0, 0, 0, 0, 0, 0, "zero_spread_vol")

        # --- Vol-target sizing ---
        vol_target_size = (
            self.cfg.vol_target_annual / spread_vol_annual
        ) * capital_usdt

        # --- Kelly sizing ---
        # Simplified Kelly: f* = Sharpe^2 / (1 + Sharpe^2)
        kelly_full = sharpe_estimate**2 / (1 + sharpe_estimate**2)
        kelly_size = self.cfg.kelly_fraction * kelly_full * capital_usdt

        # --- Take the minimum ---
        raw_size = min(vol_target_size, kelly_size)

        # --- Cap at max position ---
        max_size = self.cfg.max_position_pct * capital_usdt * self.cfg.max_leverage
        final_size = min(raw_size, max_size)

        # --- Funding drag adjustment ---
        # Annualised funding cost (3x per day * 365)
        annual_funding_cost = abs(funding_rate_8h) * 3 * 365 * final_size
        if annual_funding_cost > 0.05 * final_size:  # > 5% annual drag
            final_size *= 0.75
            warning = f"Funding drag high ({funding_rate_8h*100:.4f}%/8h) — sizing reduced 25%"
            logger.warning(warning)
        else:
            warning = None

        # --- Split into legs ---
        # qty_y in USDT, qty_x = qty_y * beta
        qty_y = final_size / (1 + abs(beta))
        qty_x = qty_y * abs(beta)

        leverage_y = qty_y / (capital_usdt * self.cfg.max_position_pct)
        leverage_x = qty_x / (capital_usdt * self.cfg.max_position_pct)

        risk_per_trade = self.cfg.risk_per_trade * capital_usdt

        logger.info(
            f"Sizing: final={final_size:.0f} USDT, "
            f"Y={qty_y:.0f}, X={qty_x:.0f}, beta={beta:.4f}"
        )

        return SizingResult(
            qty_y=qty_y,
            qty_x=qty_x,
            leverage_y=leverage_y,
            leverage_x=leverage_x,
            spread_vol_annual=spread_vol_annual,
            kelly_size=kelly_size,
            vol_target_size=vol_target_size,
            final_size=final_size,
            risk_per_trade_usdt=risk_per_trade,
            warning=warning,
        )
