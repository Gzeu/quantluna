"""
api/schemas.py  —  QuantLuna Pydantic schemas (Sprint 16 + S18 + S20 + REVIEW-3)

Toate modelele de request/response pentru API-ul backtest.

Fix-uri aplicate:
  [REVIEW-3] n_bars: ge/le pe Optional[int] sunt ignorate de Pydantic v2 cand
             valoarea este None. Adaugat @field_validator explicit.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "BarFreq",
    "JobStatus",
    "BacktestRequest",
    "BacktestMetrics",
    "BacktestResponse",
    "JobListItem",
]


class BarFreq(str, Enum):
    m1  = "1m"
    m3  = "3m"
    m5  = "5m"
    m15 = "15m"
    m30 = "30m"
    h1  = "1h"
    h2  = "2h"
    h4  = "4h"
    h6  = "6h"
    h8  = "8h"
    h12 = "12h"
    d1  = "1d"


class JobStatus(str, Enum):
    QUEUED  = "queued"
    RUNNING = "running"
    DONE    = "done"
    ERROR   = "error"


class BacktestRequest(BaseModel):
    sym_y:            str     = Field("BTC/USDT:USDT")
    sym_x:            str     = Field("ETH/USDT:USDT")
    bar_freq:         BarFreq = Field(BarFreq.h1)
    n_splits:         int     = Field(3, ge=1, le=20)
    # [REVIEW-3] Field(None) fara ge/le: Pydantic v2 ignora ge/le pe Optional[int]
    # cand valoarea este None, deci n_bars=50 trecea validarea silentios.
    # Validarea range-ului este delegata la @field_validator de mai jos.
    n_bars:           Optional[int] = Field(None)
    data_dir:         Optional[str] = None
    params_file:      Optional[str] = None
    capital_usdt:     float = Field(10_000.0, gt=0)
    vol_target:       float = Field(0.01, gt=0, le=0.5)
    kelly_fraction:   float = Field(0.25, gt=0, le=1.0)
    max_leverage:     float = Field(3.0, gt=0, le=20)
    zscore_entry:     float = Field(2.0, gt=0)
    zscore_exit:      float = Field(0.5, ge=0)
    zscore_window:    int   = Field(60, ge=10)
    warm_up_bars:     int   = Field(30, ge=5)
    delta:            float = Field(1e-4, gt=0)
    observation_noise:float = Field(1e-2, gt=0)
    fee_rate:         float = Field(0.0006, ge=0)
    slippage_pct:     float = Field(0.0002, ge=0)
    purge_bars:       int   = Field(5, ge=0)
    embargo_bars:     int   = Field(2, ge=0)
    include_trades:   bool  = Field(False)

    # [REVIEW-3] Explicit range enforcement pentru n_bars.
    # Fara acest validator, n_bars=50 trece validarea silentios in Pydantic v2.
    @field_validator("n_bars")
    @classmethod
    def validate_n_bars(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not (100 <= v <= 200_000):
            raise ValueError(
                f"n_bars must be between 100 and 200_000, got {v}"
            )
        return v

    model_config = {"json_schema_extra": {
        "example": {
            "sym_y": "BTC/USDT:USDT",
            "sym_x": "ETH/USDT:USDT",
            "bar_freq": "1h",
            "n_splits": 5,
            "capital_usdt": 10000,
            "zscore_entry": 2.0,
        }
    }}


class BacktestMetrics(BaseModel):
    sharpe:           float = 0.0
    sortino:          float = 0.0
    calmar:           float = 0.0
    max_drawdown:     float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate:         float = 0.0
    profit_factor:    float = 0.0
    n_trades:         int   = 0
    total_net_pnl:    float = 0.0
    ann_return:       float = 0.0
    ann_volatility:   float = 0.0
    n_folds:          int   = 0
    overfit_flag:     bool  = False


class BacktestResponse(BaseModel):
    job_id:         str
    status:         JobStatus
    request:        BacktestRequest
    metrics:        Optional[BacktestMetrics] = None
    trades:         Optional[List[Dict[str, Any]]] = None
    trades_csv_url: Optional[str] = None
    error:          Optional[str] = None
    duration_s:     Optional[float] = None
    created_at:     str
    completed_at:   Optional[str] = None


class JobListItem(BaseModel):
    job_id:     str
    status:     JobStatus
    sym_y:      str
    sym_x:      str
    bar_freq:   str
    n_splits:   int
    created_at: str
    duration_s: Optional[float] = None
    sharpe:     Optional[float] = None
