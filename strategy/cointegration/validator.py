"""
QuantLuna — Cointegration Validator Pipeline  (Sprint 9)

Orchestrator care combină cele trei componente:
  1. EngleGrangerTest  — test primar pereche (Y, X)
  2. JohansenTest      — confirmare + hedge ratios din eigenvectors
  3. ResidualDiagnostics — validare calitate reziduuri

Output-ul este un ValidationReport care conține:
  - verdict final (VALID / MARGINAL / INVALID) cu raționament explicit
  - hedge ratio recomandat (Johansen vector 0 dacă disponibil, altfel EG beta)
  - half-life operațional
  - flag-uri de risc
  - acces la toate rapoartele sub-componente

Verdictul este conservator în mod intenționat:
  VALID    — ambele teste pozitive, diagnostice OK, half-life operațional
  MARGINAL — unul din teste pozitiv sau diagnostice cu probleme minore
             → pair-ul poate fi investigat mai departe, NU tranzacționat direct
  INVALID  — niciun test pozitiv sau probleme critice de diagnostice

Pipeline de utilizare recomandat:
  validator = CointegrationValidator()
  report = validator.validate(close_y, close_x, sym_y="ETH/USDT:USDT", sym_x="BTC/USDT:USDT")
  print(report.summary())

  if report.verdict == "VALID":
      # Treci la Kalman Filter cu report.hedge_ratio_initial ca seed
      # Rulează WalkForwardEngine
      # Rulează MonteCarloEngine
      # Abia după => LiveTrader

Limite reale:
  - ValidationReport.verdict == "VALID" nu garantează profitabilitate.
    Este un filtru necesar, nu suficient.
  - Testele sunt pe date istorice. Regimul de piață se poate schimba.
  - Rulați periodic re-validare (ex: săptămânal) pe date fresh.
  - Un pair valid la 1h poate fi invalid la 4h sau 15m.
    Timeframe-ul de validare = timeframe-ul de execuție.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from .engle_granger import EngleGrangerTest, EGResult
from .johansen import JohansenTest, JohansenResult
from .residual_diagnostics import ResidualDiagnostics, ResidualReport


@dataclass
class ValidatorConfig:
    # Engle-Granger
    eg_alpha: float = 0.05
    eg_trend: str = "c"
    eg_min_obs: int = 150

    # Johansen
    johansen_det_order: int = 0
    johansen_k_ar_diff: Optional[int] = None

    # Residual Diagnostics
    rd_half_life_min: float = 2.0
    rd_half_life_max: float = 120.0
    rd_stability_window: int = 60

    # Verdict thresholds
    require_both_tests: bool = False  # True = VALID numai dacă AMBELE teste pozitive
    require_diagnostics_passed: bool = True


@dataclass
class ValidationReport:
    sym_y: str
    sym_x: str
    verdict: str                   # "VALID" | "MARGINAL" | "INVALID"
    verdict_reason: str
    hedge_ratio_initial: float     # recomandat ca seed pentru Kalman Filter
    half_life_bars: Optional[float]
    eg_result: EGResult
    johansen_result: JohansenResult
    residual_report: ResidualReport
    risk_flags: List[str] = field(default_factory=list)

    def summary(self) -> str:
        sep = "=" * 60
        lines = [
            sep,
            f"COINTEGRATION VALIDATION: {self.sym_y} / {self.sym_x}",
            f"VERDICT: {self.verdict}  —  {self.verdict_reason}",
            f"Hedge ratio initial (Kalman seed): {self.hedge_ratio_initial:.6f}",
            f"Half-life operațional: "
            + (f"{self.half_life_bars:.1f} bars" if self.half_life_bars else "N/A"),
            sep,
            "[1] ENGLE-GRANGER",
            self.eg_result.summary(),
            sep,
            "[2] JOHANSEN",
            self.johansen_result.summary(),
            sep,
            "[3] RESIDUAL DIAGNOSTICS",
            self.residual_report.summary(),
            sep,
        ]
        if self.risk_flags:
            lines.append("RISK FLAGS: " + " | ".join(self.risk_flags))
            lines.append(sep)
        return "\n".join(lines)


class CointegrationValidator:
    """
    Orchestrator complet pentru validarea unui pair.

    Utilizare minimă:
      from strategy.cointegration import CointegrationValidator
      validator = CointegrationValidator()
      report = validator.validate(close_eth, close_btc,
                                  sym_y="ETH/USDT:USDT",
                                  sym_x="BTC/USDT:USDT")
      print(report.summary())
    """

    def __init__(self, cfg: Optional[ValidatorConfig] = None) -> None:
        self.cfg = cfg or ValidatorConfig()
        self._eg = EngleGrangerTest(
            alpha_threshold=self.cfg.eg_alpha,
            trend=self.cfg.eg_trend,
            min_obs=self.cfg.eg_min_obs,
        )
        self._joh = JohansenTest(
            det_order=self.cfg.johansen_det_order,
            k_ar_diff=self.cfg.johansen_k_ar_diff,
            min_obs=self.cfg.eg_min_obs,
        )
        self._rd = ResidualDiagnostics(
            half_life_min=self.cfg.rd_half_life_min,
            half_life_max=self.cfg.rd_half_life_max,
            stability_window=self.cfg.rd_stability_window,
        )

    def validate(
        self,
        close_y: pd.Series,
        close_x: pd.Series,
        sym_y: str = "Y",
        sym_x: str = "X",
    ) -> ValidationReport:
        """
        Rulează pipeline-ul complet pe close_y și close_x.
        Returnează ValidationReport cu verdict și toate detaliile.
        """
        # 1. Engle-Granger
        eg = self._eg.run(close_y, close_x, sym_y=sym_y, sym_x=sym_x)

        # 2. Johansen (pe DataFrame cu ambele serii aliniate)
        df_pair = pd.DataFrame({sym_y: close_y, sym_x: close_x}).dropna()
        joh = self._joh.run(df_pair, symbols=[sym_y, sym_x])

        # 3. Residual diagnostics pe reziduurile EG (mai stabile ca seed)
        rd = self._rd.run(eg.residuals)

        # --- Selectare hedge ratio initial ---
        # Johansen vector 0 dacă cointegration confirmată, altfel EG beta
        if joh.is_cointegrated and joh.hedge_ratios:
            # Johansen normalizează pe sym_y = 1.0, sym_x = ratio
            # Hedge ratio pentru short X = coef negativ al lui sym_x
            hr_joh = joh.hedge_ratios[0].get(sym_x, eg.beta)
            # Johansen returnează coef cu semn din ecuatia de cointegrare
            # Pentru pairs trading: hedge_ratio = abs(coef_x / coef_y)
            # Dacă sym_y normalizat la 1.0, hedge_ratio = abs(hr_joh)
            hedge_ratio_initial = abs(float(hr_joh))
        else:
            hedge_ratio_initial = abs(float(eg.beta))

        # --- Verdict ---
        verdict, reason, risk_flags = self._compute_verdict(
            eg, joh, rd, hedge_ratio_initial
        )

        # Half-life: preferăm valoarea din diagnostice (mai robustă)
        half_life = rd.half_life_bars or eg.half_life_bars

        return ValidationReport(
            sym_y=sym_y,
            sym_x=sym_x,
            verdict=verdict,
            verdict_reason=reason,
            hedge_ratio_initial=hedge_ratio_initial,
            half_life_bars=half_life,
            eg_result=eg,
            johansen_result=joh,
            residual_report=rd,
            risk_flags=risk_flags,
        )

    # ------------------------------------------------------------------
    # Verdict logic
    # ------------------------------------------------------------------

    def _compute_verdict(
        self,
        eg: EGResult,
        joh: JohansenResult,
        rd: ResidualReport,
        hedge_ratio: float,
    ) -> tuple[str, str, List[str]]:
        risk_flags: List[str] = []
        cfg = self.cfg

        eg_ok = eg.is_cointegrated
        joh_ok = joh.is_cointegrated
        diag_ok = rd.passed_all

        # Colectare risk flags specifice
        if not rd.lb_passed:
            risk_flags.append(f"autocorrelation_in_residuals (LB p={rd.lb_pvalue:.3f})")
        if not rd.arch_passed:
            risk_flags.append(f"ARCH_effects (p={rd.arch_pvalue:.3f}) — sigma instabilă")
        if not rd.half_life_passed:
            hl = f"{rd.half_life_bars:.1f}" if rd.half_life_bars else "N/A"
            risk_flags.append(f"half_life_out_of_range ({hl} bars)")
        if not rd.stability_passed:
            risk_flags.append(f"structural_break @ bar {rd.stability_break_idx}")
        if rd.jb_kurtosis > 8:
            risk_flags.append(f"fat_tails kurtosis={rd.jb_kurtosis:.1f} — sizing conservativ")
        if joh.n_cointegrating_vectors > 1:
            risk_flags.append(f"multiple_coint_vectors (r={joh.n_cointegrating_vectors})")
        if eg.n_obs < 200:
            risk_flags.append(f"small_sample ({eg.n_obs} obs) — low power")
        if hedge_ratio < 0.01 or hedge_ratio > 100:
            risk_flags.append(f"hedge_ratio_extreme ({hedge_ratio:.4f}) — verificați manual")

        # --- Verdict logic ---
        if cfg.require_both_tests:
            tests_passed = eg_ok and joh_ok
        else:
            tests_passed = eg_ok or joh_ok

        critical_diag_failure = not rd.lb_passed or not rd.half_life_passed or not rd.stability_passed

        if tests_passed and diag_ok:
            verdict = "VALID"
            reason = (
                f"EG={'OK' if eg_ok else 'FAIL'} | "
                f"Johansen={'OK' if joh_ok else 'FAIL'} | "
                f"Diagnostics=OK | HL={rd.half_life_bars:.1f}bars"
                if rd.half_life_bars else "OK"
            )
        elif tests_passed and not critical_diag_failure:
            verdict = "MARGINAL"
            reason = (
                f"Tests={'OK' if tests_passed else 'FAIL'} dar diagnostice cu issues minore. "
                f"Investigare suplimentară necesară."
            )
        elif tests_passed and critical_diag_failure:
            verdict = "MARGINAL"
            reason = (
                f"Tests pozitive dar diagnostice critice eșuate: "
                + ", ".join(f for f in risk_flags if any(
                    k in f for k in ["autocorr", "half_life", "structural"]))
            )
        else:
            verdict = "INVALID"
            reason = (
                f"EG={'OK' if eg_ok else 'FAIL'} | "
                f"Johansen={'OK' if joh_ok else 'FAIL'} | "
                f"Niciun test de cointegration pozitiv."
            )

        return verdict, reason, risk_flags
