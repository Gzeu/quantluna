# QuantLuna — Production Dockerfile
# Sprint 12
#
# Multi-stage build:
#   Stage 1 (builder): installs all deps
#   Stage 2 (runtime): minimal image, no build tools
#
# Build:
#   docker build -t quantluna:latest .
#
# Run paper trader:
#   docker run --env-file .env quantluna:latest python scripts/run_paper.py --pair BTCUSDT ETHUSDT
#
# Run live trader:
#   docker run --env-file .env -v $(pwd)/data:/app/data quantluna:latest python scripts/run_live.py --pair BTCUSDT ETHUSDT
#
# Run with docker-compose:
#   docker-compose up -d

FROM python:3.11-slim AS builder

WORKDIR /build

# Install build deps
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

LABEL maintainer="George Pricop"
LABEL description="QuantLuna — Adaptive Kalman Filter Pairs Trading Engine"
LABEL version="1.0.0"

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source
COPY . .

# Create data and cache directories
RUN mkdir -p /app/data /root/.quantluna/cache

# Non-root user for security
RUN useradd -m -u 1000 quantluna && \
    chown -R quantluna:quantluna /app /root/.quantluna
USER quantluna

# Default: show help
CMD ["python", "-c", "print('QuantLuna ready. Use: python scripts/run_live.py or python scripts/run_paper.py')"]
