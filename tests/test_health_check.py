"""
tests/test_health_check.py  —  HealthCheck async unit tests

All exchange calls are mocked — no network required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from execution.health_check import HealthCheck, HealthConfig, HealthReport, CheckResult


@pytest.fixture
def base_config():
    return HealthConfig(
        exchange="bybit",
        sym_y="BTCUSDT",
        sym_x="ETHUSDT",
        api_key="test_key_abc123",
        api_secret="test_secret_xyz",
        check_cache_freshness=False,  # skip cache in unit tests
    )


class TestHealthReport:
    def test_all_passed_true_when_all_critical_pass(self):
        report = HealthReport()
        report.checks = [
            CheckResult("a", True, "ok", critical=True),
            CheckResult("b", True, "ok", critical=True),
            CheckResult("c", False, "warn", critical=False),  # non-critical fail
        ]
        assert report.all_passed is True

    def test_all_passed_false_when_critical_fails(self):
        report = HealthReport()
        report.checks = [
            CheckResult("a", True, "ok", critical=True),
            CheckResult("b", False, "fail", critical=True),
        ]
        assert report.all_passed is False

    def test_critical_failures_list(self):
        report = HealthReport()
        report.checks = [
            CheckResult("a", False, "fail", critical=True),
            CheckResult("b", False, "warn", critical=False),
        ]
        assert len(report.critical_failures) == 1
        assert report.critical_failures[0].name == "a"


class TestHealthCheckIndividual:
    def test_ccxt_import_check_passes(self, base_config):
        hc = HealthCheck(base_config)
        result = hc._check_ccxt_import()
        # ccxt is in requirements.txt, so it should be installed in CI
        assert result.name == "ccxt_import"

    def test_api_credentials_present(self, base_config):
        hc = HealthCheck(base_config)
        result = hc._check_api_credentials()
        assert result.passed is True

    def test_api_credentials_missing(self):
        cfg = HealthConfig(exchange="bybit", sym_y="BTCUSDT", sym_x="ETHUSDT")
        hc = HealthCheck(cfg)
        result = hc._check_api_credentials()
        assert result.passed is False

    def test_config_constraints_ok(self, base_config):
        hc = HealthCheck(base_config)
        result = hc._check_config_constraints()
        assert result.passed is True

    def test_config_constraints_bad_kelly(self):
        cfg = HealthConfig(
            exchange="bybit", sym_y="BTCUSDT", sym_x="ETHUSDT",
            max_kelly_fraction=0.90,  # too high
        )
        hc = HealthCheck(cfg)
        result = hc._check_config_constraints()
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_run_returns_health_report(self, base_config):
        hc = HealthCheck(base_config)
        with patch("execution.health_check.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.side_effect = [
                # load_markets for connectivity
                {"BTC/USDT:USDT": {}, "ETH/USDT:USDT": {}},
                # load_markets for symbols
                {"BTC/USDT:USDT": {}, "ETH/USDT:USDT": {}},
                # fetch_balance
                {"USDT": {"free": 5000.0}},
            ]
            report = await hc.run()
        assert isinstance(report, HealthReport)
        assert len(report.checks) >= 5
