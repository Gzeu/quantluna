"""
QuantLuna — Shared pytest fixtures.

All test modules import fixtures from here via automatic conftest discovery.
Design principle: fixtures use realistic log-price crypto scales.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.settings import SignalConfig, RiskConfig, QuantLunaConfig
from core.kalman_filter import KalmanHedgeRatio
from core.spread import SpreadEngine

# ── Seeded RNG ─────────────────────────────────────────────────────────────────
RNG = np.random.default_rng(0xC0FFEE)


# ── Data generators ────────────────────────────────────────────────────────────

def _log_price_pair(
    n: int = 1000,
    beta: float = 0.85,
    alpha: float = 0.0,
    rho: float = 0.95,
    noise_std: float = 0.08,
    freq: str = "1h",
) -> tuple[pd.Series, pd.Series]:
    """
    Cointegrated log-price pair.
    x  = log(60_000) + cumsum(N(0, 0.002))    <- BTC-like
    spread follows AR(1) with coefficient rho
    y  = beta*x + alpha + spread
    Theoretical HL = -log(2)/log(rho) bars.
    """
    x_vals = np.log(60_000) + np.cumsum(RNG.standard_normal(n) * 0.002)
    spread = [0.0]
    for _ in range(n - 1):
        spread.append(rho * spread[-1] + RNG.standard_normal() * noise_std)
    spread = np.array(spread)
    y_vals = beta * x_vals + alpha + spread
    ts = pd.date_range("2024-01-01", periods=n, freq=freq)
    return pd.Series(y_vals, index=ts, name="Y"), pd.Series(x_vals, index=ts, name="X")


def _random_walk_pair(n: int = 600) -> tuple[pd.Series, pd.Series]:
    """Two independent random walks — NOT cointegrated."""
    x = np.cumsum(RNG.standard_normal(n))
    y = np.cumsum(RNG.standard_normal(n))
    ts = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.Series(y, index=ts), pd.Series(x, index=ts)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def log_pair() -> tuple[pd.Series, pd.Series]:
    """Cointegrated log-price pair, 1000 bars, 1h."""
    return _log_price_pair(n=1000)


@pytest.fixture(scope="session")
def random_pair() -> tuple[pd.Series, pd.Series]:
    """Non-cointegrated random walk pair."""
    return _random_walk_pair(n=600)


@pytest.fixture(scope="session")
def signal_cfg() -> SignalConfig:
    return SignalConfig(
        zscore_entry=2.0,
        zscore_exit=0.5,
        zscore_stop=3.5,
    )


@pytest.fixture(scope="session")
def risk_cfg() -> RiskConfig:
    return RiskConfig(
        max_capital_usdt=10_000,
        max_leverage=3.0,
        risk_per_trade=0.01,
        vol_target_annual=0.20,
        kelly_fraction=0.25,
        max_position_pct=0.20,
    )


@pytest.fixture
def warm_kalman() -> KalmanHedgeRatio:
    """KalmanHedgeRatio already past warm-up (50 bars fed)."""
    y, x = _log_price_pair(n=200)
    kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-3, warm_up=30)
    for yi, xi in zip(y.values, x.values):
        kf.update(float(yi), float(xi))
    return kf


@pytest.fixture
def fitted_spread_df(log_pair) -> pd.DataFrame:
    """SpreadEngine.fit() output on the 1000-bar log pair."""
    y, x = log_pair
    kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-3, warm_up=30)
    engine = SpreadEngine(kf, zscore_window=100, min_warm_periods=30)
    return engine.fit(y, x)


@pytest.fixture
def spread_series(fitted_spread_df) -> pd.Series:
    """Spread (innovation) series from fitted engine."""
    return fitted_spread_df["spread"]


@pytest.fixture
def zscore_series(fitted_spread_df) -> pd.Series:
    """Z-score series from fitted engine."""
    return fitted_spread_df["zscore"]
