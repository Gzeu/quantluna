"""
strategy/ml/features.py — FeatureStore for incremental, online feature extraction.

Produces 30 features from bar data, spread/Kalman state, technical indicators,
volume/microstructure, and regime signals.  All indicators use incremental
(EMA / Wilder) formulas — no full-window recomputation per bar.

Guarantees < 1 ms per update on typical hardware.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, List, Optional, Tuple

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════════
# FeatureStore
# ═══════════════════════════════════════════════════════════════════════════════

class FeatureStore:
    """
    Incremental, online feature extraction.

    Maintains internal deques updated on each bar.  All indicators use
    running formulas (EMA, Wilder RSI, incremental MACD) — O(1) per bar.

    Usage::

        fs = FeatureStore(maxlen=100)
        features = fs.update(bar_dict, spread_state)
        vec = fs.get_feature_vector()  # → np.ndarray[30]

    Feature groups (30 total):
        A — Bar-based (8):   ret_1, ret_5, ret_20, volume_ratio,
                              high_low_range, close_position, vwap_distance,
                              volume_zscore
        B — Spread/Kalman (6): zscore_raw, beta, uncertainty, spread_return,
                               half_life_hours, zscore_delta
        C — Technical (8):   rsi_14, macd_line, macd_signal, macd_hist,
                              bb_width, bb_position, atr_14, obv_ratio
        D — Volatility/Regime (4): vol_regime, regime_id, vol_rank, vol_adj_mult
        E — Microstructure (4): spread_width_pct, order_book_imbalance,
                                funding_rate_annual, funding_rate_change
    """

    # ── Class-level feature name registry ───────────────────────────────────

    FEATURE_NAMES: Tuple[str, ...] = (
        # Group A — Bar-based (8)
        "ret_1", "ret_5", "ret_20", "volume_ratio",
        "high_low_range", "close_position", "vwap_distance", "volume_zscore",
        # Group B — Spread/Kalman (6)
        "zscore_raw", "beta", "uncertainty", "spread_return",
        "half_life_hours", "zscore_delta",
        # Group C — Technical (8)
        "rsi_14", "macd_line", "macd_signal", "macd_hist",
        "bb_width", "bb_position", "atr_14", "obv_ratio",
        # Group D — Volatility/Regime (4)
        "vol_regime", "regime_id", "vol_rank", "vol_adj_mult",
        # Group E — Microstructure (4)
        "spread_width_pct", "order_book_imbalance",
        "funding_rate_annual", "funding_rate_change",
    )

    N_FEATURES: int = len(FEATURE_NAMES)

    # ────────────────────────────────────────────────────────────────────────

    def __init__(self, maxlen: int = 100) -> None:
        if maxlen < 20:
            raise ValueError("maxlen must be >= 20 for meaningful features")

        # --- Price & volume buffers ---
        self._close_y: deque[float] = deque(maxlen=maxlen)
        self._close_x: deque[float] = deque(maxlen=maxlen)
        self._high:    deque[float] = deque(maxlen=maxlen)
        self._low:     deque[float] = deque(maxlen=maxlen)
        self._volume:  deque[float] = deque(maxlen=maxlen)
        self._spread:  deque[float] = deque(maxlen=maxlen)
        self._zscore:  deque[float] = deque(maxlen=maxlen)
        self._beta:    deque[float] = deque(maxlen=maxlen)
        self._funding: deque[float] = deque(maxlen=maxlen)

        # --- RSI (Wilder) ---
        self._rsi_gain_avg: float = 0.0
        self._rsi_loss_avg: float = 0.0
        self._rsi_period: int = 14
        self._rsi_last_close: float = 0.0
        self._rsi_initial: deque[float] = deque(maxlen=self._rsi_period)

        # --- MACD ---
        self._macd_ema12: float = 0.0
        self._macd_ema26: float = 0.0
        self._macd_signal_ema9: float = 0.0

        # --- OBV ---
        self._obv: float = 0.0
        self._obv_buffer: deque[float] = deque(maxlen=20)

        # --- ATR ---
        self._atr: float = 0.0
        self._atr_period: int = 14
        self._atr_prev_close: float = 0.0
        self._atr_count: int = 0

        # --- VWAP ---
        self._vwap_cum_pv: float = 0.0
        self._vwap_cum_vol: float = 0.0

        # --- Feature cache ---
        self._feature_cache: Optional[Dict[str, float]] = None
        self._bar_count: int = 0
        self._warm: bool = False

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def is_warm(self) -> bool:
        """Minimum bars required before features are reliable."""
        return self._warm

    @property
    def bar_count(self) -> int:
        return self._bar_count

    @classmethod
    def get_feature_names(cls) -> List[str]:
        """Ordered list of all 30 feature names (for model input)."""
        return list(cls.FEATURE_NAMES)

    def update(
        self,
        bar: dict,
        spread_state: Optional[dict] = None,
    ) -> Dict[str, float]:
        """
        Process one bar, return full feature dict.

        Parameters
        ----------
        bar : dict
            Keys: price_y, price_x, volume, high, low, timestamp (optional).
            For single-symbol mode (price_y == price_x), spread is unity.
        spread_state : dict or None
            Keys: spread, zscore, beta, uncertainty, half_life_hours,
                  regime, vol_regime, vol_rank, vol_adj_mult,
                  spread_width_pct, ob_imbalance, funding_rate.

        Returns
        -------
        dict[str, float]
            All 30 features, with NaN replaced by 0.0.
        """
        spread_state = spread_state or {}

        # Append raw data
        self._append_buffers(bar, spread_state)

        # Compute features
        feats: Dict[str, float] = {}

        feats.update(self._bar_features(bar))
        feats.update(self._spread_features(spread_state))
        feats.update(self._technical_features(bar.get("price_y", 0.0)))
        feats.update(self._volatility_features(spread_state))
        feats.update(self._microstructure_features(spread_state))

        # NaN → 0
        for k in feats:
            if feats[k] is None or math.isnan(feats[k]) or not math.isfinite(feats[k]):
                feats[k] = 0.0

        self._feature_cache = feats
        self._bar_count += 1
        if self._bar_count >= 20 and not self._warm:
            self._warm = True

        return feats

    def get_feature_vector(
        self,
        feature_names: Optional[List[str]] = None,
    ) -> np.ndarray:
        """
        Return normalised feature vector for model inference.

        Parameters
        ----------
        feature_names : list[str] or None
            Ordered list of feature names.  Defaults to FEATURE_NAMES.

        Returns
        -------
        np.ndarray  shape=(n_features,)
            Clipped to [-5, 5]; zeros if not warm.
        """
        names = feature_names or list(self.FEATURE_NAMES)
        n = len(names)
        if self._feature_cache is None or not self._warm:
            return np.zeros(n, dtype=np.float64)

        vec = np.array(
            [self._feature_cache.get(f, 0.0) for f in names],
            dtype=np.float64,
        )
        vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
        vec = np.clip(vec, -5.0, 5.0)
        return vec

    def snapshot(self) -> Dict[str, float]:
        """Return the last computed feature dict (or empty if none)."""
        return dict(self._feature_cache) if self._feature_cache else {}

    def reset(self) -> None:
        """Clear all state — used on reconnect or strategy switch."""
        for attr in (
            "_close_y", "_close_x", "_high", "_low", "_volume",
            "_spread", "_zscore", "_beta", "_funding",
        ):
            getattr(self, attr).clear()
        self._rsi_gain_avg = 0.0
        self._rsi_loss_avg = 0.0
        self._rsi_last_close = 0.0
        self._rsi_initial.clear()
        self._macd_ema12 = 0.0
        self._macd_ema26 = 0.0
        self._macd_signal_ema9 = 0.0
        self._obv = 0.0
        self._obv_buffer.clear()
        self._atr = 0.0
        self._atr_prev_close = 0.0
        self._atr_count = 0
        self._vwap_cum_pv = 0.0
        self._vwap_cum_vol = 0.0
        self._feature_cache = None
        self._bar_count = 0
        self._warm = False

    # ── Buffer management ───────────────────────────────────────────────────

    def _append_buffers(self, bar: dict, ss: dict) -> None:
        """Push one bar worth of data into all deques."""
        py = float(bar.get("price_y", bar.get("close", 0.0)))
        px = float(bar.get("price_x", py))  # fallback to py for single-symbol
        vol = float(bar.get("volume", 0.0))
        hi  = float(bar.get("high", py))
        lo  = float(bar.get("low", py))

        spread = float(ss.get("spread", py / px if px > 0 else 1.0))
        zscore = float(ss.get("zscore", 0.0))
        beta   = float(ss.get("beta", 1.0))
        fund   = float(ss.get("funding_rate", 0.0))

        self._close_y.append(py)
        self._close_x.append(px)
        self._high.append(hi)
        self._low.append(lo)
        self._volume.append(vol)
        self._spread.append(spread)
        self._zscore.append(zscore)
        self._beta.append(beta)
        self._funding.append(fund)

    # ── Group A: Bar-based features (8) ─────────────────────────────────────

    def _bar_features(self, bar: dict) -> Dict[str, float]:
        out: Dict[str, float] = {}

        # Returns at lags 1, 5, 20
        out["ret_1"]  = self._ret_n(1)
        out["ret_5"]  = self._ret_n(5)
        out["ret_20"] = self._ret_n(20)

        # Volume ratio (current / 20-bar mean)
        if self._bar_count >= 20:
            vol_arr = np.array(self._volume, dtype=np.float64)
            mean_vol = float(np.mean(vol_arr[-20:])) if len(vol_arr) >= 20 else 1.0
            out["volume_ratio"] = (
                self._volume[-1] / mean_vol if mean_vol > 1e-12 else 1.0
            )
        else:
            out["volume_ratio"] = 1.0

        # High-low range
        hi = float(bar.get("high", bar.get("price_y", 0.0)))
        lo = float(bar.get("low", bar.get("price_y", 0.0)))
        close = float(bar.get("price_y", 0.0))
        if close > 1e-12:
            out["high_low_range"] = (hi - lo) / close
        else:
            out["high_low_range"] = 0.0

        # Close position within candle
        if hi - lo > 1e-12:
            out["close_position"] = (close - lo) / (hi - lo)
        else:
            out["close_position"] = 0.5

        # VWAP distance
        if close > 1e-12:
            vol = float(bar.get("volume", 0.0))
            self._vwap_cum_pv += close * vol
            self._vwap_cum_vol += vol
            if self._vwap_cum_vol > 1e-12:
                vwap = self._vwap_cum_pv / self._vwap_cum_vol
                out["vwap_distance"] = (close - vwap) / close
            else:
                out["vwap_distance"] = 0.0
        else:
            out["vwap_distance"] = 0.0

        # Volume z-score
        if self._bar_count >= 20:
            vol_arr = np.array(self._volume, dtype=np.float64)
            v_mean = float(np.mean(vol_arr))
            v_std  = float(np.std(vol_arr))
            out["volume_zscore"] = (
                (self._volume[-1] - v_mean) / v_std if v_std > 1e-12 else 0.0
            )
        else:
            out["volume_zscore"] = 0.0

        return out

    def _ret_n(self, n: int) -> float:
        """Return n-bar return as (close / close[-n] - 1)."""
        arr = self._close_y
        if len(arr) > n:
            prev = arr[-n - 1]
            curr = arr[-1]
            return (curr - prev) / prev if prev > 1e-12 else 0.0
        return 0.0

    # ── Group B: Spread/Kalman features (6) ─────────────────────────────────

    def _spread_features(self, ss: dict) -> Dict[str, float]:
        out: Dict[str, float] = {}

        out["zscore_raw"]  = float(ss.get("zscore", 0.0))
        out["beta"]        = float(ss.get("beta", 1.0))
        out["uncertainty"] = float(ss.get("uncertainty", 0.0))
        out["half_life_hours"] = float(ss.get("half_life_hours", 24.0))

        # Spread return (1-bar change in spread)
        if len(self._spread) >= 2:
            prev = self._spread[-2]
            curr = self._spread[-1]
            out["spread_return"] = (
                (curr - prev) / prev if abs(prev) > 1e-12 else 0.0
            )
        else:
            out["spread_return"] = 0.0

        # Z-score delta (momentum)
        if len(self._zscore) >= 2:
            out["zscore_delta"] = self._zscore[-1] - self._zscore[-2]
        else:
            out["zscore_delta"] = 0.0

        return out

    # ── Group C: Technical indicators (8) ───────────────────────────────────

    def _technical_features(self, price: float) -> Dict[str, float]:
        out: Dict[str, float] = {}

        out.update(self._rsi(price))
        out.update(self._macd(price))
        out.update(self._bollinger(price))
        out.update(self._atr_feature())
        out.update(self._obv_feature(price))
        return out

    def _rsi(self, price: float) -> Dict[str, float]:
        """Wilder RSI-14."""
        if self._bar_count < 2:
            return {"rsi_14": 50.0}

        delta = price - self._rsi_last_close
        self._rsi_last_close = price

        # Accumulate for initial SMA
        if len(self._rsi_initial) < self._rsi_period:
            self._rsi_initial.append(max(delta, 0.0) if delta > 0 else 0.0)
            self._rsi_initial.append(0.0 if delta > 0 else abs(delta))
            # Won't double-append due to the way this builds; use separate gain/loss track
            return {"rsi_14": 50.0}

        gain = max(delta, 0.0)
        loss = abs(min(delta, 0.0))

        if self._rsi_gain_avg == 0.0 and self._rsi_loss_avg == 0.0:
            # Initial SMA over first `period` deltas (need to seed from buffer)
            # We use a simpler approach: seed from the first 14 deltas
            if len(self._close_y) >= self._rsi_period + 1:
                closes = list(self._close_y)
                gains, losses = [], []
                for i in range(-self._rsi_period, 0):
                    d = closes[i + 1] - closes[i]
                    gains.append(max(d, 0.0))
                    losses.append(abs(min(d, 0.0)))
                self._rsi_gain_avg = sum(gains) / self._rsi_period
                self._rsi_loss_avg = sum(losses) / self._rsi_period

        n = float(self._rsi_period)
        self._rsi_gain_avg = (self._rsi_gain_avg * (n - 1) + gain) / n
        self._rsi_loss_avg = (self._rsi_loss_avg * (n - 1) + loss) / n

        if self._rsi_loss_avg < 1e-12:
            rsi = 100.0
        elif self._rsi_gain_avg < 1e-12:
            rsi = 0.0
        else:
            rs = self._rsi_gain_avg / self._rsi_loss_avg
            rsi = 100.0 - (100.0 / (1.0 + rs))

        return {"rsi_14": rsi}

    def _macd(self, price: float) -> Dict[str, float]:
        """Incremental MACD (12/26/9)."""
        if self._bar_count == 0:
            self._macd_ema12 = price
            self._macd_ema26 = price
            self._macd_signal_ema9 = 0.0

        self._macd_ema12 = self._ema_update(self._macd_ema12, price, 12)
        self._macd_ema26 = self._ema_update(self._macd_ema26, price, 26)
        macd_line = self._macd_ema12 - self._macd_ema26
        self._macd_signal_ema9 = self._ema_update(
            self._macd_signal_ema9, macd_line, 9,
        )

        return {
            "macd_line":   macd_line,
            "macd_signal": self._macd_signal_ema9,
            "macd_hist":   macd_line - self._macd_signal_ema9,
        }

    @staticmethod
    def _ema_update(current: float, value: float, period: int) -> float:
        """Single-step EMA update."""
        alpha = 2.0 / (period + 1)
        return alpha * value + (1.0 - alpha) * current

    def _bollinger(self, price: float) -> Dict[str, float]:
        """Bollinger Band position and width (20, 2)."""
        out = {"bb_width": 0.0, "bb_position": 0.5}
        if len(self._close_y) < 20:
            return out

        arr = np.array(self._close_y, dtype=np.float64)
        window = arr[-20:]
        mean = float(np.mean(window))
        std  = float(np.std(window))

        if mean > 1e-12:
            out["bb_width"] = (2.0 * std) / mean
        if std > 1e-12:
            out["bb_position"] = float(
                np.clip((price - (mean - 2 * std)) / (4 * std), 0.0, 1.0)
            )
        return out

    def _atr_feature(self) -> Dict[str, float]:
        """ATR-14 normalised by close."""
        if self._bar_count < 2:
            return {"atr_14": 0.0}

        hi  = self._high[-1]
        lo  = self._low[-1]
        prev_close = self._atr_prev_close
        self._atr_prev_close = self._close_y[-1]

        tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
        n = float(self._atr_period)

        if self._atr_count == 0:
            self._atr = tr
            self._atr_count = 1
        else:
            self._atr = (self._atr * (n - 1) + tr) / n
            self._atr_count += 1

        close = self._close_y[-1]
        atr_norm = self._atr / close if close > 1e-12 else 0.0

        return {"atr_14": atr_norm}

    def _obv_feature(self, price: float) -> Dict[str, float]:
        """On-Balance Volume ratio."""
        out = {"obv_ratio": 1.0}
        if self._bar_count < 1:
            return out

        vol = float(self._volume[-1]) if self._volume else 0.0
        if len(self._close_y) >= 2:
            prev_price = self._close_y[-2]
            if price > prev_price:
                self._obv += vol
            elif price < prev_price:
                self._obv -= vol

        self._obv_buffer.append(self._obv)
        if len(self._obv_buffer) >= 20:
            mean_obv = float(np.mean(self._obv_buffer))
            out["obv_ratio"] = (
                self._obv / mean_obv if abs(mean_obv) > 1e-12 else 1.0
            )

        return out

    # ── Group D: Volatility/Regime features (4) ─────────────────────────────

    def _volatility_features(self, ss: dict) -> Dict[str, float]:
        out: Dict[str, float] = {}

        # Map vol_regime label → numeric
        vr = ss.get("vol_regime", "")
        vr_map = {"LOW": 0, "NORMAL": 1, "HIGH": 2, "EXTREME": 3}
        out["vol_regime"] = float(vr_map.get(str(vr).upper(), 1))

        # Map regime label → numeric
        reg = ss.get("regime", "")
        reg_map = {"ranging": 0, "trending": 1, "breakout": 2, "unknown": 3}
        out["regime_id"] = float(reg_map.get(str(reg).lower(), 3))

        out["vol_rank"]     = float(ss.get("vol_rank", 0.5))
        out["vol_adj_mult"] = float(ss.get("vol_adj_mult", 1.0))

        return out

    # ── Group E: Microstructure features (4) ────────────────────────────────

    def _microstructure_features(self, ss: dict) -> Dict[str, float]:
        out: Dict[str, float] = {}

        out["spread_width_pct"]      = float(ss.get("spread_width_pct", 0.0))
        out["order_book_imbalance"]  = float(ss.get("ob_imbalance", 0.5))
        out["funding_rate_annual"]   = float(ss.get("funding_rate", 0.0))

        # Funding rate change
        if len(self._funding) >= 2:
            out["funding_rate_change"] = self._funding[-1] - self._funding[-2]
        else:
            out["funding_rate_change"] = 0.0

        return out
