"""
tests/test_monitoring_watchdog.py  —  QuantLuna MonitoringWatchdog unit tests
Sprint S44b (2026-07-12)

Ruleaza cu:
    pytest tests/test_monitoring_watchdog.py -v
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from core.monitoring_watchdog import MonitoringWatchdog, PairThreshold

PAIR = "BTCUSDT-ETHUSDT"


def make_watchdog(
    metrics: dict | None = None,
    dispatcher=None,
    halt_cb=None,
    reduce_cb=None,
    thresholds: dict | None = None,
) -> MonitoringWatchdog:
    async def _metrics_provider(pair: str) -> dict:
        return metrics or {
            "sharpe": 1.5,
            "drawdown": 0.02,
            "z_score": 1.0,
            "half_life": 24.0,
            "loss_streak": 0,
        }

    thr = thresholds or {
        PAIR: PairThreshold(
            pair=PAIR,
            sharpe_min=0.3,
            max_drawdown=0.10,
            z_max=4.0,
            hl_max=96.0,
            loss_streak=5,
            action="ALERT_ONLY",
        )
    }

    return MonitoringWatchdog(
        thresholds=thr,
        metrics_provider=_metrics_provider,
        dispatcher=dispatcher or AsyncMock(),
        halt_callback=halt_cb,
        reduce_callback=reduce_cb,
        check_interval=60,
    )


@pytest.mark.asyncio
async def test_threshold_violation_sharpe():
    dispatcher = AsyncMock()
    wd = make_watchdog(
        metrics={
            "sharpe": 0.1,
            "drawdown": 0.01,
            "z_score": 1.0,
            "half_life": 10.0,
            "loss_streak": 0,
        },
        dispatcher=dispatcher,
    )
    await wd._check_all()

    assert len(wd._alerts) >= 1
    alert = next(a for a in wd._alerts if a.metric == "sharpe")
    assert alert.pair == PAIR
    assert alert.value == pytest.approx(0.1)
    assert alert.severity == "WARNING"
    dispatcher.emit.assert_called()


@pytest.mark.asyncio
async def test_threshold_no_violation():
    wd = make_watchdog(
        metrics={
            "sharpe": 2.0,
            "drawdown": 0.03,
            "z_score": 1.5,
            "half_life": 30.0,
            "loss_streak": 1,
        }
    )
    await wd._check_all()
    assert len(wd._alerts) == 0


@pytest.mark.asyncio
async def test_drawdown_triggers_halt():
    halt_mock = AsyncMock()
    wd = make_watchdog(
        metrics={
            "sharpe": 1.0,
            "drawdown": 0.25,
            "z_score": 1.0,
            "half_life": 10.0,
            "loss_streak": 0,
        },
        halt_cb=halt_mock,
        thresholds={
            PAIR: PairThreshold(
                pair=PAIR,
                sharpe_min=0.3,
                max_drawdown=0.10,
                z_max=4.0,
                hl_max=96.0,
                loss_streak=5,
                action="HALT",
            )
        },
    )
    await wd._check_all()

    halt_mock.assert_called_once_with(PAIR)
    alert = next((a for a in wd._alerts if a.metric == "drawdown"), None)
    assert alert is not None
    assert alert.action == "HALT"
    assert alert.severity == "CRITICAL"


@pytest.mark.asyncio
async def test_silence_suppresses_alerts():
    wd = make_watchdog(
        metrics={
            "sharpe": 0.05,
            "drawdown": 0.5,
            "z_score": 9.0,
            "half_life": 200.0,
            "loss_streak": 10,
        }
    )
    wd.silence(PAIR, 60)
    await wd._check_all()
    assert len(wd._alerts) == 0


@pytest.mark.asyncio
async def test_silence_expired_resumes():
    wd = make_watchdog(
        metrics={
            "sharpe": 0.05,
            "drawdown": 0.01,
            "z_score": 0.5,
            "half_life": 10.0,
            "loss_streak": 0,
        }
    )
    wd._thresholds[PAIR].silenced_until = datetime.now(timezone.utc) - timedelta(minutes=1)
    await wd._check_all()
    assert len(wd._alerts) >= 1


def test_update_threshold():
    wd = make_watchdog()
    wd.update_threshold(PAIR, sharpe_min=0.8, action="HALT")
    thr = wd._thresholds[PAIR]
    assert thr.sharpe_min == pytest.approx(0.8)
    assert thr.action == "HALT"


def test_update_threshold_new_pair():
    wd = make_watchdog()
    wd.update_threshold("SOLUSDT-AVAXUSDT", sharpe_min=0.5)
    assert "SOLUSDT-AVAXUSDT" in wd._thresholds
    assert wd._thresholds["SOLUSDT-AVAXUSDT"].sharpe_min == pytest.approx(0.5)


def test_get_status_structure():
    wd = make_watchdog()
    status = wd.get_status()
    for key in ("running", "check_count", "last_check", "pairs_count", "alerts_total", "recent_alerts"):
        assert key in status


def test_get_thresholds_all_pairs():
    wd = make_watchdog()
    thr = wd.get_thresholds()
    assert PAIR in thr
    assert "sharpe_min" in thr[PAIR]
    assert "action" in thr[PAIR]


@patch.dict("os.environ", {"WATCHDOG_ENABLED": "false"})
def test_from_env_disabled():
    async def dummy_provider(pair):
        return {}

    wd = MonitoringWatchdog.from_env(
        pairs=[PAIR],
        metrics_provider=dummy_provider,
        dispatcher=AsyncMock(),
    )
    assert wd._thresholds == {}


@patch.dict(
    "os.environ",
    {
        "WATCHDOG_SHARPE_MIN": "0.8",
        "WATCHDOG_MAX_DD": "0.05",
        "WATCHDOG_Z_MAX": "3.0",
        "WATCHDOG_CHECK_INTERVAL": "30",
    },
)
def test_from_env_reads_env_vars():
    async def dummy(pair):
        return {}

    wd = MonitoringWatchdog.from_env(
        pairs=[PAIR],
        metrics_provider=dummy,
        dispatcher=AsyncMock(),
    )
    thr = wd._thresholds[PAIR]
    assert thr.sharpe_min == pytest.approx(0.8)
    assert thr.max_drawdown == pytest.approx(0.05)
    assert thr.z_max == pytest.approx(3.0)
    assert wd._check_interval == 30


@pytest.mark.asyncio
async def test_max_history_cap():
    wd = make_watchdog(
        metrics={
            "sharpe": 0.01,
            "drawdown": 0.01,
            "z_score": 0.1,
            "half_life": 1.0,
            "loss_streak": 0,
        }
    )
    for _ in range(250):
        await wd._check_all()

    assert len(wd._alerts) <= MonitoringWatchdog.MAX_HISTORY
