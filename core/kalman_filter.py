from __future__ import annotations

import numpy as np
import pandas as pd
from collections import deque
from dataclasses import dataclass
from typing import Optional, Deque
from loguru import logger

# Maximum number of KalmanState snapshots kept in memory.
# At 1-min bars this is ~7 days; at 1h bars ~180 days.
_HISTORY_MAXLEN = 10_000


@dataclass
class KalmanState:
    """Snapshot of the filter state after one update."""
    beta: float
    alpha: float
    P_beta: float
    P_alpha: float
    kalman_gain_beta: float
    kalman_gain_alpha: float
    innovation: float
    innovation_var: float
    is_warm: bool = False
    timestamp: Optional[pd.Timestamp] = None

    @property
    def kalman_gain(self) -> float:
        """Primary Kalman Gain (beta component)."""
        return self.kalman_gain_beta


class KalmanHedgeRatio:
    """
    Two-state Kalman Filter estimating (beta, alpha) simultaneously.

    State space model::

        Observation:  y_t = beta_t * x_t + alpha_t + epsilon_t
        State prior:  [beta_t, alpha_t] = [beta_{t-1}, alpha_{t-1}]  (random walk)

    Kalman Equations::

        Predict:  P_pred = P + Q
        F           = [x_t, 1]
        innovation  = y_t - F @ state
        S           = F @ P_pred @ F.T + R
        K           = P_pred @ F.T / S
        state      += K * innovation
        P (Joseph)  = (I - K*F) @ P_pred @ (I - K*F).T + R * K*K.T

    Parameters
    ----------
    delta : float
        Process noise parameter. Q = delta/(1-delta) * I.
        Controls adaptation speed (1e-5=very slow, 1e-4=default, 5e-4=fast).
    observation_noise : float
        Measurement noise R. Higher = smoother but less reactive.
    warm_up : int
        Bars before is_warm becomes True. Results before warm-up
        should not be used for trading signals.
    """

    def __init__(
        self,
        delta: float = 1e-4,
        observation_noise: float = 1e-2,
        warm_up: int = 30,
        initial_beta: float = 1.0,
        initial_alpha: float = 0.0,
        initial_cov: float = 1.0,
    ) -> None:
        self._delta = delta
        self._R = observation_noise
        self.warm_up = warm_up

        # Q is derived from delta — always kept in sync via the property setter
        self._Q = self._compute_Q(delta)

        self._beta0 = initial_beta
        self._alpha0 = initial_alpha
        self._cov0 = initial_cov

        self.beta = initial_beta
        self.alpha = initial_alpha
        self.P = np.array([[initial_cov, 0.0], [0.0, initial_cov]])

        # FIX: bounded deque — prevents unbounded memory growth in live mode
        self._history: Deque[KalmanState] = deque(maxlen=_HISTORY_MAXLEN)
        self._is_warm: bool = False
        self._n_updates: int = 0

        logger.debug(
            f"KalmanHedgeRatio init: delta={delta}, R={observation_noise}, warm_up={warm_up}"
        )

    # ------------------------------------------------------------------ #
    # Properties — single source of truth for R and Q
    # ------------------------------------------------------------------ #

    @staticmethod
    def _compute_Q(delta: float) -> np.ndarray:
        """Q = delta / (1 - delta) * I₂"""
        if not (0.0 < delta < 1.0):
            raise ValueError(f"delta must be in (0, 1), got {delta}")
        return delta / (1.0 - delta) * np.eye(2)

    @property
    def delta(self) -> float:
        return self._delta

    @delta.setter
    def delta(self, value: float) -> None:
        """Setting delta recomputes Q atomically — no stale Q risk."""
        self._Q = self._compute_Q(value)
        self._delta = value
        logger.debug(f"KalmanHedgeRatio delta updated → {value} (Q recomputed)")

    @property
    def observation_noise(self) -> float:
        """Public alias for measurement noise R."""
        return self._R

    @observation_noise.setter
    def observation_noise(self, value: float) -> None:
        """Single setter — keeps R in sync everywhere."""
        self._R = float(value)
        logger.debug(f"KalmanHedgeRatio observation_noise updated → {value}")

    # Keep backward-compatible direct attribute access
    @property
    def R(self) -> float:
        return self._R

    @R.setter
    def R(self, value: float) -> None:
        self.observation_noise = value

    @property
    def Q(self) -> np.ndarray:
        return self._Q

    def update_delta(self, new_delta: float) -> None:
        """Convenience method for dynamic delta changes (regime adaptation)."""
        self.delta = new_delta

    # ------------------------------------------------------------------ #
    # Core Update
    # ------------------------------------------------------------------ #

    def update(
        self, y: float, x: float, ts: Optional[pd.Timestamp] = None
    ) -> KalmanState:
        """
        Process one observation and return updated KalmanState.

        Parameters
        ----------
        y : float  — dependent asset log-price or price
        x : float  — independent asset log-price or price (must be != 0)
        ts : optional timestamp
        """
        # FIX: guard against x=0 — makes beta unobservable and P explodes
        if abs(x) < 1e-10:
            logger.warning(
                f"KalmanHedgeRatio.update(): x={x} is effectively zero — skipping update, "
                "returning last state. Check price feed for zeros/NaNs."
            )
            return KalmanState(
                beta=self.beta,
                alpha=self.alpha,
                P_beta=float(self.P[0, 0]),
                P_alpha=float(self.P[1, 1]),
                kalman_gain_beta=0.0,
                kalman_gain_alpha=0.0,
                innovation=0.0,
                innovation_var=float(self._R),
                is_warm=self._is_warm,
                timestamp=ts,
            )

        F = np.array([x, 1.0])

        # Predict
        P_pred = self.P + self._Q

        # Innovation
        y_hat = float(F @ np.array([self.beta, self.alpha]))
        innovation = y - y_hat

        # Innovation variance (scalar)
        S = float(F @ P_pred @ F) + self._R

        # Kalman Gain
        K = P_pred @ F / S  # (2,)

        # State update
        sv = np.array([self.beta, self.alpha]) + K * innovation
        self.beta, self.alpha = float(sv[0]), float(sv[1])

        # Covariance update — Joseph form for numerical stability
        I_KF = np.eye(2) - np.outer(K, F)
        self.P = I_KF @ P_pred @ I_KF.T + self._R * np.outer(K, K)

        self._n_updates += 1
        if self._n_updates >= self.warm_up:
            self._is_warm = True

        state = KalmanState(
            beta=self.beta,
            alpha=self.alpha,
            P_beta=float(self.P[0, 0]),
            P_alpha=float(self.P[1, 1]),
            kalman_gain_beta=float(K[0]),
            kalman_gain_alpha=float(K[1]),
            innovation=float(innovation),
            innovation_var=float(S),
            is_warm=self._is_warm,
            timestamp=ts,
        )
        self._history.append(state)
        return state

    # ------------------------------------------------------------------ #
    # Batch Fit
    # ------------------------------------------------------------------ #

    def fit(self, y: pd.Series, x: pd.Series) -> pd.DataFrame:
        """
        Fit Kalman Filter over a full price series.

        Returns
        -------
        pd.DataFrame with columns:
            beta, alpha, spread (innovation), P_beta, P_alpha,
            kalman_gain_beta, innovation_var, is_warm
        """
        if len(y) != len(x):
            raise ValueError(f"y ({len(y)}) and x ({len(x)}) must have same length")

        rows = []
        for ts, yi, xi in zip(y.index, y.values, x.values):
            s = self.update(float(yi), float(xi), ts=ts)
            rows.append({
                "timestamp":        ts,
                "beta":             s.beta,
                "alpha":            s.alpha,
                "spread":           s.innovation,
                "P_beta":           s.P_beta,
                "P_alpha":          s.P_alpha,
                "kalman_gain_beta": s.kalman_gain_beta,
                "innovation_var":   s.innovation_var,
                "is_warm":          s.is_warm,
            })

        df = pd.DataFrame(rows).set_index("timestamp")
        logger.info(
            f"Kalman fit: {len(df)} bars | "
            f"beta={df['beta'].iloc[-1]:.4f} | "
            f"P_beta={df['P_beta'].iloc[-1]:.6f} | "
            f"warm_up={self.warm_up}"
        )
        return df

    # ------------------------------------------------------------------ #
    # Properties & Utilities
    # ------------------------------------------------------------------ #

    @property
    def current_beta(self) -> float:
        return self.beta

    @property
    def current_alpha(self) -> float:
        return self.alpha

    @property
    def uncertainty(self) -> float:
        """1-sigma uncertainty on the beta estimate (sqrt(P_beta))."""
        return float(np.sqrt(self.P[0, 0]))

    @property
    def is_warm(self) -> bool:
        return self._is_warm

    def reset(self) -> None:
        """Reset to initial state — use when switching pair or after regime break."""
        self.beta = self._beta0
        self.alpha = self._alpha0
        self.P = np.array([[self._cov0, 0.0], [0.0, self._cov0]])
        self._n_updates = 0
        self._is_warm = False
        self._history.clear()
        logger.debug("KalmanHedgeRatio reset")

    def get_history_df(self) -> pd.DataFrame:
        """Return full update history as DataFrame (useful for debugging)."""
        if not self._history:
            return pd.DataFrame()
        records = [
            {
                "timestamp":        s.timestamp,
                "beta":             s.beta,
                "alpha":            s.alpha,
                "P_beta":           s.P_beta,
                "kalman_gain_beta": s.kalman_gain_beta,
                "innovation":       s.innovation,
                "innovation_var":   s.innovation_var,
                "is_warm":          s.is_warm,
            }
            for s in self._history
        ]
        return pd.DataFrame(records).set_index("timestamp")
