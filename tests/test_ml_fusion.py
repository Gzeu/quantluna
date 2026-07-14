"""
tests/test_ml_fusion.py — Unit tests for SignalFusion (S47).

Tests regime-adaptive blending, confidence gating, and agreement bonus.
"""
from __future__ import annotations

import numpy as np
import pytest

from strategy.ml.config import MLConfig
from strategy.ml.signal_fusion import FusedSignal, SignalFusion


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def cfg():
    return MLConfig()


@pytest.fixture
def fusion(cfg):
    return SignalFusion(cfg)


# ── FusedSignal dataclass ───────────────────────────────────────────────


class TestFusedSignal:
    def test_default(self):
        fs = FusedSignal()
        assert fs.score == 0.0
        assert fs.direction == "FLAT"
        assert not fs.should_trade

    def test_should_trade_positive(self):
        fs = FusedSignal(score=0.5, direction="LONG", strength="moderate")
        assert fs.should_trade

    def test_should_trade_vetoed(self):
        fs = FusedSignal(score=0.5, veto=True, veto_reason="test")
        assert not fs.should_trade

    def test_should_trade_below_threshold(self):
        fs = FusedSignal(score=0.1)
        assert not fs.should_trade

    def test_as_dict(self):
        fs = FusedSignal(
            score=0.7, direction="LONG", strength="moderate",
            ml_contribution=0.3, z_contribution=0.7,
            regime="trending",
        )
        d = fs.as_dict()
        assert d["direction"] == "LONG"
        assert d["ml_contribution"] == 0.3
        assert d["regime"] == "trending"
        assert d["should_trade"] is True


# ── SignalFusion ────────────────────────────────────────────────────────


class TestSignalFusionFuse:
    def test_fuse_returns_fused_signal(self, fusion):
        r = fusion.fuse(
            ml_direction=0.5, ml_confidence=0.8,
            zscore=3.0, zscore_threshold=2.0,
            regime="trending",
        )
        assert isinstance(r, FusedSignal)

    def test_fuse_trending_gives_higher_ml_weight(self, fusion):
        # Trending should give more ML weight than ranging
        r = fusion.fuse(
            ml_direction=0.8, ml_confidence=0.9,
            zscore=3.0, zscore_threshold=2.0,
            regime="trending",
        )
        ml_w_trend = r.ml_weight_used

        r2 = fusion.fuse(
            ml_direction=0.8, ml_confidence=0.9,
            zscore=3.0, zscore_threshold=2.0,
            regime="ranging",
        )
        ml_w_ranging = r2.ml_weight_used

        assert ml_w_trend > ml_w_ranging, (
            f"Expected ml_weight(trending)={ml_w_trend} > "
            f"ml_weight(ranging)={ml_w_ranging}"
        )

    def test_low_confidence_reduces_ml_weight(self, fusion):
        # Low ML confidence should halve the weight
        r_high = fusion.fuse(
            ml_direction=0.8, ml_confidence=0.9,
            zscore=3.0, zscore_threshold=2.0,
            regime="trending",
        )
        r_low = fusion.fuse(
            ml_direction=0.8, ml_confidence=0.1,
            zscore=3.0, zscore_threshold=2.0,
            regime="trending",
        )
        assert r_low.ml_weight_used < r_high.ml_weight_used

    def test_fuse_direction_long(self, fusion):
        r = fusion.fuse(
            ml_direction=0.7, ml_confidence=0.9,
            zscore=-2.5, zscore_threshold=2.0,
            regime="trending",
        )
        # Both ML and Z-score point the same way → score should be positive
        assert r.score > 0.0
        assert r.direction == "LONG"

    def test_fuse_direction_flat(self, fusion):
        r = fusion.fuse(
            ml_direction=0.01, ml_confidence=0.5,
            zscore=0.1, zscore_threshold=2.0,
            regime="ranging",
        )
        assert r.direction == "FLAT"

    def test_fuse_clamps_weights(self, fusion):
        fusion._cfg.ml_weight_max = 0.4
        fusion._cfg.zscore_weight_min = 0.5
        w = fusion.get_fusion_weights("trending", ml_confidence=1.0)
        assert w[0] <= 0.4  # ml weight clamped
        assert w[1] >= 0.5  # zscore weight clamped

    def test_agreement_bonus(self, fusion):
        # Both ML and Z-score pointing LONG
        r = fusion.fuse(
            ml_direction=0.8, ml_confidence=0.9,
            zscore=-3.0, zscore_threshold=2.0,  # z<0 = LONG_SPREAD
            regime="trending",
        )
        assert r.agreement_bonus >= 0.0

    def test_no_agreement_bonus_when_disagree(self, fusion):
        # ML says LONG, Z-score says SHORT
        r = fusion.fuse(
            ml_direction=0.8, ml_confidence=0.9,
            zscore=3.0, zscore_threshold=2.0,  # z>0 = SHORT_SPREAD
            regime="trending",
        )
        assert r.agreement_bonus == 0.0

    def test_veto_triggered(self, fusion):
        r = fusion.fuse(
            ml_direction=0.0, ml_confidence=0.05,
            zscore=0.0, zscore_threshold=2.0,
            regime="unknown",
            zscore_confidence=0.1,
        )
        assert r.veto
        assert "low confidence" in r.veto_reason.lower()
        assert not r.should_trade

    def test_fuse_history_capped(self, fusion):
        for i in range(150):
            fusion.fuse(
                ml_direction=float(i % 3 - 1),
                ml_confidence=0.7,
                zscore=float(i % 5 - 2),
                zscore_threshold=2.0,
                regime=["trending", "ranging", "breakout"][i % 3],
            )
        assert len(fusion.history) <= 100


class TestSignalFusionWeights:
    def test_all_regime_weights(self, fusion):
        rw = fusion.get_all_regime_weights()
        assert "trending" in rw
        assert "ranging" in rw
        assert "breakout" in rw
        assert "unknown" in rw
        for reg in rw:
            assert "ml_weight" in rw[reg]
            assert "zscore_weight" in rw[reg]

    def test_breakout_highest_ml_weight(self, fusion):
        rw = fusion.get_all_regime_weights(ml_confidence=0.8)
        assert rw["breakout"]["ml_weight"] >= rw["ranging"]["ml_weight"]
        assert rw["breakout"]["ml_weight"] >= rw["trending"]["ml_weight"]

    def test_ranging_lowest_ml_weight(self, fusion):
        rw = fusion.get_all_regime_weights(ml_confidence=0.8)
        assert rw["ranging"]["ml_weight"] == min(
            rw[r]["ml_weight"] for r in rw
        )


class TestSignalFusionSnapshot:
    def test_snapshot_returns_dict(self, fusion):
        snap = fusion.snapshot()
        assert isinstance(snap, dict)
        assert "config" in snap
        assert "regime_weights" in snap

    def test_snapshot_history_count(self, fusion):
        for _ in range(5):
            fusion.fuse(
                ml_direction=0.5, ml_confidence=0.7,
                zscore=2.0, zscore_threshold=2.0,
                regime="trending",
            )
        snap = fusion.snapshot()
        assert snap["history_count"] == 5
