"""
QuantLuna — Persistence Store
Sprint 22

Triple-backend store: Memory → SQLite → Redis.
Selеctat automat prin env var QUANTLUNA_STORE_BACKEND:
  memory  — default, zero deps, lost on restart
  sqlite  — stdlib sqlite3, survives restart, single-process
  redis   — multi-process/container, requires redis-py + running Redis

JobStore   — typed facade for backtest _JOBS
SelectorStore — typed facade for AutoStrategySelector instances

Usage:
    from core.store import JobStore, SelectorStore

    jobs = JobStore()        # uses QUANTLUNA_STORE_BACKEND
    jobs.set(job_id, job)
    job = jobs.get(job_id)   # None if missing
    jobs.delete(job_id)
    all_jobs = jobs.all()

    selectors = SelectorStore()
    selectors.set("live", selector)
    sel = selectors.get("live")

Env vars:
    QUANTLUNA_STORE_BACKEND   memory | sqlite | redis   (default: memory)
    QUANTLUNA_DB_PATH         path to SQLite DB file    (default: quantluna_jobs.db)
    QUANTLUNA_REDIS_URL       redis://host:port/db       (default: redis://localhost:6379/0)
    QUANTLUNA_REDIS_TTL       TTL in seconds for Redis keys (default: 86400 = 24h)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from env
# ---------------------------------------------------------------------------

_BACKEND     = os.getenv("QUANTLUNA_STORE_BACKEND", "memory").lower().strip()
_DB_PATH     = Path(os.getenv("QUANTLUNA_DB_PATH", "quantluna_jobs.db"))
_REDIS_URL   = os.getenv("QUANTLUNA_REDIS_URL", "redis://localhost:6379/0")
_REDIS_TTL   = int(os.getenv("QUANTLUNA_REDIS_TTL", "86400"))  # 24h

# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class AbstractStore(ABC):
    """Key-value store with get/set/delete/all/count interface."""

    @abstractmethod
    def get(self, key: str) -> Optional[Any]: ...

    @abstractmethod
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def all(self) -> Dict[str, Any]: ...

    @abstractmethod
    def count(self) -> int: ...

    def keys(self) -> List[str]:
        return list(self.all().keys())

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def __len__(self) -> int:
        return self.count()


# ---------------------------------------------------------------------------
# Memory backend
# ---------------------------------------------------------------------------

class MemoryStore(AbstractStore):
    """
    In-memory store. Zero deps. Lost on restart.
    Thread-safe via GIL for CPython (dict ops are atomic).
    """

    def __init__(self) -> None:
        self._data: Dict[str, Tuple[Any, Optional[float]]] = {}  # value, expires_at

    def _is_expired(self, key: str) -> bool:
        item = self._data.get(key)
        if item is None:
            return True
        _, exp = item
        return exp is not None and time.monotonic() > exp

    def get(self, key: str) -> Optional[Any]:
        if self._is_expired(key):
            self._data.pop(key, None)
            return None
        item = self._data.get(key)
        return item[0] if item else None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        exp = (time.monotonic() + ttl) if ttl else None
        self._data[key] = (value, exp)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def all(self) -> Dict[str, Any]:
        # Evict expired entries first
        expired = [k for k in list(self._data) if self._is_expired(k)]
        for k in expired:
            del self._data[k]
        return {k: v for k, (v, _) in self._data.items()}

    def count(self) -> int:
        return len(self.all())

    def clear(self) -> None:
        self._data.clear()


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------

class SQLiteStore(AbstractStore):
    """
    SQLite-backed store using stdlib sqlite3 + WAL journal.
    Survives process restart. Single-process safe.

    Non-serializable values (e.g. pandas DataFrames, live objects) are
    stored in a separate in-memory sidecar (_live) keyed by store name.
    JSON-serializable fields are persisted; live fields are re-attached
    on get() from _live if present.
    """

    _live: Dict[str, Dict[str, Any]] = {}  # class-level cross-instance sidecar

    def __init__(self, table: str = "kv_store", db_path: Path = _DB_PATH) -> None:
        self.table = table
        self.db_path = db_path
        self._conn = self._connect()
        self._live.setdefault(table, {})

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.table} (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                expires_at REAL
            )
        """)
        conn.commit()
        return conn

    def _is_expired(self, expires_at: Optional[float]) -> bool:
        return expires_at is not None and time.time() > expires_at

    def get(self, key: str) -> Optional[Any]:
        row = self._conn.execute(
            f"SELECT value, expires_at FROM {self.table} WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        value_str, expires_at = row
        if self._is_expired(expires_at):
            self.delete(key)
            return None
        try:
            obj = json.loads(value_str)
        except Exception:
            return None
        # Reattach live fields (e.g. trades_df, selector objects)
        live = self._live.get(self.table, {}).get(key, {})
        if isinstance(obj, dict) and live:
            obj.update(live)
        return obj

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        expires_at = (time.time() + ttl) if ttl else None
        # Separate non-serializable fields
        if isinstance(value, dict):
            live_fields = {}
            safe_value = {}
            for k, v in value.items():
                try:
                    json.dumps(v, default=str)
                    safe_value[k] = v
                except (TypeError, ValueError):
                    live_fields[k] = v
                    safe_value[k] = None  # placeholder
            if live_fields:
                self._live.setdefault(self.table, {})[key] = live_fields
        else:
            safe_value = value

        try:
            payload = json.dumps(safe_value, default=str)
        except Exception as e:
            logger.warning(f"SQLiteStore.set({key}): serialization failed: {e}")
            payload = json.dumps({"_error": str(e)})

        self._conn.execute(
            f"INSERT OR REPLACE INTO {self.table} (key, value, expires_at) VALUES (?, ?, ?)",
            (key, payload, expires_at),
        )
        self._conn.commit()

    def delete(self, key: str) -> None:
        self._conn.execute(f"DELETE FROM {self.table} WHERE key = ?", (key,))
        self._conn.commit()
        self._live.get(self.table, {}).pop(key, None)

    def all(self) -> Dict[str, Any]:
        rows = self._conn.execute(
            f"SELECT key, value, expires_at FROM {self.table}"
        ).fetchall()
        result: Dict[str, Any] = {}
        now = time.time()
        for key, value_str, expires_at in rows:
            if expires_at and now > expires_at:
                continue
            try:
                obj = json.loads(value_str)
                live = self._live.get(self.table, {}).get(key, {})
                if isinstance(obj, dict) and live:
                    obj.update(live)
                result[key] = obj
            except Exception:
                pass
        return result

    def count(self) -> int:
        row = self._conn.execute(
            f"SELECT COUNT(*) FROM {self.table} WHERE expires_at IS NULL OR expires_at > ?",
            (time.time(),),
        ).fetchone()
        return row[0] if row else 0


# ---------------------------------------------------------------------------
# Redis backend
# ---------------------------------------------------------------------------

class RedisStore(AbstractStore):
    """
    Redis-backed store. Requires:
      pip install redis
      QUANTLUNA_STORE_BACKEND=redis
      QUANTLUNA_REDIS_URL=redis://localhost:6379/0

    Non-serializable values stored in MemoryStore sidecar (same process).
    In multi-process deployments, live fields (trades_df) must be re-computed
    per process or stored via separate mechanism.

    Graceful fallback: if redis-py not installed or Redis unreachable,
    falls back to MemoryStore with a warning.
    """

    def __init__(
        self,
        prefix: str = "ql",
        url: str = _REDIS_URL,
        default_ttl: int = _REDIS_TTL,
    ) -> None:
        self.prefix = prefix
        self.default_ttl = default_ttl
        self._fallback: Optional[MemoryStore] = None
        self._live: Dict[str, Any] = {}
        try:
            import redis
            self._r = redis.from_url(url, decode_responses=True, socket_timeout=2)
            self._r.ping()
            logger.info(f"RedisStore connected: {url} prefix={prefix}")
        except Exception as e:
            logger.warning(f"RedisStore unavailable ({e}), falling back to MemoryStore")
            self._r = None
            self._fallback = MemoryStore()

    def _key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    def get(self, key: str) -> Optional[Any]:
        if self._fallback:
            return self._fallback.get(key)
        raw = self._r.get(self._key(key))
        if raw is None:
            return None
        try:
            obj = json.loads(raw)
            live = self._live.get(key, {})
            if isinstance(obj, dict) and live:
                obj.update(live)
            return obj
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        if self._fallback:
            self._fallback.set(key, value, ttl)
            return
        effective_ttl = ttl or self.default_ttl
        if isinstance(value, dict):
            live_fields = {}
            safe = {}
            for k, v in value.items():
                try:
                    json.dumps(v, default=str)
                    safe[k] = v
                except (TypeError, ValueError):
                    live_fields[k] = v
                    safe[k] = None
            if live_fields:
                self._live[key] = live_fields
            payload = json.dumps(safe, default=str)
        else:
            payload = json.dumps(value, default=str)
        self._r.setex(self._key(key), effective_ttl, payload)

    def delete(self, key: str) -> None:
        if self._fallback:
            self._fallback.delete(key)
            return
        self._r.delete(self._key(key))
        self._live.pop(key, None)

    def all(self) -> Dict[str, Any]:
        if self._fallback:
            return self._fallback.all()
        pattern = f"{self.prefix}:*"
        result: Dict[str, Any] = {}
        for full_key in self._r.scan_iter(pattern):
            short_key = full_key[len(self.prefix) + 1:]
            val = self.get(short_key)
            if val is not None:
                result[short_key] = val
        return result

    def count(self) -> int:
        if self._fallback:
            return self._fallback.count()
        return len(list(self._r.scan_iter(f"{self.prefix}:*")))

    @property
    def is_connected(self) -> bool:
        return self._r is not None and self._fallback is None


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------

def _make_store(backend: str, table_or_prefix: str) -> AbstractStore:
    if backend == "redis":
        return RedisStore(prefix=f"ql_{table_or_prefix}")
    elif backend == "sqlite":
        return SQLiteStore(table=table_or_prefix)
    else:
        if backend not in ("memory", ""):
            logger.warning(f"Unknown QUANTLUNA_STORE_BACKEND={backend!r}, using memory")
        return MemoryStore()


# ---------------------------------------------------------------------------
# Typed facades
# ---------------------------------------------------------------------------

class JobStore:
    """
    Typed facade for backtest job persistence.
    Drop-in replacement for the _JOBS dict in api/backtest.py.

    Usage:
        jobs = JobStore()                  # backend from env
        jobs.set(job_id, job_dict)
        job = jobs.get(job_id)             # None if not found
        all_jobs = jobs.all()              # Dict[job_id, job_dict]
        jobs.delete(job_id)
        jobs.evict_done(keep_last=10)      # evict oldest done/error jobs
    """

    def __init__(self, backend: Optional[str] = None) -> None:
        b = (backend or _BACKEND).lower()
        self._store = _make_store(b, "jobs")

    def get(self, job_id: str) -> Optional[Dict]:
        return self._store.get(job_id)

    def set(self, job_id: str, job: Dict) -> None:
        self._store.set(job_id, job)

    def delete(self, job_id: str) -> None:
        self._store.delete(job_id)

    def all(self) -> Dict[str, Dict]:
        return self._store.all()

    def count(self) -> int:
        return self._store.count()

    def __contains__(self, job_id: str) -> bool:
        return self._store.get(job_id) is not None

    def __len__(self) -> int:
        return self.count()

    def evict_done(
        self,
        max_total: int = 100,
        keep_last: int = 10,
    ) -> int:
        """
        Evict oldest done/error jobs when total > max_total.
        Never evicts queued/running jobs (FIX-3).
        Returns number of jobs evicted.
        """
        all_jobs = self.all()
        if len(all_jobs) < max_total:
            return 0
        evictable = [
            j for j in all_jobs.values()
            if j.get("status") in ("done", "error")
        ]
        evictable_sorted = sorted(evictable, key=lambda j: j.get("created_at", ""))
        to_evict = evictable_sorted[:max(0, len(evictable) - keep_last)]
        for j in to_evict:
            self.delete(j["job_id"])
        return len(to_evict)


class SelectorStore:
    """
    Typed facade for AutoStrategySelector persistence.
    Selector objects are not serializable — always stored in MemoryStore
    regardless of backend (live Python objects).

    In multi-process deployments:
      - scores_summary() dicts ARE serialized to Redis/SQLite for dashboard
      - live selector objects stay per-process in memory
      - Use get_summary(key) for cross-process dashboard queries

    Usage:
        sel_store = SelectorStore()
        sel_store.set("live", selector_obj)
        sel = sel_store.get("live")         # live object or None
        summary = sel_store.get_summary("live")  # JSON-safe dict, Redis-backed
        sel_store.delete("live")
    """

    def __init__(self, backend: Optional[str] = None) -> None:
        b = (backend or _BACKEND).lower()
        # Live objects always in memory
        self._live: MemoryStore = MemoryStore()
        # Summaries (JSON-safe) use configured backend
        self._summaries: AbstractStore = _make_store(b, "selector_summaries")

    def get(self, key: str) -> Optional[Any]:
        """Return live selector object (in-process only)."""
        return self._live.get(key)

    def set(self, key: str, selector: Any) -> None:
        """Store live selector + persist scores_summary to configured backend."""
        self._live.set(key, selector)
        try:
            summary = selector.scores_summary()
            self._summaries.set(key, summary)
        except Exception as e:
            logger.debug(f"SelectorStore: could not persist summary for {key}: {e}")

    def delete(self, key: str) -> None:
        self._live.delete(key)
        self._summaries.delete(key)

    def get_summary(self, key: str) -> Optional[Dict]:
        """Return last persisted scores_summary dict (cross-process safe)."""
        return self._summaries.get(key)

    def all(self) -> Dict[str, Any]:
        """Return all live selector objects."""
        return self._live.all()

    def all_summaries(self) -> Dict[str, Dict]:
        """Return all persisted summaries (cross-process safe)."""
        return self._summaries.all()

    def get_or_create(self, key: str, factory) -> Any:
        """
        Return existing selector or create a new one via factory().
        factory: callable() -> AutoStrategySelector
        """
        sel = self.get(key)
        if sel is None:
            sel = factory()
            self.set(key, sel)
        return sel

    def clear(self) -> None:
        self._live.clear()

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None
