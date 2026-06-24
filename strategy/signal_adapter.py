"""
QuantLuna — LiveSignalAdapter Sprint 6

Bridge layer între SignalGenerator.generate_live() și LiveTrader.

Problema rezolvată:
LiveTrader accesa atributele TradeSignal via getattr(sig, 'zscore', default),
getattr(sig, 'hedge_ratio', default) etc. TradeSignal nu are atribut 'hedge_ratio'
— îl are ca 'beta'. Același lucru pentru 'kalman_uncertainty' vs 'uncertainty'.
Aceasta crea o divergență silentioasă: LiveTrader primea 0.0 pentru hedge ratio
în loc de beta-ul real, ceea ce contamina _publish_state() și dashboard-ul.

Soluție:
LiveSignalAdapter wrappază SignalGenerator și expune o interfață normalizată
cu atributele exacte pe care LiveTrader le consumă. Niciun getattr fallback
nu mai e necesar în LiveTrader — va fi înlocuit cu accesul direct.

Nu modifică logica internă a SignalGenerator sau TradeSignal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd
from loguru import logger

from strategy.signal import SignalGenerator, TradeSignal, Signal


@dataclass
class NormalizedSignal:
    """
    Interfața standardizată pe care LiveTrader o consumă.
    Toate atributele sunt garantate prezente cu tipuri corecte.
    """
    # Core signal
    signal: Signal
    reason: str
    confidence: float
    bars_in_trade: int

    # Kalman state — normalizat față de TradeSignal
    zscore: float           # TradeSignal.zscore
    hedge_ratio: float      # TradeSignal.beta  (alias normalizat)
    alpha: float            # TradeSignal.alpha
    spread: float           # TradeSignal.spread
    kalman_gain: float      # TradeSignal.kalman_gain
    kalman_uncertainty: float  # TradeSignal.uncertainty (alias normalizat)
    half_life: Optional[float]  # TradeSignal.half_life_hours

    # Spread bands
    spread_upper: float
    spread_lower: float

    # Metadata
    timestamp: Optional[pd.Timestamp]
    regime_multiplier: float
    is_warm: bool           # False dacă SignalGenerator e în warmup

    @classmethod
    def from_trade_signal(cls, sig: TradeSignal) -> "NormalizedSignal":
        """Construiește NormalizedSignal din TradeSignal nativ."""
        return cls(
            signal=sig.signal,
            reason=sig.reason,
            confidence=sig.confidence,
            bars_in_trade=sig.bars_in_trade,
            zscore=sig.zscore,
            hedge_ratio=sig.beta,           # ALIAS: beta → hedge_ratio
            alpha=sig.alpha,
            spread=sig.spread,
            kalman_gain=sig.kalman_gain,
            kalman_uncertainty=sig.uncertainty,  # ALIAS: uncertainty → kalman_uncertainty
            half_life=sig.half_life_hours,
            spread_upper=sig.spread_upper,
            spread_lower=sig.spread_lower,
            timestamp=sig.timestamp,
            regime_multiplier=sig.regime_multiplier,
            is_warm=(sig.reason != "warming_up"),
        )

    def as_dict(self) -> dict:
        """Serializare pentru StateBus / WebSocket."""
        return {
            "signal": self.signal.name,
            "reason": self.reason,
            "confidence": round(self.confidence, 4),
            "bars_in_trade": self.bars_in_trade,
            "zscore": round(self.zscore, 4),
            "hedge_ratio": round(self.hedge_ratio, 6),
            "alpha": round(self.alpha, 6),
            "spread": round(self.spread, 6),
            "kalman_gain": round(self.kalman_gain, 6),
            "kalman_uncertainty": round(self.kalman_uncertainty, 6),
            "half_life": round(self.half_life, 2) if self.half_life is not None else None,
            "spread_upper": round(self.spread_upper, 6),
            "spread_lower": round(self.spread_lower, 6),
            "timestamp": str(self.timestamp) if self.timestamp else None,
            "regime_multiplier": round(self.regime_multiplier, 4),
            "is_warm": self.is_warm,
        }


class LiveSignalAdapter:
    """
    Wrapper peste SignalGenerator care expune interfata NormalizedSignal.

    Usage în LiveTrader:
        adapter = LiveSignalAdapter(signal_gen)
        sig: NormalizedSignal = await adapter.on_tick(y, x, ts, funding, regime)

        # Acces direct, fără getattr fallback:
        hedge_ratio = sig.hedge_ratio      # garantat float
        zscore      = sig.zscore           # garantat float
        kg          = sig.kalman_gain      # garantat float
        uncert      = sig.kalman_uncertainty  # garantat float
    """

    def __init__(self, signal_gen: SignalGenerator) -> None:
        self._gen = signal_gen

    def on_tick(
        self,
        y: float,
        x: float,
        ts: Optional[pd.Timestamp] = None,
        funding_annual: float = 0.0,
        regime_multiplier: float = 1.0,
    ) -> NormalizedSignal:
        """
        Single-bar online update.
        Returnează NormalizedSignal cu toate atributele garantate.
        """
        raw: TradeSignal = self._gen.generate_live(
            y=y,
            x=x,
            ts=ts,
            funding_annual=funding_annual,
            regime_multiplier=regime_multiplier,
        )
        return NormalizedSignal.from_trade_signal(raw)

    def signal_summary(self) -> dict:
        """Delegat la SignalGenerator.signal_summary()."""
        return self._gen.signal_summary()

    def reset(self) -> None:
        """Hard reset intern SignalGenerator."""
        self._gen.reset()

    def reset_kalman(self) -> None:
        """
        FIX-6: Reset explicit al stării Kalman Filter.

        Apelat de LiveTrader._on_reconnect() la fiecare WS reconnect
        pentru a evita hedge ratio stale după o întrerupere a feed-ului.

        Ordinea de prioritate:
          1. inner.kalman.reset()        — reset specific Kalman (preferat)
          2. inner.reset()               — full SignalGenerator reset (fallback)
          3. log warning                 — dacă nicio metodă nu există

        După reset, filtrul va trece prin warm_up bars înainte de a genera
        semnale. LiveTrader setează state=WARMING_UP separat.
        """
        inner = self._gen
        if hasattr(inner, "kalman") and hasattr(inner.kalman, "reset"):
            inner.kalman.reset()
            logger.info(
                "LiveSignalAdapter.reset_kalman(): Kalman state reset "
                "via inner.kalman.reset() — warm-up necesar"
            )
        elif hasattr(inner, "reset"):
            inner.reset()
            logger.warning(
                "LiveSignalAdapter.reset_kalman(): inner.kalman.reset() indisponibil — "
                "fallback la SignalGenerator.reset() (full reset, inclusiv buffers)"
            )
        else:
            logger.warning(
                "LiveSignalAdapter.reset_kalman(): nicio metoda reset disponibila "
                "pe SignalGenerator — beta poate fi stale dupa reconnect!"
            )

    @property
    def inner(self) -> SignalGenerator:
        """Acces direct la SignalGenerator intern (ex: backtest batch mode)."""
        return self._gen
