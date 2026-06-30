# Contributing to QuantLuna

Thank you for considering a contribution to QuantLuna. This document describes the workflow, code standards, and review process.

---

## Table of Contents

- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Development Workflow](#development-workflow)
- [Code Standards](#code-standards)
- [Testing Requirements](#testing-requirements)
- [Sprint Conventions](#sprint-conventions)
- [Pull Request Process](#pull-request-process)
- [Reporting Issues](#reporting-issues)

---

## Getting Started

```bash
git clone https://github.com/Gzeu/quantluna.git
cd quantluna
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov
```

Create your `.env` from the template:

```bash
cp .env.example .env
```

Run the full test suite to confirm baseline:

```bash
pytest tests/ -x --tb=short -q
```

All tests must pass before starting any changes.

---

## Project Structure

| Directory | Responsibility |
|-----------|----------------|
| `core/` | Kalman Filter, spread engine, cointegration primitives |
| `strategy/` | Signal generation, regime detection, pair selection, cointegration pipeline |
| `risk/` | Kelly sizing, drawdown control, portfolio allocation, correlation matrix |
| `execution/` | Live trader, paper trader, order manager, WebSocket watchdog, funding monitor |
| `backtest/` | Vectorised engine, walk-forward, Monte Carlo, analytics |
| `data/` | OHLCV loaders, funding rate fetcher |
| `notifications/` | Telegram notifier, alert system |
| `dashboard/` | FastAPI WebSocket dashboard server |
| `config/` | LiveConfig, ExecConfig dataclasses |
| `scripts/` | CLI runners: `run_backtest.py`, `run_live.py` |
| `tests/` | Full test suite — one file per module |

---

## Development Workflow

1. **Fork** the repository and create a branch from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   # or
   git checkout -b fix/your-fix-name
   ```

2. **Make your changes** following the code standards below.

3. **Write or update tests** — new functionality requires tests. See [Testing Requirements](#testing-requirements).

4. **Run the full suite** before pushing:
   ```bash
   pytest tests/ -x --tb=short -q
   ```

5. **Push and open a Pull Request** against `main`.

---

## Code Standards

### Python Style

- **Python 3.10+** required.
- Follow [PEP 8](https://peps.python.org/pep-0008/) with a max line length of **100 characters**.
- Use `from __future__ import annotations` at the top of every file.
- Use **dataclasses** for configuration objects (`@dataclass`).
- Use **type hints** on all public function signatures.
- `async`/`await` throughout — no blocking calls in the trading loop.

### Naming Conventions

| Context | Convention |
|---------|------------|
| Classes | `PascalCase` |
| Functions / methods | `snake_case` |
| Constants | `UPPER_SNAKE_CASE` |
| Private attributes | `_prefixed_snake_case` |
| Module-level config | `dataclass` with typed fields |

### Logging

- Use `logging.getLogger(__name__)` in every module.
- Log levels: `DEBUG` for per-tick data, `INFO` for state changes and trade events, `WARNING` for non-critical degradation, `ERROR` for failures, `CRITICAL` for HALT conditions.
- Never `print()` in production code paths.

### Error Handling

- Trading loop functions must **never raise unhandled exceptions** to the caller.
- Use `try/except` with specific exception types.
- Always log the exception with context before suppressing.
- Alert-worthy errors must call `_send_alert()` or `TelegramNotifier` — do not silently fail.

### Imports

- Standard library first, then third-party, then local imports.
- No circular imports — if you need cross-module references, use dependency injection or the `StateBus`.

---

## Testing Requirements

### Rules

- Every new module must have a corresponding `tests/test_<module>.py`.
- Tests must be deterministic — no time-dependent or network-dependent behaviour without mocking.
- **No data leakage** in any backtest or walk-forward test — train/test splits must not overlap.
- Use `pytest-asyncio` for async tests.
- Mock external dependencies (exchange APIs, Telegram, WebSocket) — never hit real APIs in tests.

### Running Tests

```bash
# Full suite
pytest tests/ -x --tb=short -q

# With coverage
pytest tests/ --cov=. --cov-report=term-missing -q

# Specific module
pytest tests/test_kalman.py -v
```

### Test File Naming

| Module | Test file |
|--------|----------|
| `core/kalman_filter.py` | `tests/test_kalman.py` |
| `execution/paper_trader.py` | `tests/test_paper_trader.py` |
| `notifications/telegram_notifier.py` | `tests/test_notifier.py` |

---

## Sprint Conventions

QuantLuna follows a sprint-based development model. Each sprint adds a cohesive set of features.

### Commit Message Format

```
<type>(<scope>): <short description>

[optional body]
[optional footer]
```

**Types:**

| Type | When to use |
|------|-------------|
| `feat` | New feature or module |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `test` | Adding or updating tests |
| `refactor` | Code restructuring without behaviour change |
| `perf` | Performance improvement |
| `chore` | Dependency updates, tooling |

**Examples:**

```
feat(sprint11): add TelegramNotifier with retry + rate limiting
fix(live_trader): FIX-TZ — initialize _last_log_ts as tz-aware UTC
docs: update README with Sprint 11 paper trader and notifier
test(paper_trader): add fill simulation and slippage model tests
```

### CHANGELOG

Update `CHANGELOG.md` for every sprint with:
- Sprint number and date
- `### Added` — new modules and features
- `### Fixed` — bug fixes with FIX-ID references
- `### Changed` — behaviour changes

---

## Pull Request Process

1. **Title**: follow commit message format — `feat(scope): description`.
2. **Description**: summarize what changed, why, and how to test it.
3. **Checklist** before requesting review:
   - [ ] All tests pass (`pytest tests/ -x -q`)
   - [ ] New code has tests
   - [ ] `CHANGELOG.md` updated
   - [ ] Type hints on public APIs
   - [ ] No `print()` statements
   - [ ] No secrets or API keys in code
4. **Review**: at least one review required before merge.
5. **Merge**: squash merge preferred for feature PRs; regular merge for hotfixes.

---

## Reporting Issues

When filing a bug report, include:

- Python version (`python --version`)
- Exchange and trading mode (live / paper)
- Relevant log output (sanitize API keys)
- Steps to reproduce
- Expected vs actual behaviour

For security vulnerabilities, **do not open a public issue** — contact the maintainer directly.

---

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
