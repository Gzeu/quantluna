"""
QuantLuna — strategy package public API

Sprint 3 exports:
  SignalGenerator  — entry/exit signal engine (v3)
  TradeSignal      — signal dataclass with .as_dict() WebSocket serialisation
  Signal           — LONG_SPREAD / SHORT_SPREAD / EXIT enum
  RegimeDetector   — vol-ratio + optional HMM regime classifier (v2)
  VolRegime        — NORMAL / HIGH_VOL / BREAKDOWN / TRANSITION enum
  RegimeState      — regime snapshot dataclass (includes hmm_state field)
  PairSelector     — cointegration scanner with 5-factor composite score (v3)
  PairScore        — pair result dataclass (includes kalman_beta_stability field)
"""
from .signal import SignalGenerator, TradeSignal, Signal
from .regime_detector import RegimeDetector, VolRegime, RegimeState
from .pair_selector import PairSelector, PairScore

__all__ = [
    # Signal layer (v3)
    "SignalGenerator",   # .generate_batch(), .generate_live(), .signal_summary(), .reset()
    "TradeSignal",       # .as_dict() for WebSocket serialisation
    "Signal",            # IntEnum: LONG_SPREAD=1, SHORT_SPREAD=-1, EXIT=0
    # Regime layer (v2)
    "RegimeDetector",    # .batch(), .update_one(), .regime_series(), .get_regime_multiplier()
    "VolRegime",         # StrEnum: NORMAL / HIGH_VOL / BREAKDOWN / TRANSITION
    "RegimeState",       # dataclass: regime, vol_ratio, confirmed, hmm_state, timestamp
    # Pair selection (v3)
    "PairSelector",      # .scan(), .get_top_n(), .top_n_as_dataframe(), .rescan_stale()
    "PairScore",         # dataclass: composite_score, kalman_beta_stability, verdict
]
