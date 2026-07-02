"""
QuantLuna — Kalman Adapter (Sprint 20)

Bridge între core.KalmanFilter (API internă) și IntegrationLoop.

IntegrationLoop aşteaptă un obiect cu:
  - update(price_y, price_x)  → None
  - .zscore       → float
  - .half_life    → float  (ore)
  - .p_diag       → float  (Kalman P[0,0] hedge ratio variance)
  - .spread       → float  (spread curent)

KalmanFilter intern poate expune alte property-uri sau altă signatură.
Acest adapter normalizează interfata fără a modifica core/kalman_filter.py.

Usage:
    from core.kalman_adapter import KalmanAdapter
    kf = KalmanAdapter()          # wrappedînKalmanFilter cu defaults
    kf.update(price_y, price_x)
    print(kf.zscore, kf.spread)
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional

from loguru import logger

try:
    from core.kalman_filter import KalmanFilter as _KF
    _KF_AVAILABLE = True
except Exception as _e:
    _KF_AVAILABLE = False
    logger.warning(f"KalmanAdapter: KalmanFilter not importable ({_e}), using fallback")


class KalmanAdapter:
    """
    Normalised wrapper around KalmanFilter.

    Falls back to a simple rolling z-score if KalmanFilter is not importable
    (useful in CI where optional deps may be missing).

    Parameters
    ----------
    window      : rolling window for z-score normalisation (fallback only)
    half_life_h : assumed half-life in hours (fallback only)
    kf_kwargs   : keyword args forwarded to KalmanFilter.__init__
    """

    def __init__(
        self,
        window:       int = 100,
        half_life_h:  float = 24.0,
        **kf_kwargs,
    ) -> None:
        self._window      = window
        self._half_life_h = half_life_h
        self._kf: Optional[object] = None

        self._spread_history: Deque[float] = deque(maxlen=window)
        self._zscore:     float = 0.0
        self._spread:     float = 0.0
        self._p_diag:     float = 0.0
        self._half_life:  float = half_life_h
        self._bar_count:  int   = 0

        if _KF_AVAILABLE:
            try:
                self._kf = _KF(**kf_kwargs)
                logger.debug("KalmanAdapter: using KalmanFilter")
            except Exception as exc:
                logger.warning(f"KalmanAdapter: KalmanFilter init failed ({exc}), using fallback")

    # ------------------------------------------------------------------
    # Main interface
    # ------------------------------------------------------------------

    def update(self, price_y: float, price_x: float) -> None:
        """Process one price bar."""
        self._bar_count += 1

        if self._kf is not None:
            self._update_kf(price_y, price_x)
        else:
            self._update_fallback(price_y, price_x)

    # ------------------------------------------------------------------
    # Properties (IntegrationLoop interface)
    # ------------------------------------------------------------------

    @property
    def zscore(self) -> float:
        return self._zscore

    @property
    def spread(self) -> float:
        return self._spread

    @property
    def half_life(self) -> float:
        return self._half_life

    @property
    def p_diag(self) -> float:
        return self._p_diag

    @property
    def bar_count(self) -> int:
        return self._bar_count

    @property
    def is_warmed_up(self) -> bool:
        return self._bar_count >= self._window

    # ------------------------------------------------------------------
    # Internal: KalmanFilter path
    # ------------------------------------------------------------------

    def _update_kf(self, price_y: float, price_x: float) -> None:
        kf = self._kf
        try:
            # Try common KalmanFilter update signatures
            if hasattr(kf, "update"):
                kf.update(price_y, price_x)  # type: ignore[union-attr]
            elif hasattr(kf, "step"):
                kf.step(price_y, price_x)  # type: ignore[union-attr]

            # Extract properties (try multiple naming conventions)
            self._spread    = float(self._get_attr(kf, ["spread", "residual", "e"], 0.0))
            self._zscore    = float(self._get_attr(kf, ["zscore", "z_score", "z"], 0.0))
            self._half_life = float(self._get_attr(kf, ["half_life", "halflife"], self._half_life_h))
            self._p_diag    = float(self._get_attr(kf, ["p_diag", "P", "uncertainty"], 0.0))

            # If KF doesn't compute z-score directly, compute from spread history
            if self._zscore == 0.0 and self._spread != 0.0:
                self._compute_zscore_from_spread(self._spread)

        except Exception as exc:
            logger.warning(f"KalmanAdapter: KF update error ({exc}), falling back")
            self._update_fallback(price_y, price_x)

    def _update_fallback(self, price_y: float, price_x: float) -> None:
        """Simple spread = price_y - price_x, z-score from rolling window."""
        spread = price_y - price_x
        self._spread = spread
        self._compute_zscore_from_spread(spread)

    def _compute_zscore_from_spread(self, spread: float) -> None:
        self._spread_history.append(spread)
        if len(self._spread_history) < 2:
            self._zscore = 0.0
            return
        import statistics
        mu    = statistics.mean(self._spread_history)
        sigma = statistics.stdev(self._spread_history)
        self._zscore = (spread - mu) / sigma if sigma > 1e-10 else 0.0

    @staticmethod
    def _get_attr(obj, names: list, default):
        for name in names:
            val = getattr(obj, name, None)
            if val is not None:
                return val
        return default
