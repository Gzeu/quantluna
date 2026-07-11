# syntax=docker/dockerfile:1
# QuantLuna — Multi-stage Docker build
#
# Stages:
#   builder    — compile wheels
#   production — minimal runtime image (~200MB)
#
# Usage:
#   docker build --target production -t quantluna:latest .
#   docker build --target production -t quantluna:$(git rev-parse --short HEAD) .

# ---- Stage 1: builder -------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ libffi-dev libssl-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip wheel --wheel-dir /wheels -r requirements.txt

# ---- Stage 2: production ----------------------------------------------------
FROM python:3.11-slim AS production

LABEL org.opencontainers.image.title="QuantLuna" \
      org.opencontainers.image.description="Adaptive Kalman Filter Pairs Trading Engine" \
      org.opencontainers.image.source="https://github.com/Gzeu/quantluna" \
      org.opencontainers.image.licenses="MIT"

# Non-root user pentru securitate
RUN addgroup --system quantluna && adduser --system --ingroup quantluna quantluna

WORKDIR /app

# Install pre-built wheels — no compiler needed
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels /root/.cache

# Copy application code
COPY --chown=quantluna:quantluna . .

# Runtime directories (mounted as volumes in production)
RUN mkdir -p /app/data /app/state /app/logs \
    && chown -R quantluna:quantluna /app/data /app/state /app/logs

USER quantluna

# Health check — runner HTTP health endpoint (port 8081)
# Probe-ul loveste /api/health al runner-ului, nu dashboard-ul (8000)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "
import urllib.request, sys
try:
    r = urllib.request.urlopen('http://localhost:8081/api/health', timeout=8)
    sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_LEVEL=INFO

ENTRYPOINT ["python", "main.py"]
CMD ["live", "--pair", "BTCUSDT", "ETHUSDT"]
