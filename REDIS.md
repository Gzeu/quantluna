# QuantLuna — Redis Persistence Guide

## Overview

QuantLuna supports three persistence backends for job store and selector store:

| Backend | Persistence | Multi-process | Setup |
|---------|-------------|---------------|-------|
| `memory` | ❌ Lost on restart | ❌ Single process | Zero (default) |
| `sqlite` | ✅ Survives restart | ❌ Single process | Zero (stdlib) |
| `redis` | ✅ Survives restart | ✅ Multi-process | Redis server |

## Quick Start

### 1. Start Redis

```bash
# Docker (recommended)
docker run -d --name quantluna-redis -p 6379:6379 redis:7-alpine

# Or via docker compose (see below)
```

### 2. Install redis-py

```bash
pip install redis>=5.0
```

### 3. Set env vars

```bash
export QUANTLUNA_STORE_BACKEND=redis
export QUANTLUNA_REDIS_URL=redis://localhost:6379/0
export QUANTLUNA_REDIS_TTL=86400   # 24h TTL for job keys
```

### 4. Start the API

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Docker Compose (full stack)

```yaml
# docker-compose.yml
version: "3.9"

services:
  api:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    environment:
      - QUANTLUNA_STORE_BACKEND=redis
      - QUANTLUNA_REDIS_URL=redis://redis:6379/0
      - QUANTLUNA_REDIS_TTL=86400
      - QUANTLUNA_DB_PATH=/app/data/quantluna_jobs.db
    depends_on:
      redis:
        condition: service_healthy
    command: uvicorn api.main:app --host 0.0.0.0 --port 8000

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  redis_data:
```

---

## Env Vars Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `QUANTLUNA_STORE_BACKEND` | `memory` | `memory` \| `sqlite` \| `redis` |
| `QUANTLUNA_DB_PATH` | `quantluna_jobs.db` | SQLite DB file path |
| `QUANTLUNA_REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `QUANTLUNA_REDIS_TTL` | `86400` | Key TTL in seconds (Redis only) |

---

## Graceful Fallback

If `redis` backend is configured but Redis is unreachable, `RedisStore` automatically
falls back to `MemoryStore` with a warning log:

```
WARNING  core.store: RedisStore unavailable (Connection refused), falling back to MemoryStore
```

No code changes needed — the API continues to work with in-memory storage.
When Redis becomes available, restart the API to reconnect.

---

## Data Stored in Redis

### Job keys
```
ql_jobs:<job_id>   → JSON job dict (metrics, request, status, timestamps)
                      trades_df is NOT stored in Redis (too large).
                      Re-run backtest to re-populate trades_df after restart.
```

### Selector summary keys
```
ql_selector_summaries:<selector_id>  → scores_summary() dict
                                        (active_strategy, scores, switch_history, ...)
                                        Updated on every strategy switch.
```

### Live selector objects
AutoStrategySelector Python objects are **always in-process memory** (not serializable).
For multi-worker deployments (Gunicorn workers), each worker has its own selector state.
Use `GET /strategy/scores` to query the persisted summary from Redis instead.

---

## Migration from SQLite to Redis

```bash
# 1. Export existing jobs (optional)
sqlite3 quantluna_jobs.db ".dump jobs" > jobs_backup.sql

# 2. Switch backend
export QUANTLUNA_STORE_BACKEND=redis

# 3. Restart — existing jobs from SQLite won't auto-migrate
#    Re-run active backtests if needed
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

---

## Testing with Redis

```bash
# Unit tests use MemoryStore backend (no Redis needed)
pytest tests/test_store.py -v

# Integration test with real Redis
QUANTLUNA_STORE_BACKEND=redis pytest tests/test_store.py -v -k redis
```
