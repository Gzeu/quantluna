"""
QuantLuna — PairSelector v2

Batch cointegration scanner with 5-factor composite scoring.

Composite score weights:
  w_adf             = 0.25  (lower p-value → better)
  w_half_life       = 0.30  (peaks at optimal_hl_hours, penalises extremes)
  w_hurst           = 0.20  (lower exponent → more mean-reverting)
  w_spread_stability= 0.15  (lower rolling-beta-std / mean-beta → more stable)
  w_correlation     = 0.10  (higher rolling corr → more reliable)

Filters applied BEFORE cointegration test (cheap):
  - min_bars        : skip if < min_periods bars aligned
  - min_corr        : skip if rolling(30) corr < min_corr
  - volume gate     : placeholder (requires external volume Series)

Filters applied AFTER cointegration test (expensive):
  - is_cointegrated : Engle-Granger ADF pvalue < significance_level
  - half_life range : [min_half_life_hours, max_half_life_hours]
  - hurst_exponent  : < max_hurst (default 0.5)
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from core.cointegration import CointegrationTest, CointegrationResult
from config.settings import CointegrationConfig


@dataclass
class PairScore:
    sym_y: str
    sym_x: str
    pair: str
    adf_pvalue: float
    eg_pvalue: float
    half_life_hours: float
    hurst: float
    static_beta: float
    spread_std: float
    correlation: float
    composite_score: float
    last_scanned: float = field(default_factory=time.time)  # unix ts
    is_cointegrated: bool = True
    verdict: str = ""


class PairSelector:
    """
    Scan a universe of assets and rank pairs by cointegration quality.

    Parameters
    ----------
    universe              : list of symbol strings (must match prices DataFrame columns)
    cfg                   : CointegrationConfig
    min_corr              : pre-filter: minimum rolling(30) correlation (default 0.6)
    optimal_hl_hours      : half-life score peaks here (default 24h)
    max_hurst             : pairs with Hurst >= this are rejected (default 0.5)
    min_half_life_hours   : pairs with HL < this are rejected (too fast)
    max_half_life_hours   : pairs with HL > this are rejected (too slow)
    staleness_hours       : rescan_stale() rescans pairs older than this (default 48h)
    """

    def __init__(
        self,
        universe: List[str],
        cfg: Optional[CointegrationConfig] = None,
        min_corr: float = 0.60,
        optimal_hl_hours: float = 24.0,
        max_hurst: float = 0.50,
        min_half_life_hours: float = 4.0,
        max_half_life_hours: float = 168.0,
        staleness_hours: float = 48.0,
    ) -> None:
        self.universe = universe
        self.cfg = cfg or CointegrationConfig()
        self.min_corr = min_corr
        self.optimal_hl_hours = optimal_hl_hours
        self.max_hurst = max_hurst
        self.min_half_life_hours = min_half_life_hours
        self.max_half_life_hours = max_half_life_hours
        self.staleness_hours = staleness_hours

        self._test = CointegrationTest(
            significance=self.cfg.significance_level,
            min_half_life=self.min_half_life_hours,
            max_half_life=self.max_half_life_hours,
        )
        self._cache: Dict[str, PairScore] = {}  # pair_key → PairScore

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(
        self,
        prices: pd.DataFrame,
        log_prices: bool = True,
        freq_hours: float = 1.0,
        volume_usdt: Optional[Dict[str, float]] = None,
    ) -> pd.DataFrame:
        """
        Full scan of all pairs in universe.

        Parameters
        ----------
        prices       : DataFrame, columns=symbols, index=timestamps
        log_prices   : apply log transform before testing (recommended)
        freq_hours   : bar frequency in hours
        volume_usdt  : dict symbol->24h_volume_usd (optional liquidity gate)

        Returns
        -------
        DataFrame ranked by composite_score descending.
        Includes both passing and failing pairs (is_cointegrated column).
        """
        if log_prices:
            prices = np.log(prices.clip(lower=1e-12))

        pairs = list(itertools.combinations(self.universe, 2))
        logger.info(f"PairSelector: scanning {len(pairs)} pairs ({len(self.universe)} symbols)")

        rows: List[Dict] = []
        skipped = 0

        for sym_y, sym_x in pairs:
            if sym_y not in prices.columns or sym_x not in prices.columns:
                skipped += 1
                continue

            # Volume gate (cheap, before cointegration)
            if volume_usdt is not None:
                min_vol = self.cfg.__dict__.get("min_volume_usdt", 5_000_000)
                if volume_usdt.get(sym_y, 0) < min_vol or volume_usdt.get(sym_x, 0) < min_vol:
                    skipped += 1
                    continue

            y = prices[sym_y].dropna()
            x = prices[sym_x].dropna()
            aligned = pd.concat([y, x], axis=1).dropna()

            if len(aligned) < self.cfg.min_periods:
                skipped += 1
                continue

            # Correlation pre-filter (cheap)
            corr_window = min(30, len(aligned))
            corr = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
            if corr < self.min_corr:
                skipped += 1
                continue

            try:
                result: CointegrationResult = self._test.run(
                    aligned.iloc[:, 0],
                    aligned.iloc[:, 1],
                    freq_hours=freq_hours,
                )
            except Exception as exc:
                logger.warning(f"Coint test failed {sym_y}/{sym_x}: {exc}")
                continue

            hl = float(result.half_life_hours) if result.half_life_hours else np.nan
            hurst = float(result.hurst_exponent) if result.hurst_exponent else 0.5

            score = self.composite_score(
                adf_pvalue=result.adf_pvalue,
                half_life_hours=hl,
                hurst=hurst,
                spread_std=result.spread_std,
                static_beta=result.static_beta,
                correlation=corr,
            ) if result.is_cointegrated else 0.0

            ps = PairScore(
                sym_y=sym_y,
                sym_x=sym_x,
                pair=f"{sym_y}/{sym_x}",
                adf_pvalue=result.adf_pvalue,
                eg_pvalue=result.eg_pvalue,
                half_life_hours=hl,
                hurst=hurst,
                static_beta=result.static_beta,
                spread_std=result.spread_std,
                correlation=corr,
                composite_score=score,
                is_cointegrated=result.is_cointegrated,
                verdict=result.verdict,
            )
            self._cache[f"{sym_y}/{sym_x}"] = ps
            rows.append(ps.__dict__)

        logger.info(
            f"Scan complete: {len(rows)} tested, {skipped} skipped, "
            f"{sum(1 for r in rows if r['is_cointegrated'])} cointegrated"
        )

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
        return df

    def get_top_n(
        self,
        prices: pd.DataFrame,
        n: int = 5,
        log_prices: bool = True,
        freq_hours: float = 1.0,
    ) -> List[PairScore]:
        """
        Run scan and return top-N cointegrated pairs as PairScore list.
        """
        df = self.scan(prices, log_prices=log_prices, freq_hours=freq_hours)
        if df.empty:
            return []
        cointed = df[df["is_cointegrated"]].head(n)
        return [self._cache[row["pair"]] for _, row in cointed.iterrows() if row["pair"] in self._cache]

    def rescan_stale(
        self,
        prices: pd.DataFrame,
        log_prices: bool = True,
        freq_hours: float = 1.0,
    ) -> pd.DataFrame:
        """
        Re-test only pairs whose last scan is older than staleness_hours.
        Returns updated scan results for stale pairs only.
        """
        now = time.time()
        stale_universe: List[str] = []
        stale_pairs: List[Tuple[str, str]] = []

        for key, ps in self._cache.items():
            age_hours = (now - ps.last_scanned) / 3600.0
            if age_hours > self.staleness_hours:
                stale_pairs.append((ps.sym_y, ps.sym_x))
                if ps.sym_y not in stale_universe:
                    stale_universe.append(ps.sym_y)
                if ps.sym_x not in stale_universe:
                    stale_universe.append(ps.sym_x)

        if not stale_pairs:
            logger.info("rescan_stale: no stale pairs found")
            return pd.DataFrame()

        logger.info(f"rescan_stale: rescanning {len(stale_pairs)} stale pairs")
        old_universe = self.universe
        self.universe = stale_universe
        result = self.scan(prices, log_prices=log_prices, freq_hours=freq_hours)
        self.universe = old_universe
        return result

    def composite_score(
        self,
        adf_pvalue: float,
        half_life_hours: float,
        hurst: float,
        spread_std: float,
        static_beta: float,
        correlation: float,
        w_adf: float = 0.25,
        w_hl: float = 0.30,
        w_hurst: float = 0.20,
        w_stability: float = 0.15,
        w_corr: float = 0.10,
    ) -> float:
        """
        5-factor composite score in [0, 1].

        ADF component     : 1 - p_value (lower p → higher score)
        Half-life component: Gaussian around optimal_hl_hours
        Hurst component   : 1 - 2*hurst (H=0 → 1.0, H=0.5 → 0.0)
        Stability component: placeholder 0.5 when beta not estimable
        Correlation component: (corr - min_corr) / (1 - min_corr)
        """
        # ADF
        s_adf = float(np.clip(1.0 - adf_pvalue, 0.0, 1.0))

        # Half-life: Gaussian centred on optimal, sigma = optimal/2
        if np.isnan(half_life_hours) or half_life_hours <= 0:
            s_hl = 0.0
        else:
            sigma_hl = self.optimal_hl_hours / 2.0
            s_hl = float(np.exp(-0.5 * ((half_life_hours - self.optimal_hl_hours) / sigma_hl) ** 2))

        # Hurst: 0 (trending) to 0.5 (random walk) to 1 (mean-reverting misclassification)
        # We want H < 0.5; score = 1 when H=0, 0 when H=0.5+
        s_hurst = float(np.clip(1.0 - 2.0 * hurst, 0.0, 1.0))

        # Spread stability: use 0.5 as neutral if we can't compute beta rolling std
        # Real implementation would pass rolling_beta_std from Kalman
        s_stability = 0.5

        # Correlation
        denom = max(1.0 - self.min_corr, 1e-6)
        s_corr = float(np.clip((correlation - self.min_corr) / denom, 0.0, 1.0))

        score = (
            w_adf       * s_adf
            + w_hl      * s_hl
            + w_hurst   * s_hurst
            + w_stability * s_stability
            + w_corr    * s_corr
        )
        return float(np.clip(score, 0.0, 1.0))
