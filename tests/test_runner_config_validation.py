"""
tests/test_runner_config_validation.py

Unit tests for BybitLiveRunnerConfig.__post_init__ validation (fix #23).

Covers every guard added in runner_config.py:
  - entry_zscore <= 0
  - exit_zscore < 0
  - exit_zscore >= entry_zscore
  - base_qty <= 0
  - warmup_bars < 20
  - kalman_window < warmup_bars
  - max_drawdown_pct out of (0, 100]
  - cooldown_seconds < 0
  - max_consec_losses < 1
  - initial_capital <= 0
  - interval not in valid set
  - half_life_h <= 0
  - valid config passes without raising
  - multiple errors accumulate in one ValueError
"""
from __future__ import annotations

import os
import pytest

# Ensure env vars don't contaminate tests
for _var in [
    "ENTRY_ZSCORE", "EXIT_ZSCORE", "BASE_QTY", "WARMUP_BARS",
    "KALMAN_WINDOW", "MAX_DRAWDOWN_PCT", "COOLDOWN_SECONDS",
    "MAX_CONSEC_LOSSES", "INITIAL_CAPITAL", "INTERVAL", "HALF_LIFE_H",
    "BYBIT_API_KEY", "BYBIT_API_SECRET",
]:
    os.environ.pop(_var, None)


def _make(**overrides):
    """Build a valid config with selected field overrides."""
    from execution.runner_config import BybitLiveRunnerConfig
    defaults = dict(
        symbol_y="BTCUSDT", symbol_x="ETHUSDT", interval=5,
        dry_run=True, api_key="", api_secret="", testnet=False,
        entry_zscore=2.0, exit_zscore=0.5, base_qty=0.01,
        warmup_bars=100, kalman_window=200, half_life_h=24.0,
        initial_capital=10000.0,
        max_consec_losses=3, max_drawdown_pct=10.0, cooldown_seconds=300,
        telegram_bot_token="", telegram_chat_id="", slack_webhook_url="",
        health_port=8081,
        funding_gate_enabled=True, pnl_reconciler_enabled=True,
        market_trade_enabled=True,
        enable_reoptimizer=True, enable_watchdog=True,
        enable_spot=False, enable_margin=False,
        checkpoint_path="state/position_checkpoint.db",
    )
    defaults.update(overrides)
    return BybitLiveRunnerConfig(**defaults)


# ------------------------------------------------------------------ #
# Valid config — must NOT raise                                        #
# ------------------------------------------------------------------ #

class TestValidConfig:
    def test_default_valid_config_passes(self):
        cfg = _make()
        assert cfg.entry_zscore == 2.0
        assert cfg.exit_zscore == 0.5

    def test_valid_boundary_warmup_20(self):
        cfg = _make(warmup_bars=20, kalman_window=20)
        assert cfg.warmup_bars == 20

    def test_valid_max_drawdown_100(self):
        cfg = _make(max_drawdown_pct=100.0)
        assert cfg.max_drawdown_pct == 100.0

    def test_valid_cooldown_zero(self):
        cfg = _make(cooldown_seconds=0)
        assert cfg.cooldown_seconds == 0

    def test_summary_method(self):
        cfg = _make()
        s = cfg.summary()
        assert "BTCUSDT" in s
        assert "2.0" in s

    def test_is_live_false_dry(self):
        cfg = _make(dry_run=True, api_key="k", api_secret="s")
        assert not cfg.is_live()

    def test_is_live_true(self):
        cfg = _make(dry_run=False, api_key="key", api_secret="secret")
        assert cfg.is_live()

    def test_all_valid_intervals(self):
        from execution.runner_config import _VALID_INTERVALS
        for iv in _VALID_INTERVALS:
            cfg = _make(interval=iv)
            assert cfg.interval == iv


# ------------------------------------------------------------------ #
# entry_zscore                                                         #
# ------------------------------------------------------------------ #

class TestEntryZscore:
    def test_zero_entry_zscore_raises(self):
        with pytest.raises(ValueError, match="ENTRY_ZSCORE"):
            _make(entry_zscore=0.0)

    def test_negative_entry_zscore_raises(self):
        with pytest.raises(ValueError, match="ENTRY_ZSCORE"):
            _make(entry_zscore=-1.0)


# ------------------------------------------------------------------ #
# exit_zscore                                                          #
# ------------------------------------------------------------------ #

class TestExitZscore:
    def test_negative_exit_zscore_raises(self):
        with pytest.raises(ValueError, match="EXIT_ZSCORE"):
            _make(exit_zscore=-0.1)

    def test_exit_equals_entry_raises(self):
        with pytest.raises(ValueError, match="EXIT_ZSCORE.*must be < ENTRY_ZSCORE"):
            _make(entry_zscore=2.0, exit_zscore=2.0)

    def test_exit_greater_than_entry_raises(self):
        with pytest.raises(ValueError, match="EXIT_ZSCORE.*must be < ENTRY_ZSCORE"):
            _make(entry_zscore=2.0, exit_zscore=3.0)

    def test_exit_zero_valid(self):
        cfg = _make(entry_zscore=2.0, exit_zscore=0.0)
        assert cfg.exit_zscore == 0.0


