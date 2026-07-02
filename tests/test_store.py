"""
QuantLuna — Tests: core/store.py
Sprint 22  |  10 unit tests

Coverage:
  TestMemoryStore (4)    — set/get/delete/all/TTL expiry/eviction
  TestSQLiteStore (3)    — persist/reload/delete + non-serializable sidecar
  TestJobStoreFacade (2) — evict_done logic + __contains__
  TestSelectorStore (1)  — get_or_create + get_summary
"""
from __future__ import annotations

import time
import tempfile
from pathlib import Path

import pytest

from core.store import JobStore, MemoryStore, SelectorStore, SQLiteStore


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------

class TestMemoryStore:

    def test_set_get(self):
        s = MemoryStore()
        s.set("k1", {"a": 1})
        assert s.get("k1") == {"a": 1}

    def test_delete(self):
        s = MemoryStore()
        s.set("k1", 42)
        s.delete("k1")
        assert s.get("k1") is None

    def test_all(self):
        s = MemoryStore()
        s.set("x", 1)
        s.set("y", 2)
        assert set(s.all().keys()) == {"x", "y"}

    def test_ttl_expiry(self):
        s = MemoryStore()
        s.set("temp", "value", ttl=1)   # expires in 1 second
        assert s.get("temp") == "value"
        time.sleep(1.05)
        assert s.get("temp") is None     # should be expired


# ---------------------------------------------------------------------------
# SQLiteStore
# ---------------------------------------------------------------------------

class TestSQLiteStore:

    @pytest.fixture
    def tmp_store(self, tmp_path):
        return SQLiteStore(table="test_kv", db_path=tmp_path / "test.db")

    def test_persist_and_reload(self, tmp_path):
        db = tmp_path / "test.db"
        s1 = SQLiteStore(table="test_kv", db_path=db)
        s1.set("job1", {"status": "done", "sharpe": 1.5})
        # New connection — simulates restart
        s2 = SQLiteStore(table="test_kv", db_path=db)
        obj = s2.get("job1")
        assert obj is not None
        assert obj["status"] == "done"
        assert abs(obj["sharpe"] - 1.5) < 1e-6

    def test_delete(self, tmp_store):
        tmp_store.set("k", {"v": 1})
        tmp_store.delete("k")
        assert tmp_store.get("k") is None

    def test_non_serializable_sidecar(self, tmp_store):
        """
        Non-serializable values (e.g. DataFrames) stored in live sidecar,
        re-attached on get().
        """
        import pandas as pd
        df = pd.DataFrame({"a": [1, 2, 3]})
        tmp_store.set("job_with_df", {"status": "done", "trades_df": df})
        result = tmp_store.get("job_with_df")
        assert result is not None
        assert result["status"] == "done"
        # DataFrame re-attached from live sidecar
        assert result["trades_df"] is not None
        assert len(result["trades_df"]) == 3


# ---------------------------------------------------------------------------
# JobStore facade
# ---------------------------------------------------------------------------

class TestJobStoreFacade:

    def test_contains_and_len(self):
        store = JobStore(backend="memory")
        store.set("abc", {"job_id": "abc", "status": "done", "created_at": "2026-01-01T00:00:00"})
        assert "abc" in store
        assert len(store) == 1

    def test_evict_done_only(self):
        store = JobStore(backend="memory")
        # Add 3 done jobs + 1 running
        for i in range(3):
            store.set(f"done_{i}", {"job_id": f"done_{i}", "status": "done", "created_at": f"2026-01-0{i+1}T00:00:00"})
        store.set("running_1", {"job_id": "running_1", "status": "running", "created_at": "2026-01-04T00:00:00"})

        # Evict with max_total=3, keep_last=1 (should evict 2 oldest done)
        evicted = store.evict_done(max_total=3, keep_last=1)
        assert evicted == 2
        # Running job must still be present
        assert "running_1" in store


# ---------------------------------------------------------------------------
# SelectorStore facade
# ---------------------------------------------------------------------------

class TestSelectorStore:

    def test_get_or_create_and_summary(self):
        from strategy.auto_selector import AutoStrategySelector
        from strategy.bb_mean_reversion import BollingerBandsMeanReversion
        from strategy.zscore_momentum import ZScoreMomentum

        store = SelectorStore(backend="memory")

        def factory():
            return AutoStrategySelector(
                strategies=[
                    BollingerBandsMeanReversion(window=5),
                    ZScoreMomentum(entry_threshold=1.5),
                ],
                hysteresis_bonus=0.10,
            )

        sel = store.get_or_create("live", factory)
        assert sel is not None

        # Second call returns same object
        sel2 = store.get_or_create("live", factory)
        assert sel is sel2

        # Summary is persisted
        summary = store.get_summary("live")
        assert summary is not None
        assert "active_strategy" in summary
        assert "scores" in summary
