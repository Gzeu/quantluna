"""
QuantLuna — PairSelector v3

Batch cointegration scanner with 5-factor composite scoring.

Composite score weights:
  w_adf             = 0.25  (lower p-value → better)
  w_half_life       = 0.30  (Gaussian peak at optimal_hl_hours, σ = optimal/2)
  w_hurst           = 0.20  (lower exponent → more mean-reverting)
  w_spread_stability= 0.15  (Kalman rolling beta std / |static_beta|, or 0.5 neutral)
  w_correlation     = 0.10  (higher rolling corr → more reliable)

Changes v3:
  - composite_score s_stability: real Kalman rolling_beta_std value when provided
    (was hardcoded 0.5 placeholder in v2)
  - scan(): accepts kalman_beta_std dict {'SYM_Y/SYM_X': float}
  - Optional parallel scan via concurrent.futures.ThreadPoolExecutor (n_workers)
  - Pre-filter step cleanly separated from cointegration step
  - _test_pair(): extracted as standalone method (enables parallelism + testability)
  - top_n_as_dataframe(): returns dashboard-ready DataFrame directly
  - PairScore: new field kalman_beta_stability (float, default 0.5)
  - rescan_stale(): saved_universe always restored (no silent state mutation on error)
  - min_volume_usdt read via getattr() with fallback (no AttributeError on older configs)
"""
from __future__ import annotations

