"""
core/funding_rate.py — funding rate monitor helpers.

The monitor is intentionally exchange-agnostic and can be fed by adapters.
It classifies funding conditions for pairs strategies where carry can erode pnl.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FundingSnapshot:
    symbol: str
    funding_rate: float
    annualized_rate: float
    regime: str
    expensive: bool


class FundingRateMonitor:
    def __init__(self, expensive_threshold_bps: float = 5.0) -> None:
        self.expensive_threshold_bps = expensive_threshold_bps

    def classify(self, symbol: str, funding_rate: float, periods_per_day: int = 3) -> FundingSnapshot:
        annualized = funding_rate * periods_per_day * 365.0
        bps = funding_rate * 10_000.0

        if abs(bps) >= self.expensive_threshold_bps:
            regime = "expensive"
            expensive = True
        elif abs(bps) >= self.expensive_threshold_bps / 2:
            regime = "elevated"
            expensive = False
        else:
            regime = "normal"
            expensive = False

        return FundingSnapshot(
            symbol=symbol,
            funding_rate=funding_rate,
            annualized_rate=annualized,
            regime=regime,
            expensive=expensive,
        )

    def should_block_entry(self, funding_rate: float) -> bool:
        return abs(funding_rate * 10_000.0) >= self.expensive_threshold_bps
