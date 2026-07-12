"""
QuantLuna — Sizing API
Sprint S46 (base) | Sprint 33 (watchdog hooks) | Sprint 34 (SizingEngine)

Endpoints:
  POST /sizing/calculate            — calcul complet position size
  POST /sizing/kelly                — calcul Kelly fraction doar
  GET  /sizing/instrument/{sym}     — instrument info (qtyStep, minNotional)
  GET  /sizing/sizer_config         — configuratia curenta a sizer-ului
  GET  /sizing/live_status          — status live SizingEngine v2.5
  GET  /sizing/decision_status      — status live DecisionEngine v2.5 (alias compat)

  # Sprint 33 — Watchdog action hooks
  POST /sizing/reduce/{pair_id}     — reduce sizing o pereche (apelat de MonitoringWatchdog)
  GET  /sizing/reduce/history       — audit log REDUCE events

Callable programmatic (importat de MultiMarketOrchestrator):
  await reduce_pair_size(pair, factor)  — hook principal pentru reduce_callback
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from risk.bybit_position_sizer import BybitPositionSizer, SizingParams

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sizing", tags=["sizing"])

# ---------------------------------------------------------------------------
# Stare injectabila — populata din api/main.py la lifespan startup
# ---------------------------------------------------------------------------
_SIZING_STATE: Dict[str, Any] = {
    "sizing_engine":   None,
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


# ---------------------------------------------------------------------------
# Sprint 33 — Reduce registry (audit log)
# ---------------------------------------------------------------------------

@dataclass
class ReduceRecord:
    timestamp:  str
    pair:       str
    factor:     float
    success:    bool
    detail:     str = ""


_REDUCE_REGISTRY: List[ReduceRecord] = []
_MAX_REDUCE_HISTORY = 200


def _record_reduce(record: ReduceRecord) -> None:
    """Append record in _REDUCE_REGISTRY si trimite la max 200."""
    _REDUCE_REGISTRY.append(record)
    if len(_REDUCE_REGISTRY) > _MAX_REDUCE_HISTORY:
        del _REDUCE_REGISTRY[:-_MAX_REDUCE_HISTORY]


# ---------------------------------------------------------------------------
# reduce_pair_size() — importabil de MultiMarketOrchestrator.reduce_callback
# ---------------------------------------------------------------------------

async def reduce_pair_size(pair: str, factor: float = 0.5) -> None:
    """
    Reduce sizing-ul unei perechi active la `factor` din valoarea curenta.

    Apelat de MonitoringWatchdog via reduce_callback(pair, factor) din
    core/multi_market_orchestrator.py._make_reduce_callback().

    Strategie (cascada):
      1. SizingEngine injectat (_SIZING_STATE["sizing_engine"]) are set_pair_factor()  <- S34
      2. MultiPairManager are set_alloc_factor()
      3. Fallback: log WARNING — nu crash

    Args:
        pair:   ID pereche (ex: "BTCUSDT-ETHUSDT")
        factor: multiplicator sizing (0.5 = 50% din valoarea curenta)

    Raises:
        Nu ridica niciodata — esuarile sunt logate + inregistrate in ReduceRecord.
    """
    ts = datetime.now(timezone.utc).isoformat()
    pair_id = pair.replace("/", "-")
    factor  = max(0.0, min(1.0, factor))  # clamp [0, 1]

    # --- Cale 1: SizingEngine injectat (S34) are set_pair_factor() ---
    engine = _SIZING_STATE.get("sizing_engine")
    if engine is not None and hasattr(engine, "set_pair_factor"):
        try:
            engine.set_pair_factor(pair_id, factor)
            _record_reduce(ReduceRecord(
                timestamp=ts, pair=pair_id, factor=factor,
                success=True,
                detail=f"set_pair_factor({factor:.2f}) via SizingEngine (S34) OK",
            ))
            logger.info(
                "[reduce_pair_size] %s -> factor=%.2f via SizingEngine",
                pair_id, factor,
            )
            return
        except Exception as exc:
            logger.warning(
                "[reduce_pair_size] SizingEngine.set_pair_factor(%s, %.2f) failed: %s",
                pair_id, factor, exc,
            )

    # --- Cale 2: MultiPairManager are set_alloc_factor() ---
    try:
        from api.pairs import get_manager
        mgr = get_manager()
        if hasattr(mgr, "set_alloc_factor"):
            mgr.set_alloc_factor(pair_id, factor)
            _record_reduce(ReduceRecord(
                timestamp=ts, pair=pair_id, factor=factor,
                success=True,
                detail=f"set_alloc_factor({factor:.2f}) via MultiPairManager OK",
            ))
            logger.info(
                "[reduce_pair_size] %s -> factor=%.2f via MultiPairManager",
                pair_id, factor,
            )
            return
    except Exception as exc:
        logger.warning(
            "[reduce_pair_size] MultiPairManager.set_alloc_factor(%s) failed: %s",
            pair_id, exc,
        )

    # --- Fallback: log WARNING, nu crash ---
    logger.warning(
        "[reduce_pair_size] Niciun engine disponibil pentru %s factor=%.2f — "
        "sizing NESCHIMBAT. Injecteaza SizingEngine via set_sizing_state() "
        "sau implementeaza MultiPairManager.set_alloc_factor().",
        pair_id, factor,
    )
    _record_reduce(ReduceRecord(
        timestamp=ts, pair=pair_id, factor=factor,
        success=False,
        detail="Niciun engine disponibil (SizingEngine=None, MultiPairManager fara set_alloc_factor)",
    ))


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

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


class ReducePairRequest(BaseModel):
    factor: float = Field(default=0.5, ge=0.0, le=1.0, description="Factor sizing [0, 1]")


# ---------------------------------------------------------------------------
# REST Endpoints — existente
# ---------------------------------------------------------------------------

@router.post("/calculate")
def sizing_calculate(req: SizingRequest):
    """POST /sizing/calculate — Calculeaza marimea pozitiei (Kelly sau Fixed)."""
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
    """POST /sizing/kelly — Calculeaza doar Kelly fraction."""
    _SCALES = {"full": 1.0, "half": 0.5, "quarter": 0.25}
    sizer   = _get_sizer()
    raw_f   = sizer.kelly_fraction_raw(req.win_rate, req.avg_win_usd, req.avg_loss_usd)
    scale   = _SCALES[req.scale]
    return {
        "kelly_full":    round(raw_f, 6),
        "kelly_scaled":  round(raw_f * scale, 6),
        "scale":         req.scale,
        "win_rate":      req.win_rate,
        "profit_factor": round(req.avg_win_usd / req.avg_loss_usd, 4),
        "interpretation": (
            f"Alocare recomandata: {raw_f * scale:.1%} din capital"
            if raw_f > 0 else "Kelly negativ — nu deschide pozitie"
        ),
    }


@router.get("/instrument/{symbol}")
def instrument_info(symbol: str):
    """GET /sizing/instrument/BTCUSDT — qtyStep + minNotional."""
    mode = os.getenv("EXCHANGE_MODE", "paper")
    if mode != "live":
        defaults = {
            "BTCUSDT":  {"qty_step": 0.001, "min_notional": 5.0,  "tick_size": 0.1},
            "ETHUSDT":  {"qty_step": 0.01,  "min_notional": 5.0,  "tick_size": 0.05},
            "SOLUSDT":  {"qty_step": 0.1,   "min_notional": 1.0,  "tick_size": 0.01},
            "BNBUSDT":  {"qty_step": 0.01,  "min_notional": 5.0,  "tick_size": 0.01},
            "ADAUSDT":  {"qty_step": 1.0,   "min_notional": 1.0,  "tick_size": 0.0001},
            "DOGEUSDT": {"qty_step": 1.0,   "min_notional": 1.0,  "tick_size": 0.00001},
        }
        info = defaults.get(
            symbol.upper(),
            {"qty_step": 0.001, "min_notional": 5.0, "tick_size": 0.1},
        )
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
        "exchange":         os.getenv("EXCHANGE", "bybit"),
        "mode":             os.getenv("EXCHANGE_MODE", "paper"),
        "capital_usdt":     float(os.getenv("INITIAL_CAPITAL_USD", "10000")),
        "max_leverage":     float(os.getenv("MAX_LEVERAGE", "3.0")),
        "kelly_fraction":   os.getenv("KELLY_FRACTION", "half"),
        "max_position_pct": float(os.getenv("MAX_POSITION_PCT", "0.25")),
        "category":         os.getenv("BYBIT_CATEGORY", "linear"),
    }


@router.get("/live_status")
def live_status():
    """GET /sizing/live_status — status live SizingEngine v2.5."""
    engine = _SIZING_STATE.get("sizing_engine")
    if engine is None:
        return {
            "enabled": False,
            "source":  "none",
            "status":  "SizingEngine indisponibil — bot-ul nu a injectat engine-ul inca",
        }
    if hasattr(engine, "get_status"):
        return {"enabled": True, "source": "SizingEngine", **engine.get_status()}
    else:
        return {
            "enabled":        True,
            "source":         type(engine).__name__,
            "status":         "engine activ dar fara get_status()",
            "capital_usdt":   getattr(engine, "_capital",       None),
            "max_leverage":   getattr(engine, "_max_leverage",  None),
            "kelly_fraction": getattr(engine, "_kelly_fraction", None),
        }


@router.get("/decision_status")
def decision_status():
    """GET /sizing/decision_status — alias compat DecisionEngine v2.5."""
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


# ---------------------------------------------------------------------------
# REST Endpoints — Sprint 33 (Watchdog action hooks)
# ---------------------------------------------------------------------------

@router.post("/reduce/{pair_id:path}")
async def reduce_pair_endpoint(pair_id: str, req: ReducePairRequest = ReducePairRequest()):
    """
    POST /sizing/reduce/{pair_id}

    Reduce sizing-ul unei perechi la `factor` din valoarea curenta.
    Apelat de MonitoringWatchdog (indirect via reduce_pair_size() callable)
    sau manual din dashboard.

    Body JSON (optional):
        {"factor": 0.5}   — default 0.5 (50%)

    Nota: returneaza 200 chiar si la success=False (fire-and-forget async).
    Verifica campul `success` + `detail` din raspuns pentru statusul real.
    """
    await reduce_pair_size(pair=pair_id, factor=req.factor)
    last = _REDUCE_REGISTRY[-1] if _REDUCE_REGISTRY else None
    return {
        "ok":      True,
        "pair_id": pair_id,
        "factor":  req.factor,
        "success": last.success if last else None,
        "detail":  last.detail  if last else "",
    }


@router.get("/reduce/history")
def reduce_history(limit: int = 50):
    """GET /sizing/reduce/history — ultimele `limit` evenimente REDUCE_SIZE (audit log)."""
    records = _REDUCE_REGISTRY[-limit:]
    return {
        "count":   len(records),
        "records": [
            {
                "timestamp": r.timestamp,
                "pair":      r.pair,
                "factor":    r.factor,
                "success":   r.success,
                "detail":    r.detail,
            }
            for r in reversed(records)
        ],
    }
