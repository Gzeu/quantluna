"""
strategy/strategy_coordinator.py — Multi-strategy coordinator (Sprint 48).

Coordinates multiple strategies per pair with regime-adaptive weight
allocation.  Each strategy votes on direction; the coordinator blends
votes by regime score and produces a unified CoordinatedDecision.

Key features:
  - Regime-adaptive weights per strategy
  - Weighted voting with conflict resolution
  - Capital fraction allocation based on combined confidence
  - Outcome tracking for adaptive learning over time

Usage::

    coord = StrategyCoordinator(cfg)
    coord.register(kalman_strategy, StrategyAllocation(
        "KalmanPairs",
        weights={"ranging": 0.75, "trending": 0.10, "breakout": 0.25},
    ))
    coord.register(momentum_strategy, StrategyAllocation(
        "ZScoreMomentum",
        weights={"ranging": 0.05, "trending": 0.60, "breakout": 0.45},
    ))

    # Per bar:
    decision = coord.coordinate(context)
    if decision.direction != "FLAT":
        place_order(decision.direction, capital_fraction=decision.capital_fraction)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from loguru import logger

from strategy.signal_combiner import CombinedSignal


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class StrategyAllocation:
    """Per-strategy configuration for the coordinator."""

    strategy_name: str
    weights: Dict[str, float] = field(default_factory=lambda: {
        "ranging": 0.50, "trending": 0.20, "breakout": 0.30, "unknown": 0.30,
    })
    min_score: float = 0.30             # minimum score to participate
    enabled: bool = True


@dataclass
class CoordinatorConfig:
    """Global coordinator configuration."""

    allocations: List[StrategyAllocation] = field(default_factory=list)
    switch_cooldown_bars: int = 5        # bars before switching strategy
    min_combined_score: float = 0.30     # min combined score to trade
    conflict_resolution: str = "best_score"  # "best_score" | "vote" | "veto"
    capital_split_mode: str = "weighted"     # "weighted" | "equal" | "best_only"
    max_capital_fraction: float = 0.25       # never deploy >25% per pair


# ═══════════════════════════════════════════════════════════════════════════════
# Strategy vote
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class StrategyVote:
    """One strategy's vote for the current bar."""

    name: str
    direction: str       # "LONG" | "SHORT" | "FLAT"
    confidence: float    # [0, 1] from generate_live
    score: float         # [0, 1] from score(context)
    regime: str
    weight: float        # effective weight after regime adaptation
    enabled: bool = True


