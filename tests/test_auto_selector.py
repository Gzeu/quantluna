"""
QuantLuna — Tests: AutoStrategySelector
Sprint 20  |  12 unit tests
"""
from __future__ import annotations
import pytest
from strategy.auto_selector import AutoStrategySelector
from strategy.base import MarketContext
from strategy.bb_mean_reversion import BollingerBandsMeanReversion
from strategy.funding_arb import FundingRateArbitrage
from strategy.zscore_momentum import ZScoreMomentum


@pytest.fixture
def selector():
    return AutoStrategySelector(
        strategies=[
            BollingerBandsMeanReversion(window=5, n_std_entry=2.0),
            ZScoreMomentum(entry_threshold=1.5, momentum_window=2),
            FundingRateArbitrage(entry_funding_annual=0.20),
        ],
        hysteresis_bonus=0.10, min_score_threshold=0.30, switch_cooldown_bars=3,
    )


def _ctx(**kw) -> MarketContext:
    d = dict(zscore=0.0, half_life_hours=24.0, vol_rank=0.5, regime="ranging",
             funding_annual=0.0, coint_pvalue=0.03, spread_autocorr=0.0,
             recent_win_rate=0.5, is_warm=True)
    d.update(kw)
    return MarketContext(**d)


class TestAutoSelectorRanging:
    def test_bb_wins_ranging_normal_vol(self, selector):
        ctx = _ctx(regime="ranging", vol_rank=0.45, spread_autocorr=-0.15, half_life_hours=20.0)
        scores = {s.name: s.score(ctx) for s in selector.strategies}
        assert scores["BollingerBandsMeanReversion"] > scores["ZScoreMomentum"]
        assert scores["BollingerBandsMeanReversion"] > scores["FundingRateArbitrage"]

    def test_zscore_penalised_in_ranging(self, selector):
        ctx = _ctx(regime="ranging", spread_autocorr=-0.10)
        bb = next(s for s in selector.strategies if s.name == "BollingerBandsMeanReversion")
        zs = next(s for s in selector.strategies if s.name == "ZScoreMomentum")
        assert bb.score(ctx) > zs.score(ctx)

    def test_selector_picks_bb_in_ranging(self, selector):
        ctx = _ctx(regime="ranging", vol_rank=0.45, spread_autocorr=-0.15, half_life_hours=20.0)
        selected, _ = selector._select(ctx)
        assert selected is not None and selected.name == "BollingerBandsMeanReversion"


class TestAutoSelectorTrending:
    def test_zscore_wins_trending(self, selector):
        ctx = _ctx(regime="trending", spread_autocorr=0.25, half_life_hours=96.0, vol_rank=0.75)
        zs = next(s for s in selector.strategies if s.name == "ZScoreMomentum")
        bb = next(s for s in selector.strategies if s.name == "BollingerBandsMeanReversion")
        assert zs.score(ctx) > bb.score(ctx)

    def test_zscore_wins_breakout(self, selector):
        ctx = _ctx(regime="breakout", spread_autocorr=0.20, half_life_hours=80.0, vol_rank=0.80)
        zs = next(s for s in selector.strategies if s.name == "ZScoreMomentum")
        bb = next(s for s in selector.strategies if s.name == "BollingerBandsMeanReversion")
        assert zs.score(ctx) > bb.score(ctx)

    def test_no_strategy_below_threshold(self, selector):
        selector.min_score_threshold = 0.99
        selected, _ = selector._select(_ctx(regime="unknown"))
        assert selected is None
        selector.min_score_threshold = 0.30


class TestAutoSelectorFunding:
    def test_funding_arb_wins_extreme_funding(self, selector):
        ctx = _ctx(funding_annual=0.60, regime="ranging", vol_rank=0.45)
        fa = next(s for s in selector.strategies if s.name == "FundingRateArbitrage")
        bb = next(s for s in selector.strategies if s.name == "BollingerBandsMeanReversion")
        zs = next(s for s in selector.strategies if s.name == "ZScoreMomentum")
        assert fa.score(ctx) > bb.score(ctx) and fa.score(ctx) > zs.score(ctx)

    def test_funding_arb_zero_below_threshold(self, selector):
        fa = next(s for s in selector.strategies if s.name == "FundingRateArbitrage")
        assert fa.score(_ctx(funding_annual=0.02)) == 0.0

    def test_funding_arb_selected_by_selector(self, selector):
        ctx = _ctx(funding_annual=0.70, regime="ranging", vol_rank=0.45)
        selected, _ = selector._select(ctx)
        assert selected is not None and selected.name == "FundingRateArbitrage"


class TestHysteresis:
    def test_hysteresis_keeps_active_strategy(self, selector):
        bb = next(s for s in selector.strategies if s.name == "BollingerBandsMeanReversion")
        selector._active_strategy = bb
        selector._active_name = bb.name
        _, scores = selector._select(_ctx(regime="ranging", vol_rank=0.50))
        assert scores["BollingerBandsMeanReversion"] > scores["ZScoreMomentum"]


class TestSwitching:
    def test_switch_resets_old_strategy_state(self, selector):
        bb = next(s for s in selector.strategies if s.name == "BollingerBandsMeanReversion")
        zs = next(s for s in selector.strategies if s.name == "ZScoreMomentum")
        bb._in_trade = True; bb._bars_in_trade = 5; bb._entry_side = 1
        selector._active_strategy = bb; selector._active_name = bb.name
        selector._on_switch(old=bb, new=zs, ts=None)
        assert bb._in_trade is False and bb._bars_in_trade == 0
        assert selector._switch_cooldown_remaining == 3

    def test_switch_history_recorded(self, selector):
        bb = next(s for s in selector.strategies if s.name == "BollingerBandsMeanReversion")
        zs = next(s for s in selector.strategies if s.name == "ZScoreMomentum")
        selector._active_strategy = bb; selector._active_name = bb.name
        selector._on_switch(old=bb, new=zs, ts=None)
        assert len(selector._switch_history) == 1
        assert selector._switch_history[0]["from"] == "BollingerBandsMeanReversion"
        assert selector._switch_history[0]["to"] == "ZScoreMomentum"

    def test_cooldown_prevents_rapid_switch(self, selector):
        bb = next(s for s in selector.strategies if s.name == "BollingerBandsMeanReversion")
        selector._active_strategy = bb; selector._active_name = bb.name
        selector._switch_cooldown_remaining = 3
        selected, _ = selector._select(_ctx(regime="trending", spread_autocorr=0.30, half_life_hours=96.0))
        assert selected is not None and selected.name == "BollingerBandsMeanReversion"


class TestScoresSummary:
    def test_scores_summary_format(self, selector):
        s = selector.scores_summary()
        for k in ("active_strategy", "scores", "recent_win_rate", "switch_history", "total_bars"):
            assert k in s

    def test_scores_summary_initial_state(self, selector):
        s = selector.scores_summary()
        assert s["active_strategy"] == "" and s["scores"] == {}
        assert s["recent_win_rate"] == 0.5 and s["total_bars"] == 0
