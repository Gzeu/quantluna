# Changelog

All notable changes to QuantLuna are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

---

## [Unreleased]

### Added
- `tests/conftest.py` — shared session-scoped fixtures: `log_pair`, `random_pair`,
  `warm_kalman`, `fitted_spread_df`, `zscore_series`, `signal_cfg`, `risk_cfg`
- `tests/test_spread.py` — 11 tests covering `SpreadEngine` batch + live paths
- `tests/test_signal.py` — 10 tests covering `SignalGenerator` batch + live paths,
  cold-filter suppression, high-uncertainty gate, hard-stop override
- `tests/test_risk.py` — 19 tests covering `PositionSizer` (sizing math, funding drag,
  edge cases) and `PortfolioRisk` (exposure cap, circuit breaker, PnL tracking)
- `.github/workflows/ci.yml` — GitHub Actions: pytest + coverage (Codecov) + ruff lint

---

## [0.1.0] — 2026-06

### Added
- `core/kalman_filter.py` — 2-state Kalman Filter (Joseph-form covariance update),
  `KalmanHedgeRatio` with warm-up guard
- `core/cointegration.py` — Engle-Granger + Johansen tests, half-life, Hurst exponent
- `core/spread.py` — `SpreadEngine`: rolling z-score on Kalman innovations
- `strategy/signal.py` — `SignalGenerator` with batch + live modes, confidence scoring
- `strategy/regime.py` — `RegimeDetector`: correlation, vol-ratio, rolling Hurst
- `risk/position_sizer.py` — vol-target + fractional Kelly sizing, funding drag
- `risk/portfolio_risk.py` — exposure cap, circuit breaker, PnL aggregation
- `backtest/analytics.py` — Sharpe, Sortino, Calmar, MaxDD, win rate
- `config/settings.py` — Pydantic dataclass config (Kalman, Signal, Risk, Execution)
- Initial test suite: 15 tests on Kalman + Cointegration + Backtest
