"""
tests/test_state_bus.py — S36
Teste unitare pentru StateBus (core/state_bus.py).
StateBus este un singleton pub/sub folosit de toate engine-urile.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def fresh_bus():
    """Reseteaza StateBus inainte de fiecare test."""
    from core.state_bus import StateBus
    bus = StateBus.get_instance()
    bus.reset()  # curata toate subscriptiile si valorile
    yield bus
    bus.reset()


# ─ singleton ─────────────────────────────────────────────────────────────────

class TestStateBusSingleton:
    def test_same_instance(self):
        from core.state_bus import StateBus
        a = StateBus.get_instance()
        b = StateBus.get_instance()
        assert a is b

    def test_instance_not_none(self):
        from core.state_bus import StateBus
        assert StateBus.get_instance() is not None


# ─ publish / subscribe ────────────────────────────────────────────────────────

class TestStateBusPublishSubscribe:
    def test_subscribe_and_receive(self, fresh_bus):
        received = []
        fresh_bus.subscribe("equity", lambda v: received.append(v))
        fresh_bus.publish("equity", 12345.0)
        assert received == [12345.0]

    def test_last_value_after_publish(self, fresh_bus):
        fresh_bus.publish("sharpe", 1.5)
        assert fresh_bus.last_value("sharpe") == 1.5

    def test_last_value_none_before_publish(self, fresh_bus):
        assert fresh_bus.last_value("nonexistent_key") is None

    def test_multiple_subscribers_same_key(self, fresh_bus):
        calls = []
        fresh_bus.subscribe("drawdown", lambda v: calls.append(("a", v)))
        fresh_bus.subscribe("drawdown", lambda v: calls.append(("b", v)))
        fresh_bus.publish("drawdown", 0.05)
        assert len(calls) == 2
        assert all(v == 0.05 for _, v in calls)

    def test_different_keys_independent(self, fresh_bus):
        r1, r2 = [], []
        fresh_bus.subscribe("k1", lambda v: r1.append(v))
        fresh_bus.subscribe("k2", lambda v: r2.append(v))
        fresh_bus.publish("k1", 1)
        fresh_bus.publish("k2", 2)
        assert r1 == [1] and r2 == [2]

    def test_publish_multiple_times_last_value_updated(self, fresh_bus):
        fresh_bus.publish("win_rate", 0.5)
        fresh_bus.publish("win_rate", 0.7)
        assert fresh_bus.last_value("win_rate") == 0.7
