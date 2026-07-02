"""
QuantLuna — Data API
Sprint 26

Endpoints:
  GET  /data/ohlcv                        — fetch OHLCV (cached or download)
  GET  /data/pair                         — fetch aligned pair close prices
  GET  /data/cache/list                   — list cached datasets
  GET  /data/cache/stats                  — cache statistics
  DELETE /data/cache/{symbol}             — delete symbol cache
  POST /data/prefetch                     — background prefetch job
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/data", tags=["data"])

# Lazy singleton
_CACHE_STORE = None


def _get_store():
    global _CACHE_STORE
    if _CACHE_STORE is None:
        from data.cache_store import CacheStore
        _CACHE_STORE = CacheStore()
    return _CACHE_STORE


class PrefetchRequest(BaseModel):
    symbol:   str
    interval: str = "1h"
    start:    Optional[str] = None
    end:      Optional[str] = None


@router.get("/ohlcv")
def get_ohlcv(
    symbol:        str   = Query(..., example="BTCUSDT"),
    interval:      str   = Query("1h"),
    start:         Optional[str] = Query(None, example="2024-01-01"),
    end:           Optional[str] = Query(None, example="2024-06-30"),
    force_refresh: bool  = Query(False),
    max_rows:      int   = Query(5000, le=50000),
):
    """
    GET /data/ohlcv?symbol=BTCUSDT&interval=1h&start=2024-01-01&end=2024-06-30
    Returns OHLCV data as JSON. Cached locally after first fetch.
    """
    try:
        df = _get_store().fetch(symbol, interval, start, end, force_refresh)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {e}")
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data for {symbol} {interval} {start}→{end}")
    df_out = df.tail(max_rows).copy()
    # Serialize timestamps
    for col in ["open_time", "close_time"]:
        if col in df_out.columns:
            df_out[col] = df_out[col].astype(str)
    return {
        "symbol":   symbol.upper(),
        "interval": interval,
        "n_bars":   len(df_out),
        "columns":  [c for c in df_out.columns],
        "data":     df_out.to_dict(orient="records"),
    }


@router.get("/pair")
def get_pair(
    sym_y:         str  = Query(..., example="BTCUSDT"),
    sym_x:         str  = Query(..., example="ETHUSDT"),
    interval:      str  = Query("1h"),
    start:         Optional[str] = Query(None),
    end:           Optional[str] = Query(None),
    force_refresh: bool = Query(False),
):
    """
    GET /data/pair?sym_y=BTCUSDT&sym_x=ETHUSDT&interval=1h
    Returns aligned close price arrays for both symbols.
    Useful for direct input to /optimize/walk_forward.
    """
    try:
        y, x = _get_store().fetch_pair_for_optimizer(
            sym_y=sym_y, sym_x=sym_x, interval=interval,
            start=start, end=end, force_refresh=force_refresh,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {e}")
    return {
        "sym_y":    sym_y.upper(), "sym_x": sym_x.upper(),
        "interval": interval,     "n_bars": len(y),
        "close_y":  y.tolist(),   "close_x": x.tolist(),
    }


@router.get("/cache/list")
def list_cache():
    """GET /data/cache/list — list all cached datasets."""
    return {"datasets": _get_store().list_cache()}


@router.get("/cache/stats")
def cache_stats():
    """GET /data/cache/stats — cache disk + bar statistics."""
    return _get_store().stats()


@router.delete("/cache/{symbol}")
def delete_cache(
    symbol:   str,
    interval: Optional[str] = Query(None),
):
    """DELETE /data/cache/{symbol} — purge cached files for symbol."""
    n = _get_store().delete_cache(symbol, interval)
    return {"deleted_files": n, "symbol": symbol.upper()}


@router.post("/prefetch")
async def prefetch(
    req: PrefetchRequest,
    background_tasks: BackgroundTasks,
):
    """
    POST /data/prefetch — start background download job.
    Returns immediately; fetch runs in background.
    """
    def _do_fetch():
        try:
            _get_store().fetch(req.symbol, req.interval, req.start, req.end, force_refresh=True)
            logger.info(f"Prefetch done: {req.symbol} {req.interval}")
        except Exception as e:
            logger.error(f"Prefetch failed: {e}")
    background_tasks.add_task(_do_fetch)
    return {"ok": True, "symbol": req.symbol.upper(), "interval": req.interval, "status": "prefetching"}
