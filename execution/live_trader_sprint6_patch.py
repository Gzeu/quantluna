"""
QuantLuna — LiveTrader Sprint 6 Patch Notes & Integration Guide

Acest fișier documentează ce trebuie modificat în execution/live_trader.py
pentru a integra Sprint 6. Nu e un fișier executabil — e un ghid de patch
structurat pentru a evita conflicte cu codul existent.

=============================================================================
MODIFICĂRI NECESARE ÎN live_trader.py
=============================================================================

1. IMPORTS NOI
--------------
Adaugă la secțiunea de imports:

    from execution.funding_monitor import FundingMonitor, FundingConfig, create_funding_monitor
    from execution.pnl_reconciler import PnLReconciler, ReconcilerConfig
    from strategy.signal_adapter import LiveSignalAdapter, NormalizedSignal

2. LiveConfig — CÂMPURI NOI
----------------------------
Adaugă în dataclass LiveConfig:

    # Sprint 6 — Funding Monitor
    funding_poll_interval_s: float = 60.0
    funding_periods_per_year: float = 3.0 * 365.0  # Bybit USDT perp = 3x/day
    funding_alert_threshold: float = 0.05           # 5% annualized

    # Sprint 6 — P&L Reconciler
    pnl_reconcile_interval_s: float = 30.0
    pnl_drift_alert_usd: float = 5.0

    # Sprint 6 — API credentials pentru monitoring tasks
    # (pot fi aceleași cu cele de trading sau un read-only sub-account)
    monitor_api_key: str = ""
    monitor_api_secret: str = ""

3. __init__ — INIȚIALIZARE ADAPTER
------------------------------------
Înlocuiește:
    self.signal_gen = signal_gen  # SignalGenerator

Cu:
    # Dacă signal_gen e deja LiveSignalAdapter, folosește-l direct
    # Dacă e SignalGenerator raw, wrappează-l
    from strategy.signal_adapter import LiveSignalAdapter
    from strategy.signal import SignalGenerator
    if isinstance(signal_gen, LiveSignalAdapter):
        self.signal_gen = signal_gen
    elif isinstance(signal_gen, SignalGenerator):
        self.signal_gen = LiveSignalAdapter(signal_gen)
    else:
        raise TypeError(f"signal_gen must be SignalGenerator or LiveSignalAdapter, got {type(signal_gen)}")

    self._funding_task: Optional[asyncio.Task] = None
    self._reconciler_task: Optional[asyncio.Task] = None
    self._funding_monitor_exchange = None  # pentru close() la shutdown

4. run() — LANSARE TASKS
-------------------------
La începutul metodei run(), înainte de bucla principală, adaugă:

    # Sprint 6: lansare monitoring tasks
    if self.cfg.monitor_api_key:
        funding_cfg = FundingConfig(
            sym_y=self.cfg.sym_y,
            sym_x=self.cfg.sym_x,
            poll_interval_s=self.cfg.funding_poll_interval_s,
            funding_periods_per_year=self.cfg.funding_periods_per_year,
            exchange_id=self.cfg.exchange_id,
            testnet=self.cfg.testnet,
        )
        monitor, self._funding_monitor_exchange = await create_funding_monitor(
            funding_cfg, self.cfg.monitor_api_key, self.cfg.monitor_api_secret, self.bus
        )
        self._funding_task = asyncio.create_task(monitor.run(), name="funding_monitor")

        reconciler_cfg = ReconcilerConfig(
            sym_y=self.cfg.sym_y,
            sym_x=self.cfg.sym_x,
            poll_interval_s=self.cfg.pnl_reconcile_interval_s,
            drift_alert_usd=self.cfg.pnl_drift_alert_usd,
            exchange_id=self.cfg.exchange_id,
            testnet=self.cfg.testnet,
        )
        reconciler = PnLReconciler(
            reconciler_cfg,
            self._funding_monitor_exchange,  # refolosim exchange-ul existent
            self.bus
        )
        self._reconciler_task = asyncio.create_task(reconciler.run(), name="pnl_reconciler")

5. shutdown() / cleanup — CANCEL TASKS
---------------------------------------
Adaugă în metoda de shutdown sau în blocul finally din run():

    if self._funding_task and not self._funding_task.done():
        self._funding_task.cancel()
        try:
            await self._funding_task
        except asyncio.CancelledError:
            pass

    if self._reconciler_task and not self._reconciler_task.done():
        self._reconciler_task.cancel()
        try:
            await self._reconciler_task
        except asyncio.CancelledError:
            pass

    if self._funding_monitor_exchange:
        await self._funding_monitor_exchange.close()

6. _process_tick() — ÎNLOCUIRE getattr CU ACCES DIRECT
-------------------------------------------------------
Înlocuiește blocul:
    zscore     = getattr(sig, 'zscore', 0.0)
    hedge_ratio = getattr(sig, 'hedge_ratio', 1.0)
    kalman_gain = getattr(sig, 'kalman_gain', 0.0)
    uncertainty = getattr(sig, 'kalman_uncertainty', 0.0)

Cu (sig e acum NormalizedSignal garantat):
    zscore      = sig.zscore
    hedge_ratio = sig.hedge_ratio
    kalman_gain = sig.kalman_gain
    uncertainty = sig.kalman_uncertainty

7. _publish_state() — CÂMPURI NOI DIN BUS
------------------------------------------
StateBus primește acum automat funding_y, funding_x, funding_net
de la FundingMonitor și reconciled_open_pnl, pnl_drift_usd, pnl_drift_alert
de la PnLReconciler. Nu e nevoie de cod adițional în _publish_state().
Dashboard-ul le preia din snapshot via /state sau /ws.

8. StateSnapshot — CÂMPURI NOI (în state_bus.py)
-------------------------------------------------
Adaugă în StateSnapshot dataclass:

    # Funding (populat de FundingMonitor)
    funding_y: float = 0.0
    funding_x: float = 0.0
    funding_net: float = 0.0

    # P&L Reconciliation (populat de PnLReconciler)
    reconciled_open_pnl: float = 0.0
    pnl_drift_usd: float = 0.0
    pnl_drift_alert: bool = False
    position_size_y: float = 0.0
    position_size_x: float = 0.0
    entry_price_y: float = 0.0
    entry_price_x: float = 0.0

=============================================================================
EXEMPLU COMPLET DE INSTANȚIERE — main.py Sprint 6
=============================================================================

    import asyncio
    from state_bus import bus
    from dashboard.server import start_dashboard
    from strategy.signal import SignalGenerator
    from strategy.signal_adapter import LiveSignalAdapter
    from execution.live_trader import LiveTrader, LiveConfig

    signal_gen = SignalGenerator(spread_engine, cfg=signal_cfg)
    adapter    = LiveSignalAdapter(signal_gen)

    config = LiveConfig(
        sym_y="ETH/USDT:USDT",
        sym_x="BTC/USDT:USDT",
        state_bus_enabled=True,
        monitor_api_key="your_api_key",
        monitor_api_secret="your_api_secret",
        funding_poll_interval_s=60.0,
        pnl_reconcile_interval_s=30.0,
        pnl_drift_alert_usd=5.0,
    )

    trader = LiveTrader(config, adapter, portfolio_risk, state_bus=bus)

    async def main():
        await asyncio.gather(start_dashboard(), trader.run())

    asyncio.run(main())

=============================================================================
NOTE IMPORTANTE
=============================================================================

FundingMonitor reutilizează exchange-ul CCXT din PnLReconciler
(același obiect ccxt.Exchange) pentru a limita numărul de conexiuni REST.
Dacă vrei izolare completă, instanțiază exchange-uri separate.

funding_periods_per_year default = 3 * 365 = 1095 (Bybit USDT perp: funding la 8h).
Pentru contracte cu funding la 4h: 6 * 365 = 2190.
Verifică specificațiile contractului înainte de deploy în producție.

pnl_drift_alert_usd = 5.0 USD este un prag conservator pentru conturi mici.
Pentru conturi > 10k USD, ridică pragul la 20-50 USD pentru a evita
false positives din latența normală REST vs WS.
"""
