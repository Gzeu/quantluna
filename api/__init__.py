"""
api/__init__.py — S37
Barrel router — include toate sub-routerele.
Folosit in dashboard/server.py sau in orice app FastAPI.

Usage:
    from api import build_api
    app = build_api()

    # Sau manual:
    from api.risk     import router as risk_router
    from api.pnl      import router as pnl_router
    from api.services import router as services_router
    app.include_router(risk_router)
    app.include_router(pnl_router)
    app.include_router(services_router)
"""
from __future__ import annotations

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
except ImportError:
    raise ImportError("fastapi este necesar: pip install fastapi uvicorn")

from api.risk     import router as risk_router
from api.pnl      import router as pnl_router
from api.services import services_router


def build_api(title: str = "QuantLuna API", version: str = "0.37.0") -> FastAPI:
    """
    Construieste aplicatia FastAPI cu toate routerele inregistrate.
    CORS permisiv pentru dev (restrange in productie via env ALLOWED_ORIGINS).
    """
    import os
    app = FastAPI(title=title, version=version)

    origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(risk_router)
    app.include_router(pnl_router)
    app.include_router(services_router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": version}

    return app
