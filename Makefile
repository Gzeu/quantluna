# QuantLuna Makefile
# Run 'make help' pentru lista completa

.DEFAULT_GOAL := help
PYTHON       ?= python
PIP          ?= pip
UVICORN      ?= uvicorn

BOLD  = \033[1m
GREEN = \033[32m
YEL   = \033[33m
RESET = \033[0m

.PHONY: help install install-dev lint format typecheck test coverage pre-commit \
        paper live backtest optimize scan health dashboard daily-summary \
        docker-build docker-paper docker-live docker-dashboard \
        clean clean-all

help:
	@echo ""
	@echo "$(BOLD)QuantLuna — comenzi disponibile$(RESET)"
	@echo ""
	@echo "$(GREEN)Setup:$(RESET)"
	@echo "  make install         Install dependente productie"
	@echo "  make install-dev     Install dev deps + pre-commit hooks"
	@echo ""
	@echo "$(GREEN)Calitate cod:$(RESET)"
	@echo "  make lint            Ruff check"
	@echo "  make format          Ruff format (fix automat)"
	@echo "  make typecheck       Mypy type check"
	@echo "  make pre-commit      Ruleaza toate hook-urile"
	@echo ""
	@echo "$(GREEN)Teste:$(RESET)"
	@echo "  make test            Pytest all (exclude live/ws)"
	@echo "  make coverage        Pytest + coverage HTML report"
	@echo ""
	@echo "$(GREEN)Trading:$(RESET)"
	@echo "  make paper           Paper trading BTCUSDT/ETHUSDT bybit"
	@echo "  make live            Live trading (confirmare necesara)"
	@echo "  make backtest        Backtest 365 zile BTCUSDT/ETHUSDT"
	@echo "  make optimize        Optuna optimize 150 trials"
	@echo "  make scan            Scan perechi cointegrate"
	@echo "  make health          Pre-flight health check"
	@echo "  make daily-summary   Trimite raport zilnic via NotifierBus"
	@echo ""
	@echo "$(GREEN)Dashboard:$(RESET)"
	@echo "  make dashboard       Start FastAPI dashboard (port 8000)"
	@echo ""
	@echo "$(GREEN)Docker:$(RESET)"
	@echo "  make docker-build    Build imagine Docker"
	@echo "  make docker-paper    Paper trader in container"
	@echo "  make docker-live     Live trader in container"
	@echo "  make docker-dashboard Dashboard in container"
	@echo ""
	@echo "$(GREEN)Curatenie:$(RESET)"
	@echo "  make clean           Sterge cache-uri Python"
	@echo "  make clean-all       Sterge tot (include htmlcov, dist)"
	@echo ""

install:
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

install-dev: install
	$(PIP) install -r requirements-dev.txt
	pre-commit install
	@echo "$(GREEN)✓ Dev environment gata. pre-commit hooks instalate.$(RESET)"

lint:
	ruff check .

format:
	ruff format .
	ruff check --fix .

typecheck:
	mypy core/ risk/ execution/ strategy/ notifications/ --ignore-missing-imports --no-strict-optional

pre-commit:
	pre-commit run --all-files

test:
	pytest tests/ -v --tb=short \
		--ignore=tests/test_live_ws.py \
		--ignore=tests/test_live_trader.py \
		-p no:warnings

coverage:
	pytest tests/ --cov=. --cov-report=html --cov-report=term-missing \
		--ignore=tests/test_live_ws.py \
		--ignore=tests/test_live_trader.py \
		-p no:warnings
	@echo "$(GREEN)✓ Coverage report: htmlcov/index.html$(RESET)"

paper:
	$(PYTHON) main.py paper --pair BTCUSDT ETHUSDT --exchange bybit --capital 10000

live:
	@echo "$(YEL)ATENTIE: live trading cu bani reali!$(RESET)"
	$(PYTHON) main.py live --pair BTCUSDT ETHUSDT --exchange bybit

backtest:
	$(PYTHON) main.py backtest --pair BTCUSDT ETHUSDT --days 365 --timeframe 1h

optimize:
	$(PYTHON) main.py optimize --pair BTCUSDT ETHUSDT --trials 150 --objective sharpe

scan:
	$(PYTHON) scripts/scan_pairs.py --exchange bybit --top 20

health:
	$(PYTHON) main.py health --pair BTCUSDT ETHUSDT --exchange bybit

daily-summary:
	$(PYTHON) scripts/daily_summary.py --pair BTCUSDT ETHUSDT

dashboard:
	$(UVICORN) dashboard.server:app --reload --host 0.0.0.0 --port 8000

docker-build:
	docker build --target production -t quantluna:latest .

docker-paper:
	docker compose --profile paper up --build

docker-live:
	docker compose --profile live up --build

docker-dashboard:
	docker compose --profile dashboard up --build

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true
	find . -name '*.pyo' -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	@echo "$(GREEN)✓ Cache-uri sterse.$(RESET)"

clean-all: clean
	rm -rf htmlcov .coverage coverage.xml dist build *.egg-info
	@echo "$(GREEN)✓ Curatenie completa.$(RESET)"