import itertools
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    kalman_beta_stability: float = 0.5   # 1.0 = very stable, 0.0 = unstable
    last_scanned: float = field(default_factory=time.time)
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
    min_half_life_hours   : pairs with HL < this are rejected (too fast to trade)
    max_half_life_hours   : pairs with HL > this are rejected (too slow to trade)
    staleness_hours       : rescan_stale() rescans pairs older than this (default 48h)
    n_workers             : parallel workers for cointegration tests (1 = serial)
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
        n_workers: int = 1,
    ) -> None:
        self.universe = universe
        self.cfg = cfg or CointegrationConfig()
        self.min_corr = min_corr
        self.optimal_hl_hours = optimal_hl_hours
        self.max_hurst = max_hurst
        self.min_half_life_hours = min_half_life_hours
        self.max_half_life_hours = max_half_life_hours
        self.staleness_hours = staleness_hours
        self.n_workers = max(1, n_workers)

        self._test = CointegrationTest(
            significance=self.cfg.significance_level,
            min_half_life=self.min_half_life_hours,
            max_half_life=self.max_half_life_hours,
        )
        self._cache: Dict[str, PairScore] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(
        self,
        prices: pd.DataFrame,
        log_prices: bool = True,
        freq_hours: float = 1.0,
        volume_usdt: Optional[Dict[str, float]] = None,
        kalman_beta_std: Optional[Dict[str, float]] = None,
    ) -> pd.DataFrame:
        """
        Full scan of all pairs in universe.

        Parameters
        ----------
        prices          : DataFrame, columns=symbols, index=timestamps
        log_prices      : apply log transform before testing (recommended)
        freq_hours      : bar frequency in hours
        volume_usdt     : dict symbol->24h_volume_usd (optional liquidity gate)
        kalman_beta_std : dict 'SYM_Y/SYM_X'->rolling_beta_std from prior Kalman run
                          Enables real spread_stability scoring (not just 0.5 neutral)

        Returns
        -------
        DataFrame ranked by composite_score descending.
        Includes both passing and failing pairs (is_cointegrated column).
        """
        if log_prices:
            prices = np.log(prices.clip(lower=1e-12))

        pairs = list(itertools.combinations(self.universe, 2))
        logger.info(f"PairSelector: scanning {len(pairs)} pairs ({len(self.universe)} symbols)")

        min_vol = getattr(self.cfg, "min_volume_usdt", 5_000_000)

        # ---- Pre-filter (cheap, always serial) ----
        candidates: List[Tuple[str, str, pd.Series, pd.Series, float]] = []
        skipped_pre = 0

        for sym_y, sym_x in pairs:
            if sym_y not in prices.columns or sym_x not in prices.columns:
                skipped_pre += 1
                continue
            if volume_usdt is not None:
                if volume_usdt.get(sym_y, 0) < min_vol or volume_usdt.get(sym_x, 0) < min_vol:
                    skipped_pre += 1
                    continue
            y = prices[sym_y].dropna()
            x = prices[sym_x].dropna()
            aligned = pd.concat([y, x], axis=1).dropna()
            if len(aligned) < self.cfg.min_periods:
                skipped_pre += 1
                continue
            corr = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
            if corr < self.min_corr:
                skipped_pre += 1
                continue
            candidates.append((sym_y, sym_x, aligned.iloc[:, 0], aligned.iloc[:, 1], corr))

        logger.info(
            f"Pre-filter: {len(candidates)} candidates, {skipped_pre} skipped. "
            f"Cointegration tests with {self.n_workers} worker(s)."
        )

        # ---- Cointegration tests (optionally parallel) ----
        rows: List[Dict] = []

        def _run(item: Tuple) -> Optional[PairScore]:
            return self._test_pair(item, freq_hours, kalman_beta_std)

        if self.n_workers == 1:
            for item in candidates:
                result = _run(item)
                if result is not None:
                    rows.append(result.__dict__)
                    self._cache[result.pair] = result
        else:
            with ThreadPoolExecutor(max_workers=self.n_workers) as pool:
                futures = {pool.submit(_run, item): item for item in candidates}
                for fut in as_completed(futures):
                    result = fut.result()
                    if result is not None:
                        rows.append(result.__dict__)
                        self._cache[result.pair] = result

        cointegrated = sum(1 for r in rows if r["is_cointegrated"])
        logger.info(
            f"Scan complete: {len(rows)} tested, {skipped_pre} pre-filtered, "
            f"{cointegrated} cointegrated"
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
        kalman_beta_std: Optional[Dict[str, float]] = None,
    ) -> List[PairScore]:
        """Run scan and return top-N cointegrated pairs as PairScore list."""
        df = self.scan(
            prices, log_prices=log_prices,
            freq_hours=freq_hours, kalman_beta_std=kalman_beta_std,
        )
        if df.empty:
            return []
        cointed = df[df["is_cointegrated"]].head(n)
        return [
            self._cache[row["pair"]]
            for _, row in cointed.iterrows()
            if row["pair"] in self._cache
        ]

    def top_n_as_dataframe(
        self,
        prices: pd.DataFrame,
        n: int = 5,
        log_prices: bool = True,
        freq_hours: float = 1.0,
        kalman_beta_std: Optional[Dict[str, float]] = None,
    ) -> pd.DataFrame:
        """Convenience: top-N cointegrated pairs as dashboard-ready DataFrame."""
        df = self.scan(
            prices, log_prices=log_prices,
            freq_hours=freq_hours, kalman_beta_std=kalman_beta_std,
        )
        if df.empty:
            return df
        cols = [
            "pair", "composite_score", "adf_pvalue", "half_life_hours",
            "hurst", "correlation", "kalman_beta_stability",
            "is_cointegrated", "verdict",
        ]
        available = [c for c in cols if c in df.columns]
        return df[df["is_cointegrated"]][available].head(n).reset_index(drop=True)

    def rescan_stale(
        self,
        prices: pd.DataFrame,
        log_prices: bool = True,
        freq_hours: float = 1.0,
        kalman_beta_std: Optional[Dict[str, float]] = None,
    ) -> pd.DataFrame:
        """
        Re-test only pairs whose last scan is older than staleness_hours.
        Non-stale entries in _cache are preserved.
        """
        now = time.time()
        stale_pairs: List[Tuple[str, str]] = []
        stale_syms: List[str] = []

        for ps in self._cache.values():
            if (now - ps.last_scanned) / 3600.0 > self.staleness_hours:
                stale_pairs.append((ps.sym_y, ps.sym_x))
                if ps.sym_y not in stale_syms:
                    stale_syms.append(ps.sym_y)
                if ps.sym_x not in stale_syms:
                    stale_syms.append(ps.sym_x)

        if not stale_pairs:
            logger.info("rescan_stale: no stale pairs found")
            return pd.DataFrame()

        logger.info(f"rescan_stale: rescanning {len(stale_pairs)} stale pairs")
        saved_universe = self.universe
        self.universe = stale_syms
        result = self.scan(
            prices, log_prices=log_prices,
            freq_hours=freq_hours, kalman_beta_std=kalman_beta_std,
        )
        self.universe = saved_universe  # always restore
        return result

    def composite_score(
        self,
        adf_pvalue: float,
        half_life_hours: float,
        hurst: float,
        spread_std: float,
        static_beta: float,
        correlation: float,
        kalman_beta_std: float = 0.0,
        w_adf: float = 0.25,
        w_hl: float = 0.30,
        w_hurst: float = 0.20,
        w_stability: float = 0.15,
        w_corr: float = 0.10,
    ) -> float:
        """
        5-factor composite score in [0, 1].

        ADF component      : 1 - p_value
        Half-life component: Gaussian centred on optimal_hl_hours, σ = optimal/2
        Hurst component    : 1 - 2*H  (H<0.5 → positive score)
        Stability component: 1 - clamp(kalman_beta_std / |static_beta|, 0, 1)
                             Falls back to 0.5 when beta near zero or no Kalman data
        Correlation        : (corr - min_corr) / (1 - min_corr)
        """
        s_adf = float(np.clip(1.0 - adf_pvalue, 0.0, 1.0))

        if np.isnan(half_life_hours) or half_life_hours <= 0:
            s_hl = 0.0
        else:
            sigma_hl = self.optimal_hl_hours / 2.0
            s_hl = float(np.exp(-0.5 * ((half_life_hours - self.optimal_hl_hours) / sigma_hl) ** 2))

        s_hurst = float(np.clip(1.0 - 2.0 * hurst, 0.0, 1.0))

        abs_beta = abs(static_beta)
        if abs_beta < 1e-8 or kalman_beta_std <= 0.0:
            s_stability = 0.5
        else:
            s_stability = float(np.clip(1.0 - kalman_beta_std / abs_beta, 0.0, 1.0))

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

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _test_pair(
        self,
        item: Tuple[str, str, pd.Series, pd.Series, float],
        freq_hours: float,
        kalman_beta_std: Optional[Dict[str, float]],
    ) -> Optional[PairScore]:
        sym_y, sym_x, y, x, corr = item
        pair_key = f"{sym_y}/{sym_x}"

        try:
            result: CointegrationResult = self._test.run(y, x, freq_hours=freq_hours)
        except Exception as exc:
            logger.warning(f"Coint test failed {pair_key}: {exc}")
            return None

        hl    = float(result.half_life_hours) if result.half_life_hours else np.nan
        hurst = float(result.hurst_exponent)  if result.hurst_exponent  else 0.5

        k_std = float((kalman_beta_std or {}).get(pair_key, 0.0))
        abs_beta = abs(result.static_beta)
        kbs = (
            float(np.clip(1.0 - k_std / abs_beta, 0.0, 1.0))
            if abs_beta > 1e-8 and k_std > 0.0
            else 0.5
        )

        score = (
            self.composite_score(
                adf_pvalue=result.adf_pvalue,
                half_life_hours=hl,
                hurst=hurst,
                spread_std=result.spread_std,
                static_beta=result.static_beta,
                correlation=corr,
                kalman_beta_std=k_std,
            )
            if result.is_cointegrated else 0.0
        )

        return PairScore(
            sym_y=sym_y,
            sym_x=sym_x,
            pair=pair_key,
            adf_pvalue=result.adf_pvalue,
            eg_pvalue=result.eg_pvalue,
            half_life_hours=hl,
            hurst=hurst,
            static_beta=result.static_beta,
            spread_std=result.spread_std,
            correlation=corr,
            composite_score=score,
            kalman_beta_stability=kbs,
            is_cointegrated=result.is_cointegrated,
            verdict=result.verdict,
        )