# ------------------------------------------------------------------ #
# base_qty                                                             #
# ------------------------------------------------------------------ #

class TestBaseQty:
    def test_zero_base_qty_raises(self):
        with pytest.raises(ValueError, match="BASE_QTY"):
            _make(base_qty=0.0)

    def test_negative_base_qty_raises(self):
        with pytest.raises(ValueError, match="BASE_QTY"):
            _make(base_qty=-0.01)


# ------------------------------------------------------------------ #
# warmup_bars / kalman_window                                          #
# ------------------------------------------------------------------ #

class TestWarmupBars:
    def test_warmup_below_20_raises(self):
        with pytest.raises(ValueError, match="WARMUP_BARS"):
            _make(warmup_bars=19)

    def test_warmup_zero_raises(self):
        with pytest.raises(ValueError, match="WARMUP_BARS"):
            _make(warmup_bars=0)

    def test_kalman_window_less_than_warmup_raises(self):
        with pytest.raises(ValueError, match="KALMAN_WINDOW"):
            _make(warmup_bars=100, kalman_window=50)


# ------------------------------------------------------------------ #
# max_drawdown_pct                                                     #
# ------------------------------------------------------------------ #

class TestMaxDrawdown:
    def test_drawdown_zero_raises(self):
        with pytest.raises(ValueError, match="MAX_DRAWDOWN_PCT"):
            _make(max_drawdown_pct=0.0)

    def test_drawdown_above_100_raises(self):
        with pytest.raises(ValueError, match="MAX_DRAWDOWN_PCT"):
            _make(max_drawdown_pct=101.0)

    def test_drawdown_negative_raises(self):
        with pytest.raises(ValueError, match="MAX_DRAWDOWN_PCT"):
            _make(max_drawdown_pct=-5.0)


# ------------------------------------------------------------------ #
# cooldown_seconds                                                     #
# ------------------------------------------------------------------ #

class TestCooldown:
    def test_negative_cooldown_raises(self):
        with pytest.raises(ValueError, match="COOLDOWN_SECONDS"):
            _make(cooldown_seconds=-1)


# ------------------------------------------------------------------ #
# max_consec_losses                                                    #
# ------------------------------------------------------------------ #

class TestConsecLosses:
    def test_zero_consec_losses_raises(self):
        with pytest.raises(ValueError, match="MAX_CONSEC_LOSSES"):
            _make(max_consec_losses=0)


# ------------------------------------------------------------------ #
# initial_capital                                                      #
# ------------------------------------------------------------------ #

class TestInitialCapital:
    def test_zero_capital_raises(self):
        with pytest.raises(ValueError, match="INITIAL_CAPITAL"):
            _make(initial_capital=0.0)

    def test_negative_capital_raises(self):
        with pytest.raises(ValueError, match="INITIAL_CAPITAL"):
            _make(initial_capital=-1000.0)


# ------------------------------------------------------------------ #
# interval                                                             #
# ------------------------------------------------------------------ #

class TestInterval:
    def test_invalid_interval_raises(self):
        with pytest.raises(ValueError, match="INTERVAL"):
            _make(interval=7)  # not in valid set

    def test_interval_2_raises(self):
        with pytest.raises(ValueError, match="INTERVAL"):
            _make(interval=2)


# ------------------------------------------------------------------ #
# half_life_h                                                          #
# ------------------------------------------------------------------ #

class TestHalfLife:
    def test_zero_half_life_raises(self):
        with pytest.raises(ValueError, match="HALF_LIFE_H"):
            _make(half_life_h=0.0)

    def test_negative_half_life_raises(self):
        with pytest.raises(ValueError, match="HALF_LIFE_H"):
            _make(half_life_h=-1.0)


# ------------------------------------------------------------------ #
# Multi-error accumulation                                             #
# ------------------------------------------------------------------ #

class TestMultipleErrors:
    def test_multiple_invalid_fields_all_reported(self):
        """
        When multiple fields are invalid, ALL errors must appear
        in a single ValueError (not just the first one).
        """
        with pytest.raises(ValueError) as exc_info:
            _make(
                entry_zscore=0.0,    # [1] ENTRY_ZSCORE
                base_qty=0.0,        # [2] BASE_QTY
                warmup_bars=5,       # [3] WARMUP_BARS
                max_drawdown_pct=0,  # [4] MAX_DRAWDOWN_PCT
            )
        msg = str(exc_info.value)
        assert "ENTRY_ZSCORE" in msg
        assert "BASE_QTY" in msg
        assert "WARMUP_BARS" in msg
        assert "MAX_DRAWDOWN_PCT" in msg
        # Should be numbered
        assert "[1]" in msg
        assert "[4]" in msg
