"""
api/  —  QuantLuna REST API package (Sprint 16)

Routers:
  backtest  —  POST /api/backtest/run
               GET  /api/backtest/jobs/{job_id}
               GET  /api/backtest/jobs/{job_id}/trades.csv
               GET  /api/backtest/jobs

Mount:
  app.include_router(backtest_router, prefix="")  # in dashboard/server.py
"""
from api.backtest import router as backtest_router

__all__ = ["backtest_router"]
