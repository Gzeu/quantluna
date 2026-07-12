"""
QuantLuna — Sizing API
Sprint S46 (2026-07-12): live_status + decision_status pentru dashboard unificat

Endpoints:
  POST /sizing/calculate          — calcul complet position size
  POST /sizing/kelly              — calcul Kelly fraction doar
  GET  /sizing/instrument/{sym}   — instrument info (qtyStep, minNotional)
  GET  /sizing/sizer_config       — configuratia curenta a sizer-ului
  GET  /sizing/live_status        — status live SizingEngine v2.5
  GET  /sizing/decision_status    — status live DecisionEngine v2.5 (alias compat)
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from risk.bybit_position_sizer import BybitPositionSizer, SizingParams

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sizing", tags=["sizing"])

# ---------------------------------------------------------------------------
# Stare injectabila — populata din api/main.py la lifespan startup
# ---------------------------------------------------------------------------
_SIZING_STATE: Dict[str, Any] = {
    "sizing_engine": None,
    "decision_engine": None,
}


def set_sizing_state(state: Dict[str, Any]) -> None:
    """Injectat din api/main.py la lifespan startup."""
    _SIZING_STATE.update(state or {})


# ---------------------------------------------------------------------------
# Helper intern
# ---------------------------------------------------------------------------

def _get_sizer() -> BybitPositionSizer:
    return BybitPositionSizer(
        capital_usdt=float(os.getenv("INITIAL_CAPITAL_USD", "10000")),
        max_leverage=float(os.getenv("MAX_LEVERAGE", "3.0")),
        kelly_fraction=os.getenv("KELLY_FRACTION", "half"),
        max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.25")),
    )


# --- Request / Response models ---

class SizingRequest(BaseModel):
    symbol:        str   = Field(..., example="BTCUSDT")
    entry_price:   float = Field(..., example=65000.0)
    win_rate:      float = Field(..., ge=0.0, le=1.0, example=0.55)
    avg_win_usd:   float = Field(..., gt=0, example=120.0)
    avg_loss_usd:  float = Field(..., gt=0, example=80.0)
    leverage:      float = Field(default=1.0, ge=1.0, le=100.0)
    qty_step:      float = Field(default=0.001)
    contract_size: float = Field(default=1.0)
    method:        str   = Field(default="kelly", pattern="^(kelly|fixed)$")
    override_fraction: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class KellyRequest(BaseModel):
    win_rate:     float = Field(..., ge=0.0, le=1.0)
    avg_win_usd:  float = Field(..., gt=0)
    avg_loss_usd: float = Field(..., gt=0)
    scale:        str   = Field(default="half", pattern="^(full|half|quarter)$")


# --- Endpoints ---

@router.post("/calculate")
def sizing_calculate(req: SizingRequest):
    """
    POST /sizing/calculate
    Calculeaza marimea pozitiei (Kelly sau Fixed) pentru Bybit linear.
    """
    sizer  = _get_sizer()
    params = SizingParams(
        symbol=req.symbol,
        entry_price=req.entry_price,
        win_rate=req.win_rate,
        avg_win_usd=req.avg_win_usd,
        avg_loss_usd=req.avg_loss_usd,
        leverage=req.leverage,
        qty_step=req.qty_step,
        contract_size=req.contract_size,
        override_fraction=req.override_fraction,
    )
    try:
        result = sizer.calculate(params, method=req.method)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    return result.to_dict()


@router.post("/kelly")
def kelly_only(req: KellyRequest):
    """
    POST /sizing/kelly
    Calculeaza doar Kelly fraction (fara sizing complet).
    """
    _SCALES = {"full": 1.0, "half": 0.5, "quarter": 0.25}
    sizer   = _get_sizer()
    raw_f   = sizer.kelly_fraction_raw(req.win_rate, req.avg_win_usd, req.avg_loss_usd)
    scale   = _SCALES[req.scale]
    return {
        "kelly_full":   round(raw_f, 6),
        "kelly_scaled": round(raw_f * scale, 6),
        "scale":        req.scale,
        "win_rate":     req.win_rate,
        "profit_factor": round(req.avg_win_usd / req.avg_loss_usd, 4),
        "interpretation": (
            f"Alocare recomandata: {raw_f * scale:.1%} din capital"
            if raw_f > 0 else "Kelly negativ — nu deschide pozitie"
        ),
    }


@router.get("/instrument/{symbol}")
def instrument_info(symbol: str):
    """
    GET /sizing/instrument/BTCUSDT
    Returneaza qtyStep + minNotional din Bybit (live mode) sau valori default.
    """
    mode = os.getenv("EXCHANGE_MODE", "paper")
    if mode != "live":
        defaults = {
            "BTCUSDT":  {"qty_step": 0.001, "min_notional": 5.0, "tick_size": 0.1},
            "ETHUSDT":  {"qty_step": 0.01,  "min_notional": 5.0, "tick_size": 0.05},
            "SOLUSDT":  {"qty_step": 0.1,   "min_notional": 1.0, "tick_size": 0.01},
            "BNBUSDT":  {"qty_step": 0.01,  "min_notional": 5.0, "tick_size": 0.01},
            "ADAUSDT":  {"qty_step": 1.0,   "min_notional": 1.0, "tick_size": 0.0001},
            "DOGEUSDT": {"qty_step": 1.0,   "min_notional": 1.0, "tick_size": 0.00001},
        }
        info = defaults.get(symbol.upper(), {"qty_step": 0.001, "min_notional": 5.0, "tick_size": 0.1})
        return {"symbol": symbol.upper(), "source": "default", **info}
    try:
        from execution.bybit_order_router import BybitOrderRouter
        import asyncio
        router_inst = BybitOrderRouter(mode="live")
        step = asyncio.run(router_inst._get_qty_step(symbol))
        return {"symbol": symbol.upper(), "source": "bybit_live", "qty_step": step}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Bybit instrument info failed: {e}")


@router.get("/sizer_config")
def sizer_config():
    """GET /sizing/sizer_config — configuratia curenta a sizer-ului din env."""
    return {
        "exchange":        os.getenv("EXCHANGE", "bybit"),
        "mode":            os.getenv("EXCHANGE_MODE", "paper"),
        "capital_usdt":    float(os.getenv("INITIAL_CAPITAL_USD", "10000")),
        "max_leverage":    float(os.getenv("MAX_LEVERAGE", "3.0")),
        "kelly_fraction":  os.getenv("KELLY_FRACTION", "half"),
        "max_position_pct": float(os.getenv("MAX_POSITION_PCT", "0.25")),
        "category":        os.getenv("BYBIT_CATEGORY", "linear"),
    }


@router.get("/live_status")
def live_status():
    """
    GET /sizing/live_status
    Status live SizingEngine v2.5: streak, drawdown, kelly cap, ultimul multiplier.
    Returneaza enabled=False daca engineul nu a fost injectat inca.
    """
    engine = _SIZING_STATE.get("sizing_engine")
    if engine is None:
        return {
            "enabled": False,
            "source":  "none",
            "status":  "SizingEngine indisponibil — bot-ul nu a injectat engine-ul inca",
        }

    if hasattr(engine, "get_status"):
        return {
            "enabled": True,
            "source":  "SizingEngine",
            **engine.get_status(),
        }

    # Engine exista dar nu are get_status() — expunem ce putem
    return {
        "enabled": True,
        "source":  type(engine).__name__,
        "status":  "engine activ dar fara get_status()",
        "capital_usdt":    getattr(engine, "_capital", None),
        "max_leverage":    getattr(engine, "_max_leverage", None),
        "kelly_fraction":  getattr(engine, "_kelly_fraction", None),
    }


@router.get("/decision_status")
def decision_status():
    """
    GET /sizing/decision_status
    Alias compatibil — status live DecisionEngine v2.5.
    Dashboard-ul nou trebuie sa foloseasca /api/decision/status.
    Acest endpoint ramane pentru compatibilitate si debugging.
    """
    engine = _SIZING_STATE.get("decision_engine")
    if engine is None:
        return {
            "enabled": False,
            "status":  "DecisionEngine indisponibil — bot-ul nu a injectat engine-ul inca",
        }

    return {
        "enabled":             True,
        "entry_zscore":        getattr(engine, "_entry_z",       None),
        "exit_zscore":         getattr(engine, "_exit_z",        None),
        "partial_exit_zscore": getattr(engine, "_partial_z",     None),
        "scale_in_zscore":     getattr(engine, "_scale_z",       None),
        "base_qty_y":          getattr(engine, "_base_qty_y",    None),
        "base_qty_x":          getattr(engine, "_base_qty_x",    None),
        "current_streak":      getattr(engine, "_current_streak", 0),
        "current_drawdown":    getattr(engine, "_current_dd",    0.0),
        "in_position":         getattr(engine, "_in_position",   False),
    }
