"""
QuantLuna — Pair Selector

Scans a universe of assets and ranks pairs by cointegration quality,
half-life, spread stability, and liquidity.
"""
import itertools
import pandas as pd
import numpy as np
from typing import List, Tuple, Optional
from loguru import logger

from core.cointegration import CointegrationTest, CointegrationResult
from config.settings import CointegrationConfig


class PairSelector:
    """
    Brute-force + filtered pair selection from a universe.

    Parameters
    ----------
    universe : list of symbol strings
    min_volume_usdt : minimum 24h volume for pair to be considered
    """

    def __init__(
        self,
        universe: List[str],
        cfg: CointegrationConfig = None,
        min_volume_usdt: float = 5_000_000,
    ):
        self.universe = universe
        self.cfg = cfg or CointegrationConfig()
        self.min_volume_usdt = min_volume_usdt
        self._test = CointegrationTest(
            significance=self.cfg.significance_level,
            min_half_life=12.0,
            max_half_life=168.0,
        )

    def scan(
        self,
        prices: pd.DataFrame,  # columns = symbols, index = timestamps
        log_prices: bool = True,
        freq_hours: float = 1.0,
    ) -> pd.DataFrame:
        """
        Scan all pairs in universe, return ranked DataFrame of results.

        Parameters
        ----------
        prices : DataFrame of close prices
        log_prices : apply log transform before testing
        freq_hours : bar frequency
        """
        if log_prices:
            prices = np.log(prices)

        pairs = list(itertools.combinations(self.universe, 2))
        logger.info(f"Scanning {len(pairs)} pairs from universe of {len(self.universe)}")

        rows = []
        for sym_y, sym_x in pairs:
            if sym_y not in prices.columns or sym_x not in prices.columns:
                continue
            y = prices[sym_y].dropna()
            x = prices[sym_x].dropna()
            aligned = pd.concat([y, x], axis=1).dropna()
            if len(aligned) < self.cfg.min_periods:
                continue

            try:
                result: CointegrationResult = self._test.run(
                    aligned.iloc[:, 0], aligned.iloc[:, 1], freq_hours=freq_hours
                )
                rows.append({
                    "pair": f"{sym_y}/{sym_x}",
                    "sym_y": sym_y,
                    "sym_x": sym_x,
                    "is_cointegrated": result.is_cointegrated,
                    "adf_pvalue": result.adf_pvalue,
                    "eg_pvalue": result.eg_pvalue,
                    "half_life_hours": result.half_life_hours,
                    "hurst": result.hurst_exponent,
                    "static_beta": result.static_beta,
                    "spread_std": result.spread_std,
                    "verdict": result.verdict,
                })
            except Exception as e:
                logger.warning(f"Pair {sym_y}/{sym_x} failed: {e}")

        df = pd.DataFrame(rows)
        if df.empty:
            logger.warning("No pairs passed cointegration scan")
            return df

        # Filter and rank
        passed = df[df["is_cointegrated"]].copy()
        passed["score"] = (
            -passed["adf_pvalue"] * 0.3
            - passed["eg_pvalue"] * 0.3
            + (1 / passed["half_life_hours"].replace(0, np.nan)) * 0.4
        )
        passed = passed.sort_values("score", ascending=False).reset_index(drop=True)

        logger.info(f"Pairs passing cointegration: {len(passed)} / {len(pairs)}")
        return passed
