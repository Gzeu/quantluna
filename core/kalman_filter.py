"""
QuantLuna — Kalman Filter for Dynamic Hedge Ratio Estimation

State space model:
  State:       beta_t  (hedge ratio) — what we estimate
  Observation: y_t = beta_t * x_t + alpha_t + epsilon_t

Equations:
  Predict:
    beta_hat_t|t-1 = beta_hat_t-1|t-1
    P_t|t-1       = P_t-1|t-1 + Q

  Update:
    y_hat_t  = x_t * beta_hat_t|t-1
    e_t      = y_t - y_hat_t           (innovation)
    S_t      = x_t^2 * P_t|t-1 + R    (innovation variance)
    K_t      = P_t|t-1 * x_t / S_t    (Kalman Gain)
    beta_hat_t = beta_hat_t|t-1 + K_t * e_t
    P_t      = (1 - K_t * x_t) * P_t|t-1
"""
import numpy as np
import pandas as pd
from typing import Tuple, Optional
from dataclasses import dataclass
from loguru import logger


@dataclass
class KalmanState:
    """Current state of the Kalman Filter."""
    beta: float           # Current hedge ratio estimate
    alpha: float          # Current intercept estimate
    P_beta: float         # State covariance for beta
    P_alpha: float        # State covariance for alpha
    kalman_gain_beta: float
    kalman_gain_alpha: float
    innovation: float     # Last residual (y_t - y_hat_t)
    innovation_var: float # S_t — for signal quality assessment
    timestamp: Optional[pd.Timestamp] = None


class KalmanHedgeRatio:
    """
    Two-state Kalman Filter estimating (beta, alpha) simultaneously.

    The filter tracks both slope (hedge ratio) and intercept dynamically,
    adapting to gradual regime shifts in the cointegration relationship.

    Parameters
    ----------
    delta : float
        Process noise parameter. Controls adaptation speed.
        Typical range: 1e-5 (very slow) to 1e-2 (very fast).
        For crypto: 1e-4 to 5e-4 works well on 1h data.
    observation_noise : float
        Measurement noise R. Higher = smoother but less reactive.
    """

    def __init__(
        self,
        delta: float = 1e-4,
        observation_noise: float = 1e-2,
        initial_beta: float = 1.0,
        initial_alpha: float = 0.0,
        initial_cov: float = 1.0,
    ):
        self.delta = delta
        self.R = observation_noise
        self.Q = delta / (1 - delta) * np.eye(2)  # Process noise matrix

        # Initial state
        self.beta = initial_beta
        self.alpha = initial_alpha
        self.P = np.array([
            [initial_cov, 0.0],
            [0.0,         initial_cov]
        ])

        self._history: list = []
        self._is_warm = False
        self._n_updates = 0
        logger.debug(f"KalmanHedgeRatio init: delta={delta}, R={observation_noise}")

    # ------------------------------------------------------------------
    # Core update step
    # ------------------------------------------------------------------
    def update(self, y: float, x: float, ts: Optional[pd.Timestamp] = None) -> KalmanState:
        """
        Process one observation and return updated state.

        Parameters
        ----------
        y : float  — dependent asset price (e.g., ETH close)
        x : float  — independent asset price (e.g., BTC close)
        ts : optional timestamp

        Returns
        -------
        KalmanState with updated beta, alpha, gain, innovation
        """
        # Observation vector F = [x, 1]
        F = np.array([x, 1.0])

        # --- Predict step ---
        # beta_hat and alpha_hat stay the same (random walk prior)
        P_pred = self.P + self.Q

        # --- Innovation ---
        y_hat = F @ np.array([self.beta, self.alpha])
        innovation = y - y_hat

        # --- Innovation variance ---
        S = F @ P_pred @ F.T + self.R

        # --- Kalman Gain ---
        K = P_pred @ F.T / S   # shape (2,)

        # --- Update state ---
        state_vec = np.array([self.beta, self.alpha]) + K * innovation
        self.beta = state_vec[0]
        self.alpha = state_vec[1]

        # --- Update covariance ---
        self.P = (np.eye(2) - np.outer(K, F)) @ P_pred

        self._n_updates += 1
        if self._n_updates >= 30:
            self._is_warm = True

        state = KalmanState(
            beta=self.beta,
            alpha=self.alpha,
            P_beta=self.P[0, 0],
            P_alpha=self.P[1, 1],
            kalman_gain_beta=K[0],
            kalman_gain_alpha=K[1],
            innovation=innovation,
            innovation_var=S,
            timestamp=ts,
        )
        self._history.append(state)
        return state

    # ------------------------------------------------------------------
    # Batch fit
    # ------------------------------------------------------------------
    def fit(self, y: pd.Series, x: pd.Series) -> pd.DataFrame:
        """
        Fit Kalman Filter over full price series.
        Returns DataFrame with beta, alpha, spread, kalman_gain, uncertainty.
        """
        if len(y) != len(x):
            raise ValueError("y and x must have same length")

        results = []
        for ts, (yi, xi) in zip(y.index, zip(y.values, x.values)):
            state = self.update(float(yi), float(xi), ts=ts)
            results.append({
                "timestamp": ts,
                "beta": state.beta,
                "alpha": state.alpha,
                "spread": state.innovation,
                "P_beta": state.P_beta,
                "kalman_gain_beta": state.kalman_gain_beta,
                "innovation_var": state.innovation_var,
                "is_warm": self._is_warm or self._n_updates >= 30,
            })

        df = pd.DataFrame(results).set_index("timestamp")
        logger.info(
            f"Kalman fit complete: {len(df)} bars, "
            f"final beta={df['beta'].iloc[-1]:.4f}, "
            f"beta_std={df['beta'].std():.4f}"
        )
        return df

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def current_beta(self) -> float:
        return self.beta

    @property
    def current_alpha(self) -> float:
        return self.alpha

    @property
    def uncertainty(self) -> float:
        """Current 1-sigma uncertainty on beta estimate."""
        return np.sqrt(self.P[0, 0])

    @property
    def is_warm(self) -> bool:
        return self._is_warm

    def reset(self) -> None:
        """Reset filter state for re-initialization."""
        self.P = np.eye(2)
        self._n_updates = 0
        self._is_warm = False
        self._history.clear()

    def get_history_df(self) -> pd.DataFrame:
        if not self._history:
            return pd.DataFrame()
        return pd.DataFrame([vars(s) for s in self._history]).set_index("timestamp")
