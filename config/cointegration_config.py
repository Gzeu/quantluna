"""
config/cointegration_config.py  —  QuantLuna Cointegration Config

Sprint 14 FIX: centralizează toți parametrii testelor de cointegration
într-un singur dataclass configurabil. Elimină valorile hardcodate din
EngleGrangerTest, JohansenTest și CointegrationValidator.

Înainte (Sprint 9, hardcodat):
    EngleGrangerTest()           # alpha_threshold=0.05 hardcodat
    JohansenTest(signif=0.05)    # 0.05 hardcodat în codul callerului
    CointegrationValidator(...)  # min_half_life_h, max_half_life_h hardcodate

După (Sprint 14):
    from config.cointegration_config import CointegrationConfig
    cfg = CointegrationConfig()  # toate default-urile într-un loc
    # sau override selectiv:
    cfg = CointegrationConfig(adf_alpha=0.01, min_half_life_h=4.0)
    eg = EngleGrangerTest(alpha_threshold=cfg.adf_alpha)

Poate fi suprascris din:
    - .env (via pydantic-settings)
    - config/live_config.py
    - scripts/optimize_params.py --cointegration-alpha 0.01
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass
class CointegrationConfig:
    """
    Parametrii completi pentru toate testele de cointegration.

    Engle-Granger:
      adf_alpha           — p-value threshold ADF (default 0.05)
                           Conservative: 0.01, Liberal: 0.10
      adf_trend           — specificație trend ADF ('c', 'ct', 'nc')
      adf_max_lags        — max lags ADF, None = autoselect AIC
      eg_min_obs          — observații minime pentru EG test valid

    Johansen:
      johansen_signif     — nivel de semnificație tabelele Johansen
                           Valori valide: 0.10, 0.05, 0.01
      johansen_det_order  — deterministic term: -1 (none), 0 (constant), 1 (trend)
      johansen_k_ar_diff  — lags în modelul VAR Johansen
      johansen_min_obs    — observații minime pentru Johansen valid

    Half-life / Mean reversion:
      min_half_life_h     — half-life minimă acceptată în ore
                           Sub aceasta: spread mean-reverts prea rapid (noise)
      max_half_life_h     — half-life maximă acceptată în ore
                           Peste aceasta: spread mean-reverts prea lent (capital blocat)

    Residual diagnostics:
      lb_lags             — lags Ljung-Box autocorrelation test
      lb_alpha            — p-value threshold Ljung-Box
      normality_alpha     — p-value threshold Jarque-Bera / Shapiro-Wilk
      hurst_threshold     — Hurst exponent max (< 0.5 = mean-reverting)

    Validation gates:
      require_both_tests  — True: perechea trebuie să treacă EG + Johansen
                           False: suficient una din cele două (OR logic)
      require_half_life   — True: validare half-life obligatorie
      require_residuals   — True: Ljung-Box + normalitate obligatorii
    """

    # --- Engle-Granger ------------------------------------------------
    adf_alpha: float = 0.05
    adf_trend: Literal["c", "ct", "nc"] = "c"
    adf_max_lags: Optional[int] = None
    eg_min_obs: int = 150

    # --- Johansen -----------------------------------------------------
    johansen_signif: float = 0.05
    johansen_det_order: int = 0
    johansen_k_ar_diff: int = 1
    johansen_min_obs: int = 200

    # --- Half-life ----------------------------------------------------
    min_half_life_h: float = 2.0    # ore  (sub 2h = prea rapid pentru 1h bars)
    max_half_life_h: float = 168.0  # ore  (7 zile maxim)

    # --- Residual diagnostics -----------------------------------------
    lb_lags: int = 10
    lb_alpha: float = 0.05
    normality_alpha: float = 0.05
    hurst_threshold: float = 0.50

    # --- Validation gates ---------------------------------------------
    require_both_tests: bool = False  # OR logic: EG sau Johansen
    require_half_life: bool = True
    require_residuals: bool = False   # diagnosticele sunt informative, nu blocking

    def __post_init__(self) -> None:
        """Validate config on creation."""
        if not (0.0 < self.adf_alpha < 1.0):
            raise ValueError(f"adf_alpha must be in (0, 1), got {self.adf_alpha}")
        if self.johansen_signif not in (0.10, 0.05, 0.01):
            raise ValueError(
                f"johansen_signif must be 0.10, 0.05 or 0.01, got {self.johansen_signif}"
            )
        if self.min_half_life_h <= 0:
            raise ValueError(f"min_half_life_h must be > 0, got {self.min_half_life_h}")
        if self.max_half_life_h <= self.min_half_life_h:
            raise ValueError(
                f"max_half_life_h ({self.max_half_life_h}) must be > "
                f"min_half_life_h ({self.min_half_life_h})"
            )
        if self.johansen_det_order not in (-1, 0, 1):
            raise ValueError(
                f"johansen_det_order must be -1, 0 or 1, got {self.johansen_det_order}"
            )

    def to_engle_granger_kwargs(self) -> dict:
        """Returns kwargs dict pentru EngleGrangerTest(**kwargs)."""
        return {
            "alpha_threshold": self.adf_alpha,
            "trend": self.adf_trend,
            "max_lags": self.adf_max_lags,
            "min_obs": self.eg_min_obs,
        }

    def to_johansen_kwargs(self) -> dict:
        """Returns kwargs dict pentru JohansenTest(**kwargs)."""
        return {
            "signif": self.johansen_signif,
            "det_order": self.johansen_det_order,
            "k_ar_diff": self.johansen_k_ar_diff,
            "min_obs": self.johansen_min_obs,
        }

    def to_validator_kwargs(self) -> dict:
        """Returns kwargs dict pentru CointegrationValidator(**kwargs)."""
        return {
            "min_half_life_h": self.min_half_life_h,
            "max_half_life_h": self.max_half_life_h,
            "require_both_tests": self.require_both_tests,
            "require_half_life": self.require_half_life,
        }

    @classmethod
    def conservative(cls) -> "CointegrationConfig":
        """Preset conservator: alpha=0.01, ambele teste, diagnostice stricte."""
        return cls(
            adf_alpha=0.01,
            johansen_signif=0.01,
            require_both_tests=True,
            require_half_life=True,
            require_residuals=True,
            min_half_life_h=4.0,
            max_half_life_h=120.0,
        )

    @classmethod
    def liberal(cls) -> "CointegrationConfig":
        """Preset liberal: alpha=0.10, un singur test, fără diagnostice."""
        return cls(
            adf_alpha=0.10,
            johansen_signif=0.10,
            require_both_tests=False,
            require_half_life=False,
            require_residuals=False,
        )

    @classmethod
    def from_env(cls) -> "CointegrationConfig":
        """
        Construiește config din variabile de mediu.
        Variabile suportate (prefix QUANTLUNA_COINT_):
            QUANTLUNA_COINT_ADF_ALPHA=0.05
            QUANTLUNA_COINT_MIN_HALF_LIFE_H=2.0
            QUANTLUNA_COINT_MAX_HALF_LIFE_H=168.0
            QUANTLUNA_COINT_REQUIRE_BOTH_TESTS=false
        """
        import os

        def _float(key: str, default: float) -> float:
            v = os.environ.get(f"QUANTLUNA_COINT_{key}")
            return float(v) if v is not None else default

        def _bool(key: str, default: bool) -> bool:
            v = os.environ.get(f"QUANTLUNA_COINT_{key}")
            if v is None:
                return default
            return v.lower() in ("true", "1", "yes")

        def _int(key: str, default: int) -> int:
            v = os.environ.get(f"QUANTLUNA_COINT_{key}")
            return int(v) if v is not None else default

        return cls(
            adf_alpha=_float("ADF_ALPHA", 0.05),
            johansen_signif=_float("JOHANSEN_SIGNIF", 0.05),
            min_half_life_h=_float("MIN_HALF_LIFE_H", 2.0),
            max_half_life_h=_float("MAX_HALF_LIFE_H", 168.0),
            require_both_tests=_bool("REQUIRE_BOTH_TESTS", False),
            require_half_life=_bool("REQUIRE_HALF_LIFE", True),
            require_residuals=_bool("REQUIRE_RESIDUALS", False),
            eg_min_obs=_int("EG_MIN_OBS", 150),
        )
