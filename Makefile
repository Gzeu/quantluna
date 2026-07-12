# QuantLuna Makefile
# Run 'make help' pentru lista completa

.DEFAULT_GOAL := help
PYTHON       ?= python
PIP          ?= pip
UVICORN      ?= uvicorn

BOLD  = \033[1m
GREEN = \033[32m
YEL   = \033[33m
CYAN  = \033[36m
RED   = \033[31m
RESET = \033[0m

.PHONY: help install install-dev lint format typecheck test coverage pre-commit \
        paper live backtest optimize scan health dashboard daily-summary \
        docker-build docker-paper docker-live docker-dashboard \
        prod-build prod-up prod-down prod-logs prod-restart prod-status \
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
	@echo "$(GREEN)Trading local:$(RESET)"
	@echo "  make paper           Paper trading BTCUSDT/ETHUSDT bybit"
	@echo "  make live            Live trading (confirmare necesara)"
	@echo "  make backtest        Backtest 365 zile BTCUSDT/ETHUSDT"
	@echo "  make optimize        Optuna optimize 150 trials"
	@echo "  make scan            Scan perechi cointegrate"
	@echo "  make health          Pre-flight health check"
	@echo "  make daily-summary   Trimite raport zilnic via NotifierBus"
	@echo ""
	@echo "$(GREEN)Dashboard local:$(RESET)"
	@echo "  make dashboard       Start FastAPI dashboard (port 8000)"
	@echo ""
	@echo "$(GREEN)Docker dev:$(RESET)"
	@echo "  make docker-build    Build imagine Docker"
	@echo "  make docker-paper    Paper trader in container"
	@echo "  make docker-live     Live trader in container"
	@echo "  make docker-dashboard Dashboard in container"
	@echo ""
	@echo "$(CYAN)Productie (docker-compose.prod.yml):$(RESET)"
	@echo "  make prod-build      Build toate imaginile productie"
	@echo "  make prod-up         Porneste stiva completa (API+trader+dashboard+nginx)"
	@echo "  make prod-down       Opreste stiva productie"
	@echo "  make prod-restart    Restart rapid (fara rebuild)"
	@echo "  make prod-logs       Logs live din toate containerele"
	@echo "  make prod-status     Status containere productie"
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
	$(UVICORN) api.main:app --reload --host 0.0.0.0 --port 8000

# Docker dev
docker-build:
	docker build --target production -t quantluna:latest .

docker-paper:
	docker compose --profile paper up --build

docker-live:
	docker compose --profile live up --build

docker-dashboard:
	docker compose --profile dashboard up --build

# Productie
prod-build:
	@echo "$(CYAN)Building productie...$(RESET)"
	docker compose -f docker-compose.prod.yml build --no-cache
	@echo "$(GREEN)✓ Build complet.$(RESET)"

prod-up:
	@echo "$(CYAN)Pornire stiva productie...$(RESET)"
	@test -f .env || (echo "$(RED)Eroare: .env nu exista! cp .env.production.example .env$(RESET)" && exit 1)
	docker compose -f docker-compose.prod.yml up -d
	@echo "$(GREEN)✓ Productie activa. Dashboard: http://localhost$(RESET)"

prod-down:
	@echo "$(YEL)Oprire stiva productie...$(RESET)"
	docker compose -f docker-compose.prod.yml down

prod-restart:
	docker compose -f docker-compose.prod.yml restart
	@echo "$(GREEN)✓ Restart complet.$(RESET)"

prod-logs:
	docker compose -f docker-compose.prod.yml logs -f --tail=100

prod-status:
	docker compose -f docker-compose.prod.yml ps

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true
	find . -name '*.pyo' -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	@echo "$(GREEN)✓ Cache-uri sterse.$(RESET)"

clean-all: clean
	rm -rf htmlcov .coverage coverage.xml dist build *.egg-info
	@echo "$(GREEN)✓ Curatenie completa.$(RESET)"
