"""
strategy/ml/signal_fusion.py — Regime-adaptive ML + Z-score signal blending.

Combines ML predictions with traditional Z-score signals using weights that
adapt to market regime.  Outputs a FusedSignal that can feed directly into
the existing DecisionEngine or SignalCombiner.

Key logic:
  1. Determine base ML weight from market regime
  2. Adjust ML weight by prediction confidence
  3. Clamp weights to configured bounds
  4. Blend: final = w_ml * ml_direction + w_z * z_signal
  5. Apply confidence gate: if ml_conf < threshold, reduce ML influence
  6. Agreement bonus: if ML and Z-score agree on direction, boost confidence
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

from strategy.ml.config import MLConfig
from strategy.ml.models import MLPrediction

# Lazy import to avoid circular dependency at module level
_Signal = getattr(__import__("strategy.signal", fromlist=["Signal"]), "Signal", None)


# ═══════════════════════════════════════════════════════════════════════════════
# FusedSignal
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class FusedSignal:
    """
    Result of blending ML prediction with Z-score signal.

    Compatible with the existing SignalCombiner.CombinedSignal interface.
    """

    score: float = 0.0            # final blended score [-1, 1]
    direction: str = "FLAT"       # "LONG" | "SHORT" | "FLAT"
    strength: str = "none"        # "strong" | "moderate" | "weak" | "none"
    ml_contribution: float = 0.0  # how much ML contributed (0-1)
    z_contribution: float = 0.0   # how much Z-score contributed (0-1)
    ml_confidence: float = 0.0
    ml_weight_used: float = 0.0   # the effective ML weight after adaptation
    z_weight_used: float = 0.0    # the effective Z-score weight
    regime: str = "unknown"
    veto: bool = False
    veto_reason: str = ""
    agreement_bonus: float = 0.0  # bonus when both signals agree

    @property
    def should_trade(self) -> bool:
        """True if the signal is strong enough to warrant a trade."""
        return not self.veto and abs(self.score) >= 0.3

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "direction": self.direction,
            "strength": self.strength,
            "ml_contribution": self.ml_contribution,
            "z_contribution": self.z_contribution,
            "ml_confidence": self.ml_confidence,
            "ml_weight_used": self.ml_weight_used,
            "z_weight_used": self.z_weight_used,
            "regime": self.regime,
            "veto": self.veto,
            "veto_reason": self.veto_reason,
            "agreement_bonus": self.agreement_bonus,
            "should_trade": self.should_trade,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# SignalFusion
# ═══════════════════════════════════════════════════════════════════════════════


class SignalFusion:
    """
    Combines ML predictions with traditional Z-score signals.

    Regime-adaptive blending with confidence gating and agreement bonus.

    Usage::

        cfg = MLConfig()
        fusion = SignalFusion(cfg)

        # Per bar:
        fused = fusion.fuse(
            ml_direction=-0.6,
            ml_confidence=0.75,
            zscore=2.5,
            zscore_threshold=2.0,
            regime="trending",
        )
        if fused.should_trade:
            place_order(fused.direction)

    Regime → ML weight mapping (configurable via MLConfig):
        trending  → trending_ml_weight  (default: 0.40)
        ranging   → ranging_ml_weight   (default: 0.15)
        breakout  → breakout_ml_weight   (default: 0.50)
        unknown   → unknown_ml_weight    (default: 0.20)
    """

    def __init__(self, cfg: MLConfig) -> None:
        self._cfg = cfg
        self._last_fused: Optional[FusedSignal] = None
        self._history: list = []  # last 100 fused signals

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def last_fused(self) -> Optional[FusedSignal]:
        return self._last_fused

    @property
    def history(self) -> list:
        return self._history[-100:]

    def fuse(
        self,
        ml_direction: float,
        ml_confidence: float,
        zscore: float,
        zscore_threshold: float = 2.0,
        regime: str = "unknown",
        zscore_signal: object = None,
        zscore_confidence: float = 0.5,
    ) -> FusedSignal:
        """
        Blend ML prediction with Z-score signal.

        Parameters
        ----------
        ml_direction : float
            ML model direction score in [-1, 1].
            Positive → LONG bias, negative → SHORT bias.
        ml_confidence : float
            ML model confidence in [0, 1].
        zscore : float
            Current Kalman Z-score (signed).
        zscore_threshold : float
            Entry threshold for Z-score (e.g. 2.0).
        regime : str
            Market regime: "trending", "ranging", "breakout", "unknown".
        zscore_signal : optional
            Signal enum from strategy.signal if available (used for
            agreement bonus).  Falls back to zscore sign if None.
        zscore_confidence : float
            Confidence of the Z-score signal [0, 1].

        Returns
        -------
        FusedSignal
        """
        # Step 1: Normalise Z-score → directional signal [-1, 1]
        # stat-arb: z < 0 → LONG (buy spread), z > 0 → SHORT (sell spread)
        # So we negate z: negative z becomes positive signal (LONG)
        if zscore_threshold > 1e-9:
            z_signal = float(np.clip(-zscore / (zscore_threshold * 2.0), -1.0, 1.0))
        else:
            z_signal = float(np.clip(-zscore / 4.0, -1.0, 1.0))

        # Step 2: Determine base ML weight by regime
        ml_w = self._regime_weight(regime)

        # Step 3: Adjust ML weight by confidence
        ml_w = self._adjust_by_confidence(ml_w, ml_confidence)

        # Step 4: Clamp to configured bounds
        ml_w = float(np.clip(ml_w, self._cfg.ml_weight_min, self._cfg.ml_weight_max))
        z_w  = float(np.clip(1.0 - ml_w, self._cfg.zscore_weight_min, self._cfg.zscore_weight_max))

        # Step 5: Blend
        combined = ml_w * ml_direction + z_w * z_signal
        combined = float(np.clip(combined, -1.0, 1.0))

        # Step 6: Agreement bonus
        agreement_bonus = self._agreement_bonus(ml_direction, zscore, zscore_signal)

        # Step 7: Determine direction and strength
        direction, strength = self._classify(combined, abs(ml_direction), ml_confidence)

        # Step 8: Veto check
        veto, veto_reason = self._veto_check(ml_confidence, zscore_confidence)

        fused = FusedSignal(
            score=combined,
            direction=direction,
            strength=strength,
            ml_contribution=ml_w,
            z_contribution=z_w,
            ml_confidence=ml_confidence,
            ml_weight_used=ml_w,
            z_weight_used=z_w,
            regime=regime,
            veto=veto,
            veto_reason=veto_reason,
            agreement_bonus=agreement_bonus,
        )

        self._last_fused = fused
        self._history.append(fused)
        if len(self._history) > 100:
            self._history.pop(0)

        return fused

    def get_fusion_weights(
        self, regime: str, ml_confidence: float = 0.5,
    ) -> Tuple[float, float]:
        """
        Return (ml_weight, zscore_weight) for current regime + confidence.
        Useful for the dashboard to display what weights are being used.
        """
        ml_w = self._regime_weight(regime)
        ml_w = self._adjust_by_confidence(ml_w, ml_confidence)
        ml_w = float(np.clip(ml_w, self._cfg.ml_weight_min, self._cfg.ml_weight_max))
        z_w  = float(np.clip(1.0 - ml_w, self._cfg.zscore_weight_min, self._cfg.zscore_weight_max))
        return (ml_w, z_w)

    def get_all_regime_weights(self, ml_confidence: float = 0.5) -> dict:
        """Return {regime: {ml_weight, z_weight}} for all regimes."""
        regimes = ["trending", "ranging", "breakout", "unknown"]
        result = {}
        for reg in regimes:
            ml_w, z_w = self.get_fusion_weights(reg, ml_confidence)
            result[reg] = {"ml_weight": ml_w, "zscore_weight": z_w}
        return result

    def snapshot(self) -> dict:
        """Full state for API / dashboard."""
        return {
            "last_fused": self._last_fused.as_dict() if self._last_fused else None,
            "history_count": len(self._history),
            "config": {
                "ml_weight_min": self._cfg.ml_weight_min,
                "ml_weight_max": self._cfg.ml_weight_max,
                "confidence_threshold": self._cfg.confidence_threshold,
                "fusion_entry_threshold": self._cfg.fusion_entry_threshold,
            },
            "regime_weights": self.get_all_regime_weights(),
        }

    # ── Internal helpers ────────────────────────────────────────────────────

    def _regime_weight(self, regime: str) -> float:
        """Return the base ML weight for a given regime."""
        r = regime.lower()
        if r == "trending":
            return self._cfg.trending_ml_weight
        elif r == "breakout":
            return self._cfg.breakout_ml_weight
        elif r == "ranging":
            return self._cfg.ranging_ml_weight
        else:
            return self._cfg.unknown_ml_weight

    def _adjust_by_confidence(self, ml_w: float, ml_conf: float) -> float:
        """Reduce ML weight when confidence is low; boost when very high."""
        thresh = self._cfg.confidence_threshold
        if ml_conf < thresh:
            ml_w *= 0.5
        elif ml_conf >= 0.8:
            ml_w = min(ml_w * 1.2, self._cfg.ml_weight_max)
        return ml_w

    def _agreement_bonus(
        self,
        ml_dir: float,
        zscore: float,
        zscore_signal: object,
    ) -> float:
        """
        Compute agreement bonus: +0.10 if both signals agree on direction.

        Direction convention:
          ml_dir  > 0  → LONG   /  ml_dir  < 0  → SHORT
          zscore  < 0  → LONG   /  zscore  > 0  → SHORT  (stat-arb: neg z = buy)
        """
        ml_sign = 1 if ml_dir > 0.05 else (-1 if ml_dir < -0.05 else 0)
        if zscore_signal is not None and hasattr(zscore_signal, "value"):
            z_sign = 1 if zscore_signal.value == 1 else (-1 if zscore_signal.value == -1 else 0)
        else:
            # stat-arb: z < 0 → LONG_SPREAD (+1), z > 0 → SHORT_SPREAD (-1)
            z_sign = 1 if zscore < -0.05 else (-1 if zscore > 0.05 else 0)

        if ml_sign != 0 and ml_sign == z_sign:
            return 0.10
        return 0.0

    @staticmethod
    def _classify(
        combined: float,
        ml_abs: float,
        ml_conf: float,
    ) -> Tuple[str, str]:
        """Classify final blended score into direction + strength."""
        if combined > 0.15:
            direction = "LONG"
        elif combined < -0.15:
            direction = "SHORT"
        else:
            direction = "FLAT"
            return (direction, "none")

        abs_score = abs(combined)
        if abs_score >= 0.75:
            strength = "strong"
        elif abs_score >= 0.5:
            strength = "moderate"
        elif abs_score >= 0.3:
            strength = "weak"
        else:
            strength = "none"
            direction = "FLAT"

        return (direction, strength)

    @staticmethod
    def _veto_check(ml_conf: float, z_conf: float) -> Tuple[bool, str]:
        """Veto the signal if both models have very low confidence."""
        if ml_conf < 0.15 and z_conf < 0.2:
            return (True, "both models low confidence")
        return (False, "")
