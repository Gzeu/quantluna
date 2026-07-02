"""
QuantLuna — Live Trader API
Sprint 24

Endpoints:
  POST /live/start          — start live trader (paper or live mode)
  POST /live/stop           — graceful stop
  POST /live/emergency_stop — flatten + stop immediately
  GET  /live/status         — current trader status snapshot
  GET  /live/stream         — SSE stream of live bar events

SSE format (text/event-stream):
  event: bar
  data: {ts, spread, zscore, regime, signal, active_strategy, ...}

  event: status
  data: {state, mode, active_strategy, regime, pnl, ...}

  event: heartbeat
  data: {ts}

Usage:
  curl http://localhost:8000/live/stream
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live", tags=["live"])

# Global trader instance (one per process)
_TRADER: Optional[Any] = None
_SSE_QUEUE: asyncio.Queue = asyncio.Queue(maxsize=200)


class StartRequest(BaseModel):
    sym_y:       str   = Field(..., example="BTCUSDT")
    sym_x:       str   = Field(..., example="ETHUSDT")
    bar_freq:    str   = Field("1h")
    capital_usdt: float = Field(100.0, gt=0)
    mode:        str   = Field("paper", pattern="^(paper|live)$")
    selector_id: str   = Field("live")


@router.post("/start")
async def start_trader(req: StartRequest) -> Dict:
    """
    POST /live/start
    Start the live trader. Only one trader runs at a time.
    """
    global _TRADER
    if _TRADER is not None:
        s = _TRADER.status()
        if s.state == "running":
            raise HTTPException(status_code=409, detail="Trader already running. POST /live/stop first.")

    try:
        from config.strategy_config import StrategyConfig
    except ImportError:
        raise HTTPException(status_code=500, detail="config.strategy_config not available")

    import os
    os.environ["QUANTLUNA_LIVE_MODE"] = req.mode

    cfg = StrategyConfig(
        sym_y=req.sym_y, sym_x=req.sym_x,
        bar_freq=req.bar_freq, capital_usdt=req.capital_usdt,
    )

    def _on_bar_event(bar_y, bar_x):
        """Push bar event to SSE queue (from executor thread)."""
        try:
            ev = json.dumps({"type": "bar", "sym_y": bar_y.symbol, "sym_x": bar_x.symbol,
                             "ts": bar_y.timestamp.isoformat(), "close_y": bar_y.close,
                             "close_x": bar_x.close})
            _SSE_QUEUE.put_nowait(ev)
        except asyncio.QueueFull:
            pass

    from execution.live_trader import LiveTrader
    _TRADER = LiveTrader(cfg, selector_id=req.selector_id, on_bar=_on_bar_event)
    await _TRADER.start()

    return {"ok": True, "mode": req.mode, "pair": f"{req.sym_y}/{req.sym_x}",
            "bar_freq": req.bar_freq, "selector_id": req.selector_id}


@router.post("/stop")
async def stop_trader() -> Dict:
    global _TRADER
    if _TRADER is None:
        raise HTTPException(status_code=404, detail="No trader running")
    await _TRADER.stop()
    _TRADER = None
    return {"ok": True, "state": "stopped"}


@router.post("/emergency_stop")
async def emergency_stop() -> Dict:
    global _TRADER
    if _TRADER is None:
        raise HTTPException(status_code=404, detail="No trader running")
    await _TRADER.emergency_stop()
    _TRADER = None
    return {"ok": True, "state": "emergency_stopped"}


@router.get("/status")
def get_status() -> Dict:
    if _TRADER is None:
        return {"state": "idle", "mode": "paper", "note": "No trader running. POST /live/start to begin."}
    s = _TRADER.status()
    return {
        "state":           s.state,
        "mode":            s.mode,
        "sym_y":           s.sym_y,
        "sym_x":           s.sym_x,
        "bar_freq":        s.bar_freq,
        "active_strategy": s.active_strategy,
        "regime":          s.regime,
        "position_side":   s.position_side,
        "unrealised_pnl":  s.unrealised_pnl,
        "realised_pnl":    s.realised_pnl,
        "n_trades":        s.n_trades,
        "bars_processed":  s.bars_processed,
        "last_bar_ts":     s.last_bar_ts,
        "scores":          s.scores,
        "switch_history":  s.switch_history,
        "uptime_s":        s.uptime_s,
        "error":           s.error,
    }


@router.get("/stream")
async def sse_stream() -> StreamingResponse:
    """
    GET /live/stream
    Server-Sent Events stream of live bar + status events.

    curl http://localhost:8000/live/stream
    """
    async def _event_generator():
        # Initial status event
        status = get_status()
        yield f"event: status\ndata: {json.dumps(status)}\n\n"

        heartbeat_interval = 15.0  # seconds
        last_hb = asyncio.get_event_loop().time()

        while True:
            now = asyncio.get_event_loop().time()
            timeout = max(0.1, heartbeat_interval - (now - last_hb))
            try:
                data = await asyncio.wait_for(_SSE_QUEUE.get(), timeout=timeout)
                yield f"event: bar\ndata: {data}\n\n"
            except asyncio.TimeoutError:
                # Heartbeat
                hb = json.dumps({"ts": datetime.now(timezone.utc).isoformat()})
                yield f"event: heartbeat\ndata: {hb}\n\n"
                last_hb = asyncio.get_event_loop().time()

                # Push status every heartbeat
                status_data = json.dumps(get_status())
                yield f"event: status\ndata: {status_data}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
