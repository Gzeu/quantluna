"""
tests/test_cointegration.py  —  Cointegration tests suite

Covers:
  - EngleGrangerTest cu pereche cointegrated vs necointegrated
  - CointegrationConfig parametrizabil (nu hardcodat 0.05)
  - CointegrationConfig.conservative() / liberal() presets
  - CointegrationConfig.from_env() — override din env vars
  - CointegrationConfig.__post_init__ validation
  - to_engle_granger_kwargs() / to_johansen_kwargs()
  - StrategyConfig.from_optimizer_json()
  - StrategyConfig constraint validation
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cointegrated_pair(rng):
    """Pereche clar cointegrated — EG test trebuie să dea p < 0.05."""
    n = 500
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    x = 100 + np.cumsum(rng.normal(0, 0.3, n))
    y = 1.5 * x + 10 + rng.normal(0, 0.5, n)  # tight cointegration
    return pd.Series(y, index=idx), pd.Series(x, index=idx)


@pytest.fixture
def independent_pair(rng):
    """Pereche INDEPENDENT — EG test trebuie să dea p > 0.05."""
    n = 500
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    y = 100 + np.cumsum(rng.normal(0, 1, n))
    x = 200 + np.cumsum(rng.normal(0, 1, n))  # independent random walks
    return pd.Series(y, index=idx), pd.Series(x, index=idx)


# ---------------------------------------------------------------------------
# CointegrationConfig
# ---------------------------------------------------------------------------

class TestCointegrationConfig:
    def test_default_adf_alpha(self):
        from config.cointegration_config import CointegrationConfig
        cfg = CointegrationConfig()
        assert cfg.adf_alpha == 0.05

    def test_custom_adf_alpha(self):
        from config.cointegration_config import CointegrationConfig
        cfg = CointegrationConfig(adf_alpha=0.01)
        assert cfg.adf_alpha == 0.01

    def test_invalid_adf_alpha_raises(self):
        from config.cointegration_config import CointegrationConfig
        with pytest.raises(ValueError, match="adf_alpha"):
            CointegrationConfig(adf_alpha=1.5)

    def test_invalid_johansen_signif_raises(self):
        from config.cointegration_config import CointegrationConfig
        with pytest.raises(ValueError, match="johansen_signif"):
            CointegrationConfig(johansen_signif=0.07)  # must be 0.01/0.05/0.10

    def test_half_life_inverted_raises(self):
        from config.cointegration_config import CointegrationConfig
        with pytest.raises(ValueError):
            CointegrationConfig(min_half_life_h=100.0, max_half_life_h=10.0)

    def test_conservative_preset(self):
        from config.cointegration_config import CointegrationConfig
        cfg = CointegrationConfig.conservative()
        assert cfg.adf_alpha == 0.01
        assert cfg.require_both_tests is True
        assert cfg.require_residuals is True

    def test_liberal_preset(self):
        from config.cointegration_config import CointegrationConfig
        cfg = CointegrationConfig.liberal()
        assert cfg.adf_alpha == 0.10
        assert cfg.require_both_tests is False

    def test_to_engle_granger_kwargs(self):
        from config.cointegration_config import CointegrationConfig
        cfg = CointegrationConfig(adf_alpha=0.01, eg_min_obs=200)
        kwargs = cfg.to_engle_granger_kwargs()
        assert kwargs["alpha_threshold"] == 0.01
        assert kwargs["min_obs"] == 200
        assert "trend" in kwargs

    def test_to_johansen_kwargs(self):
        from config.cointegration_config import CointegrationConfig
        cfg = CointegrationConfig(johansen_signif=0.01)
        kwargs = cfg.to_johansen_kwargs()
        assert kwargs["signif"] == 0.01

    def test_from_env(self, monkeypatch):
        from config.cointegration_config import CointegrationConfig
        monkeypatch.setenv("QUANTLUNA_COINT_ADF_ALPHA", "0.01")
        monkeypatch.setenv("QUANTLUNA_COINT_MIN_HALF_LIFE_H", "4.0")
        monkeypatch.setenv("QUANTLUNA_COINT_REQUIRE_BOTH_TESTS", "true")
        cfg = CointegrationConfig.from_env()
        assert cfg.adf_alpha == 0.01
        assert cfg.min_half_life_h == 4.0
        assert cfg.require_both_tests is True


# ---------------------------------------------------------------------------
# EngleGrangerTest cu config parametrizabil
# ---------------------------------------------------------------------------

class TestEngleGrangerWithConfig:
    def test_cointegrated_pair_detected(self, cointegrated_pair):
        from config.cointegration_config import CointegrationConfig
        from strategy.cointegration.engle_granger import EngleGrangerTest
        cfg = CointegrationConfig()
        eg = EngleGrangerTest(**cfg.to_engle_granger_kwargs())
        y, x = cointegrated_pair
        result = eg.run(y, x)
        assert result.is_cointegrated, f"Expected cointegrated, p={result.p_value:.4f}"
        assert result.p_value < cfg.adf_alpha

    def test_independent_pair_not_detected(self, independent_pair):
        from config.cointegration_config import CointegrationConfig
        from strategy.cointegration.engle_granger import EngleGrangerTest
        cfg = CointegrationConfig()
        eg = EngleGrangerTest(**cfg.to_engle_granger_kwargs())
        y, x = independent_pair
        result = eg.run(y, x)
        assert not result.is_cointegrated, f"Expected NOT cointegrated, p={result.p_value:.4f}"

    def test_strict_alpha_harder_to_pass(self, cointegrated_pair, rng):
        """
        Cu alpha=0.001 mai strict, un p=0.03 nu mai trece.
        Generăm o pereche cu cointegration slăbă (mult noise).
        """
        from config.cointegration_config import CointegrationConfig
        from strategy.cointegration.engle_granger import EngleGrangerTest

        n = 300
        idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
        x = 100 + np.cumsum(rng.normal(0, 1, n))
        y = 1.5 * x + 10 + rng.normal(0, 15, n)  # mult noise — cointegration slăbă

        cfg_strict = CointegrationConfig(adf_alpha=0.001)
        cfg_normal = CointegrationConfig(adf_alpha=0.05)

        eg_strict = EngleGrangerTest(**cfg_strict.to_engle_granger_kwargs())
        eg_normal = EngleGrangerTest(**cfg_normal.to_engle_granger_kwargs())

        res_strict = eg_strict.run(pd.Series(y, index=idx), pd.Series(x, index=idx))
        res_normal = eg_normal.run(pd.Series(y, index=idx), pd.Series(x, index=idx))

        # normal poate detecta, strict poate să nu — ambele sunt OK
        # Important: rezultatele sunt consistente cu pragul configurat
        if res_strict.is_cointegrated:
            assert res_strict.p_value < 0.001
        if res_normal.is_cointegrated:
            assert res_normal.p_value < 0.05

    def test_eg_result_has_half_life(self, cointegrated_pair):
        from config.cointegration_config import CointegrationConfig
        from strategy.cointegration.engle_granger import EngleGrangerTest
        cfg = CointegrationConfig()
        eg = EngleGrangerTest(**cfg.to_engle_granger_kwargs())
        y, x = cointegrated_pair
        result = eg.run(y, x)
        if result.is_cointegrated:
            assert result.half_life_bars is not None
            assert result.half_life_bars > 0

    def test_eg_result_summary_str(self, cointegrated_pair):
        from config.cointegration_config import CointegrationConfig
        from strategy.cointegration.engle_granger import EngleGrangerTest
        cfg = CointegrationConfig()
        eg = EngleGrangerTest(**cfg.to_engle_granger_kwargs())
        y, x = cointegrated_pair
        result = eg.run(y, x)
        s = result.summary()
        assert "ADF stat" in s
        assert "p-value" in s

    def test_eg_insufficient_data(self):
        from config.cointegration_config import CointegrationConfig
        from strategy.cointegration.engle_granger import EngleGrangerTest
        cfg = CointegrationConfig()
        eg = EngleGrangerTest(**cfg.to_engle_granger_kwargs())
        y = pd.Series([1.0] * 20)
        x = pd.Series([1.0] * 20)
        result = eg.run(y, x)
        assert not result.is_cointegrated
        assert any("insufficient" in note for note in result.notes)


# ---------------------------------------------------------------------------
# StrategyConfig
# ---------------------------------------------------------------------------

class TestStrategyConfig:
    def test_default_instantiation(self):
        from config.strategy_config import StrategyConfig
        cfg = StrategyConfig()
        assert cfg.sym_y == "BTCUSDT"
        assert cfg.zscore_exit < cfg.zscore_entry
        assert isinstance(cfg.cointegration.adf_alpha, float)

    def test_zscore_constraint_raises(self):
        from config.strategy_config import StrategyConfig
        with pytest.raises(ValueError, match="zscore_exit"):
            StrategyConfig(zscore_exit=3.0, zscore_entry=2.0)

    def test_half_life_constraint_raises(self):
        from config.strategy_config import StrategyConfig
        with pytest.raises(ValueError):
            StrategyConfig(half_life_min_h=200.0, half_life_max_h=10.0)

    def test_from_optimizer_json(self, tmp_path):
        from config.strategy_config import StrategyConfig
        params = {
            "delta": 5e-4,
            "zscore_entry": 2.5,
            "zscore_exit": 0.3,
            "kelly_fraction": 0.30,
        }
        data = {"params": params, "sharpe_test": 1.8}
        p = tmp_path / "best.json"
        p.write_text(json.dumps(data))
        cfg = StrategyConfig.from_optimizer_json(str(p))
        assert cfg.delta == 5e-4
        assert cfg.zscore_entry == 2.5

    def test_from_optimizer_json_unknown_keys_ignored(self, tmp_path):
        from config.strategy_config import StrategyConfig
        params = {"delta": 1e-4, "nonexistent_param": 999}
        p = tmp_path / "best.json"
        p.write_text(json.dumps({"params": params}))
        cfg = StrategyConfig.from_optimizer_json(str(p))
        assert cfg.delta == 1e-4  # known param applied
        assert not hasattr(cfg, "nonexistent_param")  # unknown ignored

    def test_summary_str(self):
        from config.strategy_config import StrategyConfig
        cfg = StrategyConfig()
        s = cfg.summary()
        assert "BTCUSDT" in s
        assert "delta" in s