@dataclass
class CoordinatedDecision:
    """Output of strategy coordination."""

    direction: str = "FLAT"          # "LONG" | "SHORT" | "FLAT"
    confidence: float = 0.0          # [0, 1]
    capital_fraction: float = 0.0    # [0, 1] how much capital to deploy
    active_strategies: List[str] = field(default_factory=list)
    regime: str = "unknown"
    votes: List[StrategyVote] = field(default_factory=list)
    combined_score: float = 0.0

    @property
    def should_trade(self) -> bool:
        return (
            self.direction != "FLAT"
            and self.confidence >= 0.3
            and self.capital_fraction > 0
        )

    def as_dict(self) -> dict:
        return {
            "direction": self.direction,
            "confidence": self.confidence,
            "capital_fraction": self.capital_fraction,
            "active_strategies": self.active_strategies,
            "regime": self.regime,
            "combined_score": self.combined_score,
            "vote_count": len(self.votes),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# StrategyCoordinator
# ═══════════════════════════════════════════════════════════════════════════════


class StrategyCoordinator:
    """
    Multi-strategy coordinator with regime-adaptive weight allocation.

    Register strategies with per-regime weights, then call coordinate()
    on each bar to get a unified trading decision.
    """

    def __init__(self, cfg: Optional[CoordinatorConfig] = None) -> None:
        self._cfg = cfg or CoordinatorConfig()
        self._strategies: Dict[str, object] = {}      # name → strategy instance
        self._allocations: Dict[str, StrategyAllocation] = {}
        self._outcomes: Dict[str, List[float]] = {}    # name → list of PnLs
        self._active_name: Optional[str] = None
        self._switch_cooldown: int = 0

        # Load allocations from config
        for alloc in self._cfg.allocations:
            self._allocations[alloc.strategy_name] = alloc

    # ── Registration ────────────────────────────────────────────────────────

    def register(self, strategy: object, allocation: StrategyAllocation) -> None:
        """
        Register a strategy instance.

        Parameters
        ----------
        strategy : BaseStrategy (from strategy/base.py)
            Must have .name, .score(context), .generate_live(...)
        allocation : StrategyAllocation
            Per-regime weights and thresholds.
        """
        name = allocation.strategy_name
        self._strategies[name] = strategy
        self._allocations[name] = allocation
        self._outcomes.setdefault(name, [])
        logger.info(
            "StrategyCoordinator: registered {} weights={}",
            name, allocation.weights,
        )

    def unregister(self, name: str) -> None:
        self._strategies.pop(name, None)
        self._allocations.pop(name, None)
        self._outcomes.pop(name, None)

    # ── Coordination ────────────────────────────────────────────────────────

    def coordinate(self, context: object) -> CoordinatedDecision:
        """
        Run all strategies and produce a unified decision.

        Parameters
        ----------
        context : MarketContext
            From strategy/base.py: zscore, half_life_hours, vol_rank,
            regime, funding_annual, coint_pvalue, spread_autocorr,
            recent_win_rate, n_bars_since_entry, is_warm.

        Returns
        -------
        CoordinatedDecision
        """
        if not context.is_warm:
            return CoordinatedDecision()

        regime = getattr(context, "regime", "unknown")
        votes: List[StrategyVote] = []

        # 1. Score all strategies and collect votes
        for name, strategy in self._strategies.items():
            alloc = self._allocations.get(name)
            if alloc is None or not alloc.enabled:
                continue

            # Get strategy score for current context
            try:
                score = float(strategy.score(context))
            except Exception:
                score = 0.0

            if score < alloc.min_score:
                continue

            # Regime weight
            regime_weight = alloc.weights.get(regime, alloc.weights.get("unknown", 0.3))
            if regime_weight <= 0:
                continue

            # Generate signal
            try:
                trade_signal = strategy.generate_live(
                    y=getattr(context, "price_y", 0.0),
                    x=getattr(context, "price_x", 0.0),
                    ts=getattr(context, "timestamp", None),
                    funding_annual=getattr(context, "funding_annual", 0.0),
                    regime_multiplier=1.0,
                    coint_valid=True,
                )
                direction = self._signal_to_direction(trade_signal)
                confidence = getattr(trade_signal, "confidence", 0.5)
            except Exception as exc:
                logger.debug("Strategy {} generate_live failed: {}", name, exc)
                direction = "FLAT"
                confidence = 0.0

            vote = StrategyVote(
                name=name,
                direction=direction,
                confidence=float(confidence),
                score=score,
                regime=regime,
                weight=regime_weight * score,  # effective weight = regime_weight * score
            )
            votes.append(vote)

        if not votes:
            return CoordinatedDecision(regime=regime)

        # 2. Resolve conflicts
        direction, confidence, combined_score = self._resolve(votes)

        # 3. Compute capital fraction
        capital_frac = self._capital_fraction(confidence, combined_score)

        # 4. Hysteresis / cooldown
        if self._switch_cooldown > 0:
            self._switch_cooldown -= 1

        decision = CoordinatedDecision(
            direction=direction,
            confidence=confidence,
            capital_fraction=capital_frac,
            active_strategies=[v.name for v in votes if v.direction != "FLAT"],
            regime=regime,
            votes=votes,
            combined_score=combined_score,
        )

        return decision

    # ── Outcome tracking ────────────────────────────────────────────────────

    def record_outcome(self, strategy_name: str, pnl: float) -> None:
        """Record trade outcome for a strategy (used for learning)."""
        self._outcomes.setdefault(strategy_name, []).append(pnl)

    def get_win_rate(self, strategy_name: str, window: int = 20) -> float:
        """Recent win rate for a strategy."""
        outcomes = self._outcomes.get(strategy_name, [])
        if not outcomes:
            return 0.5
        recent = outcomes[-window:]
        wins = sum(1 for p in recent if p > 0)
        return wins / len(recent) if recent else 0.5

    # ── API helpers ─────────────────────────────────────────────────────────

    def get_active_weights(self, regime: str) -> Dict[str, float]:
        """Return effective weight per strategy for a given regime."""
        weights: Dict[str, float] = {}
        for name, alloc in self._allocations.items():
            if alloc.enabled:
                weights[name] = alloc.weights.get(regime, alloc.weights.get("unknown", 0.3))
        return weights

    def snapshot(self) -> dict:
        """Return full state for API / dashboard."""
        return {
            "strategies": list(self._strategies.keys()),
            "allocations": {
                name: {
                    "weights": alloc.weights,
                    "min_score": alloc.min_score,
                    "win_rate": self.get_win_rate(name),
                }
                for name, alloc in self._allocations.items()
            },
            "config": {
                "conflict_resolution": self._cfg.conflict_resolution,
                "capital_split_mode": self._cfg.capital_split_mode,
                "switch_cooldown_bars": self._cfg.switch_cooldown_bars,
            },
        }

    # ── Internal ────────────────────────────────────────────────────────────

    @staticmethod
    def _signal_to_direction(signal: object) -> str:
        """Convert TradeSignal / Signal enum to direction string."""
        sig_val = getattr(signal, "signal", None)
        if sig_val is None:
            return "FLAT"
        val = int(sig_val)
        if val == 1:
            return "LONG"
        elif val == -1:
            return "SHORT"
        elif val == 0:
            return "FLAT"
        elif val == 2:
            return "FLAT"  # partial exit → treat as flat for new entries
        return "FLAT"

    def _resolve(self, votes: List[StrategyVote]) -> Tuple[str, float, float]:
        """Resolve conflicting votes into a single decision."""
        # Separate directional votes
        long_votes = [v for v in votes if v.direction == "LONG"]
        short_votes = [v for v in votes if v.direction == "SHORT"]
        flat_votes = [v for v in votes if v.direction == "FLAT"]

        def weighted_score(vs: List[StrategyVote]) -> float:
            if not vs:
                return 0.0
            return sum(v.weight * v.confidence for v in vs)

        long_score = weighted_score(long_votes)
        short_score = weighted_score(short_votes)

        if self._cfg.conflict_resolution == "best_score":
            # Pick the side with the highest weighted score
            if long_score > short_score and long_score >= self._cfg.min_combined_score:
                return ("LONG", min(1.0, long_score), long_score)
            elif short_score > long_score and short_score >= self._cfg.min_combined_score:
                return ("SHORT", min(1.0, short_score), short_score)
            else:
                return ("FLAT", 0.0, max(long_score, short_score))

        elif self._cfg.conflict_resolution == "vote":
            # Majority vote (by weight)
            total_weight = sum(v.weight for v in votes)
            if total_weight < 1e-9:
                return ("FLAT", 0.0, 0.0)
            long_pct = sum(v.weight for v in long_votes) / total_weight
            short_pct = sum(v.weight for v in short_votes) / total_weight
            if long_pct > 0.5:
                return ("LONG", long_pct, long_pct)
            elif short_pct > 0.5:
                return ("SHORT", short_pct, short_pct)
            else:
                return ("FLAT", 0.0, max(long_pct, short_pct))

        elif self._cfg.conflict_resolution == "veto":
            # If BOTH sides have votes → FLAT (veto each other)
            if long_votes and short_votes:
                return ("FLAT", 0.0, 0.0)
            elif long_votes and long_score >= self._cfg.min_combined_score:
                return ("LONG", min(1.0, long_score), long_score)
            elif short_votes and short_score >= self._cfg.min_combined_score:
                return ("SHORT", min(1.0, short_score), short_score)
            else:
                return ("FLAT", 0.0, 0.0)

        return ("FLAT", 0.0, 0.0)

    def _capital_fraction(self, confidence: float, combined_score: float) -> float:
        """Compute how much capital to deploy (0–1)."""
        if confidence < self._cfg.min_combined_score:
            return 0.0

        if self._cfg.capital_split_mode == "best_only":
            frac = min(0.25, combined_score * 0.3)
        elif self._cfg.capital_split_mode == "equal":
            n = max(1, len(self._strategies))
            frac = min(0.25, 1.0 / n)
        else:  # weighted
            frac = min(self._cfg.max_capital_fraction, combined_score * 0.35)

        return round(frac, 4)
