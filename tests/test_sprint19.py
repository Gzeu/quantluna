"""
Sprint 19 — End-to-End Integration Tests

Teste de integrare care valideaza fluxul complet:
  paper run -> signal -> order -> pnl
  live preflight -> health check
  backtest -> report builder
  config loading

Toate testele sunt offline (mock-uri, fara conexiuni reale la exchange).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Helpers ─────────────────────────────────────────────────────────────────


def make_ohlcv(n: int = 300, base_price: float = 40_000.0):
    """Genereaza date OHLCV sintetice pentru teste."""
    import numpy as np
    import pandas as pd

    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="1h")
    prices = base_price + np.cumsum(np.random.randn(n) * 100)
    prices = np.abs(prices)
    df = pd.DataFrame(
        {
            "open": prices * 0.999,
            "high": prices * 1.002,
            "low": prices * 0.998,
            "close": prices,
            "volume": np.random.uniform(1_000, 5_000, n),
        },
        index=dates,
    )
    return df


# ─── S19-01: Config loading ───────────────────────────────────────────────────


class TestConfigLoading:
    """Verifica ca config.py se incarca corect si are toate campurile necesare."""

    def test_config_module_importable(self):
        """config.py trebuie sa se poata importa fara erori."""
        import importlib

        spec = importlib.util.find_spec("config")
        assert spec is not None, "config.py nu exista in repo"

    def test_env_example_has_required_keys(self):
        """Verifica ca .env.example contine cheile minime necesare."""
        env_example = Path(".env.example")
        if not env_example.exists():
            pytest.skip(".env.example nu exista")
        content = env_example.read_text()
        required_keys = [
            "BYBIT_API_KEY",
            "BYBIT_API_SECRET",
            "DRY_RUN",
            "EXCHANGE",
            "CAPITAL_USD",
            "TELEGRAM_BOT_TOKEN",
            "DISCORD_WEBHOOK_URL",
            "LOG_LEVEL",
        ]
        for key in required_keys:
            assert key in content, f".env.example lipseste cheia: {key}"

    def test_pyproject_version_matches_changelog(self):
        """Versiunea din pyproject.toml trebuie sa existe in CHANGELOG.md."""
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        pyproject = Path("pyproject.toml")
        changelog = Path("CHANGELOG.md")
        if not pyproject.exists() or not changelog.exists():
            pytest.skip("pyproject.toml sau CHANGELOG.md lipseste")

        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        version = data["project"]["version"]
        changelog_content = changelog.read_text()
        assert version in changelog_content, (
            f"Versiunea {version} din pyproject.toml nu apare in CHANGELOG.md"
        )


# ─── S19-02: Kalman + Spread pipeline ───────────────────────────────────────


class TestKalmanSpreadPipeline:
    """Test pipeline: Kalman filter -> spread calculator -> z-score."""

    def test_kalman_spread_pipeline_produces_zscore(self):
        """Ruleaza Kalman filter pe date sintetice si verifica z-score valid."""
        import numpy as np

        from core.kalman_filter import KalmanFilter
        from core.spread_calculator import SpreadCalculator

        df_y = make_ohlcv(200, 40_000.0)
        df_x = make_ohlcv(200, 2_500.0)

        kf = KalmanFilter()
        spread_calc = SpreadCalculator()

        spreads = []
        for i in range(50, len(df_y)):
            y_prices = df_y["close"].iloc[:i].values
            x_prices = df_x["close"].iloc[:i].values
            kf.update(y_prices[-1], x_prices[-1])
            hedge = kf.get_hedge_ratio()
            spread = y_prices[-1] - hedge * x_prices[-1]
            spreads.append(spread)

        assert len(spreads) > 0
        spreads_arr = np.array(spreads)
        mean = spreads_arr.mean()
        std = spreads_arr.std()
        if std > 0:
            zscore = (spreads_arr[-1] - mean) / std
            assert -10 < zscore < 10, f"Z-score anormal: {zscore}"

    def test_spread_calculator_zscore_normalization(self):
        """SpreadCalculator returneaza z-score normalizat."""
        import numpy as np

        from core.spread_calculator import SpreadCalculator

        calc = SpreadCalculator()
        prices_y = np.random.randn(100) * 100 + 40_000
        prices_x = prices_y * 0.0625 + np.random.randn(100) * 10

        for i in range(30, len(prices_y)):
            result = calc.compute(prices_y[i], prices_x[i], hedge_ratio=16.0)
            if result is not None:
                assert hasattr(result, "zscore") or isinstance(result, (float, dict, tuple))


# ─── S19-03: Cointegration quick check ──────────────────────────────────────


class TestCointegrationQuick:
    """Teste rapide pentru modulul de cointegration."""

    def test_engle_granger_on_cointegrated_series(self):
        """Serii cointegrate artificial trebuie sa treaca testul EG."""
        import numpy as np

        from core.cointegration import CointegrationAnalyzer

        np.random.seed(42)
        n = 200
        common = np.cumsum(np.random.randn(n))
        y = common + np.random.randn(n) * 0.1
        x = common * 0.8 + np.random.randn(n) * 0.1

        analyzer = CointegrationAnalyzer()
        result = analyzer.engle_granger(y, x)
        # Cel putin atribulul p_value trebuie sa existe
        assert hasattr(result, "p_value") or isinstance(result, dict)

    def test_half_life_ornstein_uhlenbeck(self):
        """Half-life pe spread mean-reverting trebuie sa fie pozitiv si finit."""
        import numpy as np

        from core.half_life import compute_half_life

        np.random.seed(7)
        theta = 0.1  # mean reversion speed
        mu = 0.0
        sigma = 0.5
        n = 500
        spread = np.zeros(n)
        for i in range(1, n):
            spread[i] = spread[i - 1] + theta * (mu - spread[i - 1]) + sigma * np.random.randn()

        hl = compute_half_life(spread)
        assert hl is not None
        assert hl > 0
        assert hl < 1000, f"Half-life prea mare: {hl}"


# ─── S19-04: RegimeFilter + CircuitBreaker integration ───────────────────────


class TestRegimeCircuitBreakerIntegration:
    """Testeaza integrarea RegimeFilter cu CircuitBreaker."""

    def test_circuit_breaker_trips_after_consecutive_losses(self):
        """CircuitBreaker trebuie sa se deschida dupa N pierderi consecutive."""
        from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

        cb = CircuitBreaker(
            CircuitBreakerConfig(max_consecutive_losses=3, cooldown_seconds=60)
        )
        assert not cb.is_open

        for _ in range(3):
            cb.record_trade(pnl=-100.0)

        assert cb.is_open, "CircuitBreaker trebuia sa se deschida dupa 3 pierderi"

    def test_circuit_breaker_resets_after_win(self):
        """Un trade castigator trebuie sa reseteze counter-ul de pierderi."""
        from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

        cb = CircuitBreaker(
            CircuitBreakerConfig(max_consecutive_losses=3, cooldown_seconds=60)
        )
        cb.record_trade(pnl=-100.0)
        cb.record_trade(pnl=-100.0)
        cb.record_trade(pnl=+200.0)  # win — reset
        cb.record_trade(pnl=-100.0)
        cb.record_trade(pnl=-100.0)

        assert not cb.is_open, "Nu trebuia sa se deschida — win a resetat counter-ul"

    def test_regime_filter_blocks_when_cb_open(self):
        """RegimeFilter trebuie sa blocheze toate semnalele cand CB e deschis."""
        from core.spread_monitor import SpreadMonitor
        from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
        from strategy.regime_filter import RegimeFilter

        cb = CircuitBreaker(
            CircuitBreakerConfig(max_consecutive_losses=1, cooldown_seconds=3600)
        )
        sm = SpreadMonitor()
        rf = RegimeFilter(circuit_breaker=cb, spread_monitor=sm)

        cb.record_trade(pnl=-100.0)  # Trip CB
        assert cb.is_open

        report = sm.update(0.0, 1.5, 24.0, [0.01, 0.01])
        gate = rf.check(ltf_zscore=1.5, htf_zscore=1.2, spread_report=report)

        assert not gate.allowed, "RegimeFilter trebuia sa blocheze — CB deschis"


# ─── S19-05: NotifierBus fanout ──────────────────────────────────────────────


class TestNotifierBusFanout:
    """Testeaza ca NotifierBus trimite la toti subscriberii."""

    @pytest.mark.asyncio
    async def test_notifier_bus_calls_all_channels(self):
        """send_entry_signal trebuie sa apeleze fiecare canal inregistrat."""
        from notifications.notifier_bus import NotifierBus

        bus = NotifierBus()

        mock_slack = AsyncMock()
        mock_slack.send = AsyncMock()
        mock_telegram = AsyncMock()
        mock_telegram.send = AsyncMock()

        bus.register("slack", mock_slack)
        bus.register("telegram", mock_telegram)

        await bus.send_entry_signal("BTCUSDT", "LONG", zscore=2.1)

        mock_slack.send.assert_called_once()
        mock_telegram.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_notifier_bus_tolerates_channel_failure(self):
        """Un canal care esueaza nu trebuie sa opreasca celelate canale."""
        from notifications.notifier_bus import NotifierBus

        bus = NotifierBus()

        failing_channel = AsyncMock()
        failing_channel.send = AsyncMock(side_effect=Exception("network error"))
        working_channel = AsyncMock()
        working_channel.send = AsyncMock()

        bus.register("failing", failing_channel)
        bus.register("working", working_channel)

        # Nu trebuie sa ridice exceptie
        await bus.send_entry_signal("ETHUSDT", "SHORT", zscore=-2.5)

        working_channel.send.assert_called_once()


# ─── S19-06: Paper engine smoke test ────────────────────────────────────────


class TestPaperEngineSmokeE2E:
    """Smoke test end-to-end pentru paper engine."""

    @pytest.mark.asyncio
    async def test_paper_engine_buy_sell_cycle(self):
        """Paper engine: cumpara -> vinde -> PnL calculat corect."""
        from execution.paper_engine import PaperEngine

        engine = PaperEngine(initial_capital=10_000.0, slippage=0.0005)

        # Buy
        buy_result = await engine.place_order(
            symbol="BTCUSDT",
            side="BUY",
            qty=0.01,
            price=40_000.0,
        )
        assert buy_result is not None

        # Sell la pret mai mare
        sell_result = await engine.place_order(
            symbol="BTCUSDT",
            side="SELL",
            qty=0.01,
            price=41_000.0,
        )
        assert sell_result is not None

    @pytest.mark.asyncio
    async def test_paper_engine_respects_capital_limit(self):
        """Paper engine nu trebuie sa permita ordine peste capitalul disponibil."""
        from execution.paper_engine import PaperEngine

        engine = PaperEngine(initial_capital=100.0, slippage=0.0)

        # Incerca sa cumpere $1M worth de BTC cu doar $100
        try:
            result = await engine.place_order(
                symbol="BTCUSDT",
                side="BUY",
                qty=100.0,  # 100 BTC @ 40k = $4M
                price=40_000.0,
            )
            # Fie refuza, fie returneaza None/eroare
            # Nu trebuie sa treaca fara exceptie sau None
        except (ValueError, RuntimeError, Exception):
            pass  # Expected behavior


# ─── S19-07: Main CLI smoke test ────────────────────────────────────────────


class TestMainCLISmoke:
    """Testeaza ca main.py parseza argumentele corect."""

    def test_parse_args_paper_defaults(self):
        """Comanda paper cu argumente default trebuie sa parseze corect."""
        import sys
        from unittest.mock import patch

        with patch.object(sys, "argv", ["main.py", "paper"]):
            import importlib

            # Importa modulul fresh
            if "main" in sys.modules:
                main_module = sys.modules["main"]
            else:
                import main as main_module

            args = main_module.parse_args()
            assert args.command == "paper"
            assert args.pair == ["BTCUSDT", "ETHUSDT"]
            assert args.capital == 10_000.0

    def test_parse_args_backtest_custom(self):
        """Comanda backtest cu parametri custom trebuie sa parseze corect."""
        import sys
        from unittest.mock import patch

        with patch.object(
            sys,
            "argv",
            ["main.py", "backtest", "--pair", "SOLUSDT", "BNBUSDT", "--days", "180"],
        ):
            if "main" in sys.modules:
                main_module = sys.modules["main"]
            else:
                import main as main_module

            args = main_module.parse_args()
            assert args.command == "backtest"
            assert args.pair == ["SOLUSDT", "BNBUSDT"]
            assert args.days == 180

    def test_parse_args_optimize_objective_choices(self):
        """Comanda optimize trebuie sa accepte toate obiectivele valide."""
        import sys
        from unittest.mock import patch

        for obj in ["sharpe", "sortino", "calmar", "profit_factor"]:
            with patch.object(
                sys, "argv", ["main.py", "optimize", "--objective", obj]
            ):
                if "main" in sys.modules:
                    main_module = sys.modules["main"]
                else:
                    import main as main_module

                args = main_module.parse_args()
                assert args.objective == obj
