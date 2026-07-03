# QuantLuna — Developer Makefile
# Usage: make <target>
# Requires: Python 3.10+, pip, docker, docker-compose

.PHONY: help install test lint format typecheck clean docker-build \
        paper live backtest scan coverage pre-commit

PY     ?= python
PIP    ?= pip
RUFF   ?= ruff
MYPY   ?= mypy
DOCKER ?= docker

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | \
	 awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# -----------------------------------------------------------------------
# Dev setup
# -----------------------------------------------------------------------

install:  ## Install all dependencies
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

install-dev: install  ## Install + dev tools (pre-commit, mypy, ruff)
	$(PIP) install pre-commit mypy ruff
	pre-commit install

# -----------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------

test:  ## Run unit tests (excludes live WS tests)
	$(PY) -m pytest tests/ -v --tb=short \
	  --ignore=tests/test_live_ws.py \
	  --ignore=tests/test_live_trader.py \
	  -p no:warnings

coverage:  ## Run tests + coverage report
	$(PY) -m coverage run -m pytest tests/ \
	  --ignore=tests/test_live_ws.py \
	  --ignore=tests/test_live_trader.py -q
	$(PY) -m coverage report --fail-under=60
	$(PY) -m coverage html -d htmlcov
	@echo "Coverage HTML: htmlcov/index.html"

test-all:  ## Run ALL tests including live (requires live env)
	$(PY) -m pytest tests/ -v --tb=short

# -----------------------------------------------------------------------
# Code quality
# -----------------------------------------------------------------------

lint:  ## Run ruff linter
	$(RUFF) check .

format:  ## Auto-format with ruff
	$(RUFF) format .

format-check:  ## Check formatting without modifying files
	$(RUFF) format --check .

typecheck:  ## Run mypy type checks on core packages
	$(MYPY) core/ risk/ execution/ strategy/ \
	  --ignore-missing-imports --no-strict-optional --warn-unused-ignores --pretty \
	  || true

pre-commit:  ## Run pre-commit on all files
	pre-commit run --all-files

# -----------------------------------------------------------------------
# Trading commands (local)
# -----------------------------------------------------------------------

PAIR ?= BTCUSDT ETHUSDT
EXCHANGE ?= bybit

paper:  ## Run paper trader locally
	$(PY) main.py paper --pair $(PAIR) --exchange $(EXCHANGE)

live:  ## Run live trader locally (requires confirmation)
	$(PY) main.py live --pair $(PAIR) --exchange $(EXCHANGE)

backtest:  ## Run backtest (30 days, 1h)
	$(PY) main.py backtest --pair $(PAIR) --exchange $(EXCHANGE) --days 30 --timeframe 1h

scan:  ## Scan top pairs
	$(PY) main.py scan --exchange $(EXCHANGE) --top 20

# -----------------------------------------------------------------------
# Docker
# -----------------------------------------------------------------------

docker-build:  ## Build Docker image
	$(DOCKER) build -t quantluna:latest .

docker-paper:  ## Run paper trader in Docker
	docker-compose up paper

docker-live:  ## Run live trader in Docker (with profile)
	docker-compose --profile live up live

docker-dashboard:  ## Run dashboard in Docker
	docker-compose up dashboard

docker-clean:  ## Remove all QuantLuna Docker containers
	$(DOCKER) rm -f quantluna_paper quantluna_live quantluna_dashboard 2>/dev/null || true

# -----------------------------------------------------------------------
# Cleanup
# -----------------------------------------------------------------------

clean:  ## Remove cache, build artifacts, coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete
	rm -rf .pytest_cache .mypy_cache htmlcov .coverage coverage.xml dist build
	@echo "Cleaned."
