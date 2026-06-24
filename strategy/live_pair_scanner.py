"""
QuantLuna — Live Pair Scanner Sprint 8

Scanner live pentru selecția de perechi candidate market-neutral.

Scop:
- Rulează periodic peste un univers de simboluri perp/spot
- Filtrează pe lichiditate minimă și disponibilitate date
- Scorează perechile după relație statistică robustă, nu doar corelație
- Prioritizează pair-urile cu cointegration plauzibilă și mean reversion utilă

Scoring curent:
- corelație rolling
- volatilitate spread
- stabilitate hedge ratio simplificat (rolling beta variance)
- half-life estimată aproximativ din spread AR(1)
- penalizare pentru funding net mare
- penalizare pentru lichiditate slabă

Notă importantă:
Acest scanner este un pre-filter operațional, nu validator final.
Un pair selectat aici trebuie trecut ulterior prin pipeline complet:
Engle-Granger / Johansen + Kalman Filter + walk-forward + Monte Carlo.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class PairScanConfig:
    min_history_bars: int = 300
    min_quote_volume_usd: float = 1_000_000.0
    max_pairs: int = 25
    corr_window: int = 100
    beta_window: int = 100
    max_half_life_bars: float = 72.0
    max_funding_net_annual: float = 0.15


@dataclass
class PairCandidate:
    sym_y: str
    sym_x: str
    score: float
    corr: float
    spread_vol: float
    beta_var: float
    half_life: float
    funding_net: float
    liquidity_score: float
    notes: str


class LivePairScanner:
    """
    Input așteptat:
    market_data = {
      "ETH/USDT:USDT": {"close": pd.Series, "quote_volume": pd.Series, "funding_annual": float},
      ...
    }
    """

    def __init__(self, cfg: Optional[PairScanConfig] = None) -> None:
        self.cfg = cfg or PairScanConfig()

    def scan(self, market_data: Dict[str, Dict]) -> List[PairCandidate]:
        symbols = [
            s for s, d in market_data.items()
            if len(d.get("close", [])) >= self.cfg.min_history_bars
            and float(pd.Series(d.get("quote_volume", [])).tail(20).mean() or 0.0) >= self.cfg.min_quote_volume_usd
        ]

        out: List[PairCandidate] = []
        for sym_y, sym_x in itertools.combinations(symbols, 2):
            cand = self._score_pair(sym_y, sym_x, market_data)
            if cand is not None:
                out.append(cand)

        out.sort(key=lambda x: x.score, reverse=True)
        return out[: self.cfg.max_pairs]

    def _score_pair(self, sym_y: str, sym_x: str, market_data: Dict[str, Dict]) -> Optional[PairCandidate]:
        y = pd.Series(market_data[sym_y]["close"]).astype(float).dropna().reset_index(drop=True)
        x = pd.Series(market_data[sym_x]["close"]).astype(float).dropna().reset_index(drop=True)
        n = min(len(y), len(x))
        if n < self.cfg.min_history_bars:
            return None
        y = y.iloc[-n:]
        x = x.iloc[-n:]

        # rolling correlation
        corr = float(y.tail(self.cfg.corr_window).corr(x.tail(self.cfg.corr_window)))
        if np.isnan(corr):
            return None

        # rolling beta variance
        beta = (y.rolling(self.cfg.beta_window).cov(x) / x.rolling(self.cfg.beta_window).var()).dropna()
        beta_var = float(beta.var()) if len(beta) else 999.0
        beta_last = float(beta.iloc[-1]) if len(beta) else 1.0

        # spread + half-life aprox pe beta ultim
        spread = y - beta_last * x
        spread_vol = float(spread.tail(self.cfg.corr_window).std() or 0.0)
        half_life = self._estimate_half_life(spread.tail(self.cfg.corr_window))

        funding_y = float(market_data[sym_y].get("funding_annual", 0.0) or 0.0)
        funding_x = float(market_data[sym_x].get("funding_annual", 0.0) or 0.0)
        funding_net = abs(funding_y - funding_x)

        vol_y = float(pd.Series(market_data[sym_y]["quote_volume"]).tail(20).mean() or 0.0)
        vol_x = float(pd.Series(market_data[sym_x]["quote_volume"]).tail(20).mean() or 0.0)
        liquidity_score = min(vol_y, vol_x) / self.cfg.min_quote_volume_usd

        # scoring robust, fără hype
        score = 0.0
        score += max(0.0, corr) * 40.0
        score += max(0.0, 1.0 / (1.0 + beta_var)) * 25.0
        score += max(0.0, 1.0 / (1.0 + spread_vol)) * 15.0
        score += max(0.0, 1.0 - min(half_life, self.cfg.max_half_life_bars) / self.cfg.max_half_life_bars) * 10.0
        score += min(liquidity_score, 5.0) * 2.0
        score -= min(funding_net / max(self.cfg.max_funding_net_annual, 1e-9), 3.0) * 8.0

        notes = []
        if corr < 0.7:
            notes.append("corr_weak")
        if beta_var > 0.5:
            notes.append("beta_unstable")
        if half_life > self.cfg.max_half_life_bars:
            notes.append("half_life_slow")
        if funding_net > self.cfg.max_funding_net_annual:
            notes.append("funding_expensive")

        return PairCandidate(
            sym_y=sym_y,
            sym_x=sym_x,
            score=round(score, 3),
            corr=round(corr, 4),
            spread_vol=round(spread_vol, 6),
            beta_var=round(beta_var, 6),
            half_life=round(half_life, 2),
            funding_net=round(funding_net, 6),
            liquidity_score=round(liquidity_score, 3),
            notes=",".join(notes),
        )

    def _estimate_half_life(self, spread: pd.Series) -> float:
        s = spread.dropna()
        if len(s) < 20:
            return 999.0
        lag = s.shift(1).dropna()
        delta = s.diff().dropna()
        n = min(len(lag), len(delta))
        lag = lag.iloc[-n:]
        delta = delta.iloc[-n:]
        if n < 10:
            return 999.0
        x = lag.to_numpy()
        y = delta.to_numpy()
        beta = np.polyfit(x, y, 1)[0]
        if beta >= 0:
            return 999.0
        hl = -np.log(2) / beta
        return float(max(0.0, hl))
