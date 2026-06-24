"""
QuantLuna — Portfolio Drawdown Controller  (Sprint 10)

DD Control pe trei niveluri:

  LEVEL 1 — Pair-level soft stop
    Dacă un pair individual depășește max_pair_dd, îl închide forțat.
    Logica: un pair în drawdown extins sugerează fie breakdown de
    cointegration, fie regim shift. Nu așteptăm mean reversion care
    nu mai vine.

  LEVEL 2 — Portfolio soft limit
    Dacă DD agregat depășește portfolio_soft_dd:
    - Nicio poziție nouă
    - Pozițiile existente rămân deschise (nu le forțăm la pierdere)
    - LogWarning + alert în StateBus

  LEVEL 3 — Portfolio hard stop (circuit breaker)
    Dacă DD agregat depășește portfolio_hard_dd:
    - Toate pozițiile se marchează pentru închidere imediată
    - Trading halted complet
    - Reset manual necesar (safety gate explicit)

De ce trei niveluri, nu unul:
  Un circuit breaker binar (on/off) este prea agresiv pe crypto:
  volatilitatea intraday poate declanșa și opri circuitul de mai
  multe ori pe zi. Trei niveluri cu praguri diferite dă sistemului
  spațiu să respire la nivel pair, dar protejează capitalul total.

Tracking equity curve:
  DDController menține propria equity curve pentru a calcula
  drawdown față de high-water mark (HWM), nu față de capital inițial.
  Aceasta este metrica corectă pentru prop trading.

Limite reale:
  - Pair-level DD este calculat pe open PnL (mark-to-market).
    La slippage mare la exit, pierderea reală poate depăși limitele.
  - Hard stop declanșat în miezul nopții pe crypto poate coincide
    cu lichiditate minimă. Adăugați un delay de execuție dacă
    exchange-ul are spread mare în acel moment.
  - DDController nu cunoaște cauzele drawdown-ului. Poate fi
    cointegration breakdown sau poate fi wick temporar. Analiza
    post-hoc este obligatorie înainte de re-activare.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import numpy as np


class DDLevel(Enum):
    NORMAL = "NORMAL"
    SOFT_LIMIT = "SOFT_LIMIT"     # nicio poziție nouă
    HARD_STOP = "HARD_STOP"       # toate pozițiile se închid, trading halted


@dataclass
class DDConfig:
    pair_soft_dd: float = 0.05        # 5% DD pe pair → forțare exit pair individual
    portfolio_soft_dd: float = 0.08   # 8% DD portfolio → nicio poziție nouă
    portfolio_hard_dd: float = 0.15   # 15% DD portfolio → circuit breaker total
    capital_usd: float = 10_000.0
    hwm_reset_on_manual_resume: bool = True  # resetare HWM la re-activare manuală


@dataclass
class PairDDState:
    pair_id: str
    entry_equity_snapshot: float     # equity la deschiderea poziției
    current_open_pnl: float = 0.0
    max_open_pnl: float = 0.0        # peak open PnL pentru HWM pair-level
    force_close: bool = False


@dataclass
class DDSnapshot:
    level: DDLevel
    portfolio_dd_pct: float
    portfolio_equity: float
    hwm: float
    pairs_force_close: List[str]
    notes: List[str]


class DrawdownController:
    """
    Controller de drawdown pe trei niveluri pentru portfolio multi-pair.

    Integrare cu LiveTrader:
      În loop-ul principal, apelați:
        snap = dd_ctrl.update(open_pnl_per_pair)
        if snap.level == DDLevel.HARD_STOP:
            await live_trader.close_all()
        elif snap.level == DDLevel.SOFT_LIMIT:
            live_trader.block_new_entries()
        for pair_id in snap.pairs_force_close:
            await live_trader.close_pair(pair_id)
    """

    def __init__(self, cfg: Optional[DDConfig] = None) -> None:
        self.cfg = cfg or DDConfig()
        self._equity_curve: List[float] = [self.cfg.capital_usd]
        self._hwm: float = self.cfg.capital_usd
        self._level: DDLevel = DDLevel.NORMAL
        self._pair_states: Dict[str, PairDDState] = {}
        self._hard_stop_triggered: bool = False
        self._resume_armed: bool = False  # flag pentru re-activare manuală

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open_pair(self, pair_id: str) -> None:
        """Înregistrează deschiderea unui pair nou."""
        equity_now = self._equity_curve[-1]
        self._pair_states[pair_id] = PairDDState(
            pair_id=pair_id,
            entry_equity_snapshot=equity_now,
        )

    def close_pair(self, pair_id: str) -> Optional[PairDDState]:
        """Marchează un pair ca închis și returnează starea finală."""
        return self._pair_states.pop(pair_id, None)

    def update(self, open_pnl_per_pair: Dict[str, float]) -> DDSnapshot:
        """
        Actualizează starea DD cu PnL-ul curent per pair.

        Parametri:
          open_pnl_per_pair — dict {pair_id: open_pnl_usd}
        """
        notes: List[str] = []
        pairs_force_close: List[str] = []
        cfg = self.cfg

        # Actualizare open PnL per pair
        total_open_pnl = 0.0
        for pair_id, pnl in open_pnl_per_pair.items():
            if pair_id not in self._pair_states:
                self.open_pair(pair_id)
            state = self._pair_states[pair_id]
            state.current_open_pnl = float(pnl)
            state.max_open_pnl = max(state.max_open_pnl, float(pnl))
            total_open_pnl += float(pnl)

            # Level 1: pair-level soft stop
            pair_dd = self._pair_dd(state)
            if pair_dd >= cfg.pair_soft_dd:
                state.force_close = True
                pairs_force_close.append(pair_id)
                notes.append(
                    f"PAIR_DD: {pair_id} DD={pair_dd:.1%} ≥ {cfg.pair_soft_dd:.1%}"
                )

        # Portfolio equity curentă
        equity = cfg.capital_usd + total_open_pnl
        self._equity_curve.append(equity)

        # Update HWM
        if equity > self._hwm:
            self._hwm = equity

        # Portfolio DD față de HWM
        portfolio_dd = max(0.0, (self._hwm - equity) / self._hwm) if self._hwm > 0 else 0.0

        # Level 2: soft limit
        if portfolio_dd >= cfg.portfolio_hard_dd:
            self._level = DDLevel.HARD_STOP
            self._hard_stop_triggered = True
            pairs_force_close = list(self._pair_states.keys())  # toate
            notes.append(
                f"HARD_STOP: portfolio DD={portfolio_dd:.1%} ≥ {cfg.portfolio_hard_dd:.1%}"
            )
        elif portfolio_dd >= cfg.portfolio_soft_dd:
            self._level = DDLevel.SOFT_LIMIT
            notes.append(
                f"SOFT_LIMIT: portfolio DD={portfolio_dd:.1%} ≥ {cfg.portfolio_soft_dd:.1%}"
            )
        elif not self._hard_stop_triggered:
            self._level = DDLevel.NORMAL

        return DDSnapshot(
            level=self._level,
            portfolio_dd_pct=portfolio_dd,
            portfolio_equity=equity,
            hwm=self._hwm,
            pairs_force_close=list(set(pairs_force_close)),
            notes=notes,
        )

    def manual_resume(self) -> bool:
        """
        Re-activare manuală după HARD_STOP.
        Necesită apel explicit — nu se auto-resetează.
        Returns True dacă re-activarea a reușit.
        """
        if not self._hard_stop_triggered:
            return False  # nu era în hard stop
        self._hard_stop_triggered = False
        self._level = DDLevel.NORMAL
        if self.cfg.hwm_reset_on_manual_resume:
            self._hwm = self._equity_curve[-1]  # HWM reset la equity curentă
        return True

    @property
    def level(self) -> DDLevel:
        return self._level

    @property
    def is_trading_allowed(self) -> bool:
        return self._level == DDLevel.NORMAL

    @property
    def can_open_new(self) -> bool:
        return self._level == DDLevel.NORMAL

    # ------------------------------------------------------------------
    # Helpers private
    # ------------------------------------------------------------------

    def _pair_dd(self, state: PairDDState) -> float:
        """
        DD al unui pair față de peak open PnL.
        Folosim peak PnL, nu zero, pentru a nu penaliza
        traderele care niciodată n-au fost profitabile pe pair.
        Dacă pair-ul n-a avut niciodată profit, DD față de entry.
        """
        if state.max_open_pnl > 0:
            # DD față de peak profit
            dd = (state.max_open_pnl - state.current_open_pnl) / (
                state.entry_equity_snapshot + state.max_open_pnl + 1e-9
            )
        else:
            # DD față de capital alocat la entry
            dd = abs(state.current_open_pnl) / (state.entry_equity_snapshot + 1e-9)
        return max(0.0, float(dd))
