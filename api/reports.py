"""
Module: api/reports.py
Sprint: 31 — S (Strategy Backtesting Report)
Description:
    FastAPI router for generating, listing and downloading backtest
    reports (HTML / PDF).
    Endpoints:
        POST /reports/backtest  — generate report from backtest_id or payload
        GET  /reports/list      — list saved reports
        GET  /reports/{id}      — download report by ID

Usage:
    from api.reports import router as reports_router
    app.include_router(reports_router, prefix="/reports", tags=["reports"])
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from reporting.report_builder import ReportBuilder

logger = logging.getLogger(__name__)
router = APIRouter()

REPORTS_DIR = Path(os.getenv("REPORTS_DIR", "data/reports"))


class GenerateReportRequest(BaseModel):
    backtest_id: str | None = None
    payload: dict[str, Any] | None = None
    format: Literal["html", "pdf"] = "html"


class ReportMeta(BaseModel):
    id: str
    path: str
    format: str
    size_bytes: int


@router.post("/backtest", response_class=HTMLResponse)
async def generate_report(req: GenerateReportRequest) -> Any:
    """Generate backtest report from payload or backtest_id."""
    if req.payload is None and req.backtest_id is None:
        raise HTTPException(status_code=400, detail="Provide payload or backtest_id")
    result: dict[str, Any] = req.payload or _load_backtest(req.backtest_id)  # type: ignore[arg-type]
    report_id = req.backtest_id or str(uuid.uuid4())[:8]
    rb = ReportBuilder(result, report_id=report_id)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if req.format == "pdf":
        path = REPORTS_DIR / f"{report_id}.pdf"
        rb.save_pdf(str(path))
        return FileResponse(str(path), media_type="application/pdf", filename=path.name)
    path = REPORTS_DIR / f"{report_id}.html"
    rb.save(str(path))
    html_content = rb.build_html()
    logger.info("[REPORTS_API] Generated report %s (%s)", report_id, req.format)
    return HTMLResponse(content=html_content)


@router.get("/list")
async def list_reports() -> dict[str, Any]:
    """List all saved reports."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    reports = []
    for p in sorted(REPORTS_DIR.iterdir()):
        if p.suffix in (".html", ".pdf"):
            reports.append(
                ReportMeta(
                    id=p.stem,
                    path=str(p),
                    format=p.suffix.lstrip("."),
                    size_bytes=p.stat().st_size,
                ).model_dump()
            )
    return {"reports": reports, "count": len(reports)}


@router.get("/{report_id}")
async def get_report(report_id: str) -> Any:
    """Download a saved report by ID."""
    for ext in (".html", ".pdf"):
        path = REPORTS_DIR / f"{report_id}{ext}"
        if path.exists():
            media = "text/html" if ext == ".html" else "application/pdf"
            return FileResponse(str(path), media_type=media, filename=path.name)
    raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")


def _load_backtest(backtest_id: str) -> dict[str, Any]:
    """Load a saved backtest result JSON (stub — integrate with backtest module)."""
    p = Path("data/backtests") / f"{backtest_id}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Backtest '{backtest_id}' not found")
    import json
    with p.open() as fh:
        return json.load(fh)  # type: ignore[return-value]
