# syntax=docker/dockerfile:1
# QuantLuna — Multi-stage Docker build
#
# Stages:
#   builder — compile wheels
#   production — minimal runtime image (~200MB)
#
# Usage:
#   docker build --target production -t quantluna:latest .
#   docker build --target production -t quantluna:$(git rev-parse --short HEAD) .

# ---- Stage 1: builder -------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps for compiling numpy / pandas extensions
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

# Non-root user for security
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

# Health check — dashboard alive?
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_LEVEL=INFO

ENTRYPOINT ["python", "main.py"]
CMD ["paper", "--pair", "BTCUSDT", "ETHUSDT"]
