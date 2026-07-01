"""
api/schemas.py  —  Pydantic models pentru backtest REST API

BacktestRequest   — body pentru POST /api/backtest/run
BacktestMetrics   — OOS aggregate metrics
BacktestResponse  — răspuns complet job
JobStatus         — enumerare stări job
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    QUEUED    = "queued"
    RUNNING   = "running"
    DONE      = "done"
    ERROR     = "error"


class BarFreq(str, Enum):
    M1  = "1m"
    M3  = "3m"
    M5  = "5m"
    M15 = "15m"
    M30 = "30m"
    H1  = "1h"
    H2  = "2h"
    H4  = "4h"
    H6  = "6h"
    H8  = "8h"
    H12 = "12h"
    D1  = "1d"


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class BacktestRequest(BaseModel):
    """
    Body pentru POST /api/backtest/run.

    Toate câmpurile au valori implicite — un body gol {} este valid
    și rulează cu parametrii default ai StrategyConfig.

    Exemplu minimal:
        {"sym_y": "BTCUSDT", "sym_x": "ETHUSDT", "capital_usdt": 5000}

    Exemplu complet:
        {
            "sym_y": "BTCUSDT",
            "sym_x": "ETHUSDT",
            "bar_freq": "1h",
            "capital_usdt": 10000,
            "zscore_entry": 2.0,
            "zscore_exit": 0.5,
            "delta": 1e-4,
            "n_splits": 5,
            "purge_bars": 30,
            "embargo_bars": 24,
            "n_bars": 2000
        }
    """
    # Symbols
    sym_y: str = Field(default="BTCUSDT", description="Symbol Y (leg activă)")
    sym_x: str = Field(default="ETHUSDT", description="Symbol X (leg pasivă / hedge)")
    bar_freq: BarFreq = Field(default=BarFreq.H1, description="Bar frequency")

    # Capital & sizing
    capital_usdt: float = Field(default=10_000.0, gt=0, description="Capital inițial USD")
    vol_target: float   = Field(default=0.01, gt=0, le=0.20, description="Daily vol target")
    kelly_fraction: float = Field(default=0.25, gt=0, le=1.0)
    max_leverage: float   = Field(default=3.0, gt=0, le=10.0)

    # Signal
    zscore_entry: float = Field(default=2.0, gt=0.5, le=5.0)
    zscore_exit:  float = Field(default=0.5, ge=0.0, le=3.0)
    zscore_window: int  = Field(default=100, ge=20, le=500)
    warm_up_bars: int   = Field(default=30, ge=10, le=200)

    # Kalman
    delta: float = Field(default=1e-4, gt=0, le=0.1, description="Kalman delta (process noise)")
    observation_noise: float = Field(default=1e-2, gt=0)

    # Fees & slippage
    fee_rate: float     = Field(default=0.00055, ge=0, le=0.01)
    slippage_pct: float = Field(default=0.0005,  ge=0, le=0.01)

    # Walk-forward
    n_splits:     int = Field(default=5, ge=2, le=20)
    purge_bars:   int = Field(default=30, ge=0, le=200)
    embargo_bars: int = Field(default=24, ge=0, le=200)

    # Data source
    n_bars: Optional[int] = Field(
        default=None, ge=300,
        description="Lungime date sintetice (doar dacă data_dir nu e specificat)"
    )
    data_dir: Optional[str] = Field(
        default=None,
        description="Director cu fişiere parquet {SYM}_{freq}.parquet"
    )
    params_file: Optional[str] = Field(
        default=None,
        description="JSON best_params de la Optuna (override individuale)"
    )

    # Output
    include_trades: bool = Field(
        default=True,
        description="Dacă True, trades sunt incluse în răspuns (max 1000 rânduri)"
    )

    @field_validator("sym_y", "sym_x")
    @classmethod
    def symbols_uppercase(cls, v: str) -> str:
        return v.upper().strip()

    @model_validator(mode="after")
    def zscore_entry_gt_exit(self) -> "BacktestRequest":
        if self.zscore_entry <= self.zscore_exit:
            raise ValueError(
                f"zscore_entry ({self.zscore_entry}) must be > zscore_exit ({self.zscore_exit})"
            )
        return self


# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------

class BacktestMetrics(BaseModel):
    sharpe:          float
    sortino:         float
    calmar:          float
    max_drawdown:    float
    max_drawdown_pct: float
    win_rate:        float
    profit_factor:   float
    n_trades:        int
    total_net_pnl:   float
    ann_return:      float
    ann_volatility:  float
    n_folds:         int
    overfit_flag:    bool

    class Config:
        json_encoders = {float: lambda v: round(v, 6)}


class BacktestResponse(BaseModel):
    job_id:   str
    status:   JobStatus
    request:  BacktestRequest
    metrics:  Optional[BacktestMetrics] = None
    trades:   Optional[List[Dict[str, Any]]] = None  # max 1000 rows
    trades_csv_url: Optional[str] = None             # GET /api/backtest/jobs/{id}/trades.csv
    error:    Optional[str] = None
    duration_s: Optional[float] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class JobListItem(BaseModel):
    job_id:    str
    status:    JobStatus
    sym_y:     str
    sym_x:     str
    bar_freq:  str
    n_splits:  int
    created_at: Optional[str] = None
    duration_s: Optional[float] = None
    sharpe:    Optional[float] = None
