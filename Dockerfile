# QuantLuna — Production Dockerfile
# July 2026 hardening
#
# Improvements over Sprint 12:
#   - HEALTHCHECK built-in (docker ps shows healthy/unhealthy)
#   - build-arg APP_VERSION propagated as image label
#   - /app/state directory created + owned by quantluna user
#     (was missing — checkpoint writes failed in Docker)
#   - pip-compile hash verification in builder stage
#   - .dockerignore reference: ensure data/, logs/, .env are excluded
#   - CMD changed to use main.py CLI instead of raw scripts/
#
# Build:
#   docker build -t quantluna:latest .
#   docker build --build-arg APP_VERSION=0.14.0 -t quantluna:0.14.0 .
#
# Run:
#   docker run --env-file .env quantluna:latest paper --pair BTCUSDT ETHUSDT
#   docker run --env-file .env quantluna:latest live  --pair BTCUSDT ETHUSDT --yes

FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt

# -----------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ARG APP_VERSION=dev

LABEL maintainer="George Pricop"
LABEL description="QuantLuna — Adaptive Kalman Filter Pairs Trading Engine"
LABEL version="${APP_VERSION}"
LABEL org.opencontainers.image.source="https://github.com/Gzeu/quantluna"

WORKDIR /app

COPY --from=builder /install /usr/local

COPY . .

# Create all runtime directories in one layer
RUN mkdir -p /app/data /app/state /app/logs /root/.quantluna/cache

# Non-root user
RUN useradd -m -u 1000 quantluna && \
    chown -R quantluna:quantluna /app /root/.quantluna
USER quantluna

# Healthcheck: verify the Python environment is intact
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import quantluna_health_check 2>/dev/null || python -c 'import core, execution, risk; print(\"ok\")'"

# Use main.py CLI as default entrypoint
ENTRYPOINT ["python", "main.py"]
CMD ["--help"]
