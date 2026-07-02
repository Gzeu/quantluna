"""
QuantLuna — Strategy Router
Sprint 20 + Sprint 22 (SelectorStore)

Endpoints:
  GET  /strategy/scores            — scores_summary() din selectorul activ
  GET  /strategy/list              — toate strategiile disponibile + versiune
  POST /strategy/switch            — forteaza switch manual
  GET  /strategy/context/{job_id}  — MarketContext la ultimul bar din job

Sprint 22: _SELECTORS dict inlocuit cu SelectorStore (Redis/SQLite/memory).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.store import SelectorStore
from strategy.auto_selector import AutoStrategySelector
from strategy.bb_mean_reversion import BollingerBandsMeanReversion
from strategy.funding_arb import FundingRateArbitrage
from strategy.zscore_momentum import ZScoreMomentum

router = APIRouter(prefix="/strategy", tags=["strategy"])

# Sprint 22: SelectorStore replaces raw _SELECTORS dict
_STORE: SelectorStore = SelectorStore()


def _default_factory() -> AutoStrategySelector:
    return AutoStrategySelector(
        strategies=[
            BollingerBandsMeanReversion(window=20, n_std_entry=2.0),
            ZScoreMomentum(entry_threshold=1.5),
            FundingRateArbitrage(entry_funding_annual=0.20),
        ],
        hysteresis_bonus=0.10,
        min_score_threshold=0.30,
    )


# ---------------------------------------------------------------------------
# Backward-compat shims (used by tests + api/main.py imports)
# ---------------------------------------------------------------------------

def register_selector(job_id: str, selector: AutoStrategySelector) -> None:
    """Called by backtest job runner to register selector for /context endpoint."""
    _STORE.set(job_id, selector)


def clear_selector(job_id: str) -> None:
    _STORE.delete(job_id)


# Internal: used only by tests to reset state
def _reset_store() -> None:
    _STORE.clear()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SwitchRequest(BaseModel):
    strategy_name: str = Field(...)
    selector_id: str = Field("live")


class ScoresResponse(BaseModel):
    active_strategy: str
    scores: Dict[str, float]
    recent_win_rate: float
    switch_history: list
    total_bars: int
    selector_id: str


class StrategyInfo(BaseModel):
    name: str
    version: str
    description: str


class StrategyListResponse(BaseModel):
    strategies: List[StrategyInfo]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/scores", response_model=ScoresResponse)
def get_scores(selector_id: str = "live") -> ScoresResponse:
    sel = _STORE.get_or_create(selector_id, _default_factory)
    s = sel.scores_summary()
    return ScoresResponse(
        active_strategy=s["active_strategy"] or "none",
        scores=s["scores"],
        recent_win_rate=s["recent_win_rate"],
        switch_history=s["switch_history"],
        total_bars=s["total_bars"],
        selector_id=selector_id,
    )


@router.get("/list", response_model=StrategyListResponse)
def list_strategies(selector_id: str = "live") -> StrategyListResponse:
    _D = {
        "KalmanPairsTrading": "Flagship Kalman filter pairs trading. Best ranging + strong cointegration.",
        "BollingerBandsMeanReversion": "Bollinger Bands on spread. Fast, no warm-up. Best ranging + normal vol.",
        "ZScoreMomentum": "Trend-following on z-score. Best trending/breakout regimes.",
        "FundingRateArbitrage": "Carry on perpetual funding. Active only above 20%/year.",
    }
    sel = _STORE.get_or_create(selector_id, _default_factory)
    items = [StrategyInfo(name=s.name, version=s.version, description=_D.get(s.name, "")) for s in sel.strategies]
    return StrategyListResponse(strategies=items, total=len(items))


@router.post("/switch")
def switch_strategy(req: SwitchRequest) -> Dict[str, Any]:
    sel = _STORE.get_or_create(req.selector_id, _default_factory)
    target = next((s for s in sel.strategies if s.name == req.strategy_name), None)
    if target is None:
        raise HTTPException(
            status_code=404,
            detail=f"Strategy '{req.strategy_name}' not found. Available: {[s.name for s in sel.strategies]}"
        )
    old_name = sel._active_name
    if sel._active_strategy is not None and sel._active_name != req.strategy_name:
        sel._active_strategy.reset()
        sel._switch_history.append({
            "from": old_name, "to": req.strategy_name,
            "timestamp": None, "scores": dict(sel._last_scores), "manual": True,
        })
    sel._active_strategy = target
    sel._active_name = req.strategy_name
    sel._switch_cooldown_remaining = sel.switch_cooldown_bars
    _STORE.set(req.selector_id, sel)  # persist updated selector
    return {"ok": True, "switched_from": old_name or "none", "switched_to": req.strategy_name, "selector_id": req.selector_id}


@router.get("/context/{job_id}")
def get_context(job_id: str) -> Dict[str, Any]:
    sel = _STORE.get(job_id)
    if sel is None:
        # Try persisted summary (cross-process)
        summary = _STORE.get_summary(job_id)
        if summary:
            return {"job_id": job_id, **summary, "source": "persisted_summary"}
        return {"job_id": job_id, "context": {}, "note": "No selector found for this job_id. Run a new backtest to populate."}
    s = sel.scores_summary()
    return {"job_id": job_id, "active_strategy": s["active_strategy"], "scores": s["scores"], "total_bars": s["total_bars"], "switch_history": s["switch_history"]}
