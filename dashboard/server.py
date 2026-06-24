"""
dashboard/server.py  —  QuantLuna Sprint 8

FastAPI app:
  GET  /        — serves dashboard/index.html
  GET  /state   — current snapshot (debug / bootstrap)
  WS   /ws      — live push stream (JSON snapshots from StateBus)

Run:
  uvicorn dashboard.server:app --host 0.0.0.0 --port 8765 --reload

For production:
  uvicorn dashboard.server:app --host 0.0.0.0 --port 8765 --workers 1
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from state_bus import bus

logger = logging.getLogger(__name__)

DASHBOARD_DIR = Path(__file__).parent
INDEX_HTML = DASHBOARD_DIR / "index.html"

app = FastAPI(
    title="QuantLuna Dashboard",
    version="8.0.0",
    docs_url=None,
    redoc_url=None,
)

_static = DASHBOARD_DIR / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")


@app.get("/")
async def serve_dashboard():
    """Serve the live dashboard HTML."""
    if not INDEX_HTML.exists():
        return JSONResponse(
            {"error": "dashboard/index.html not found"},
            status_code=503,
        )
    return FileResponse(str(INDEX_HTML), media_type="text/html")


@app.get("/state")
async def get_state():
    """Current snapshot — useful for bootstrap and debugging."""
    return JSONResponse(bus.snapshot_dict())


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    """
    Push live StateBus snapshots to connected dashboard clients.
    Each message is a full JSON snapshot (not a diff).
    """
    await websocket.accept()
    client = websocket.client
    logger.info(f"WS client connected: {client}")
    try:
        async for snapshot in bus.subscribe():
            await websocket.send_json(snapshot)
            await asyncio.sleep(0)
    except WebSocketDisconnect:
        logger.info(f"WS client disconnected: {client}")
    except Exception as exc:
        logger.warning(f"WS error for {client}: {exc}")


async def start_dashboard(host: str = "0.0.0.0", port: int = 8765):
    """
    Launch the dashboard server as an asyncio task alongside LiveTrader.
    """
    import uvicorn

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    await server.serve()
