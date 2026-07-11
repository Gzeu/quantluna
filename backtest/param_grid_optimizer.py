"""
backtest/param_grid_optimizer.py  -  QuantLuna Param Grid Optimizer v1.0

Sprint S39 (2026-07-12):
  Grid search exhaustiv pe parametrii de intrare/iesire ai strategiei
  pairs-trading per pereche simboluri.

  Parametrii optimizati:
    - entry_z     : z-score threshold pentru intrare in pozitie
    - exit_z      : z-score threshold pentru iesire din pozitie
    - stop_z      : z-score stop-loss
    - lookback    : fereastra rolling pentru spread mean/std

  Functii obiectiv suportate:
    - sharpe      : Sharpe ratio anualizat (DEFAULT, recomandat)
    - calmar      : Calmar ratio (PnL / MaxDD)
    - pnl         : PnL total brut
    - profit_factor: gross_profit / gross_loss
    - sortino     : Sortino ratio (doar downside deviation)

  Anti-overfit walk-forward:
    Datele se impart in in_sample (70%) + out_of_sample (30%).
    Optimizarea se face pe in_sample; scorul final e pe out_of_sample.
    Doar parametrii cu WFO_score > wfo_min_score trec de filtru.

  Output:
    - Dict {symbol_pair: OptimalParams}
    - CSV: backtest/reports/grid_PAIR_YYYYMMDD.csv
    - HTML heatmap: backtest/reports/heatmap_PAIR_YYYYMMDD.html
    - Auto-update: config/pairs/PAIR.json cu parametrii optimi

Usage::

    optimizer = ParamGridOptimizer.from_engine(
        engine=backtest_engine,
        pairs=["BTCUSDT-ETHUSDT", "SOLUSDT-AVAXUSDT"],
    )
    results = await optimizer.run_all()
    for pair, params in results.items():
        print(f"{pair}: entry_z={params.entry_z:.2f} "
              f"exit_z={params.exit_z:.2f} sharpe={params.sharpe:.3f}")
"""
from __future__ import annotations

import asyncio
import csv
import itertools
import json
import math
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GridParams:
    """Un set de parametrii candidat din grid."""
    entry_z: float
    exit_z: float
    stop_z: float
    lookback: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def label(self) -> str:
        return (
            f"ez{self.entry_z:.1f}_xz{self.exit_z:.1f}"
            f"_sz{self.stop_z:.1f}_lb{self.lookback}"
        )


@dataclass
class GridResult:
    """Rezultatul evaluarii unui set de parametrii pe un interval."""
    params: GridParams
    sharpe: float
    calmar: float
    pnl_usdt: float
    pnl_pct: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    sortino: float
    trades: int
    is_out_of_sample: bool = False

    @property
    def objective(self) -> Dict[str, float]:
        return {
            "sharpe": self.sharpe,
            "calmar": self.calmar,
            "pnl": self.pnl_usdt,
            "profit_factor": self.profit_factor,
            "sortino": self.sortino,
        }


@dataclass
class OptimalParams:
    """Parametrii optimi pentru o pereche dupa grid search + WFO."""
    pair: str
    entry_z: float
    exit_z: float
    stop_z: float
    lookback: int
    # Scoruri in-sample
    is_sharpe: float
    is_calmar: float
    is_pnl_pct: float
    # Scoruri out-of-sample (anti-overfit)
    oos_sharpe: float
    oos_calmar: float
    oos_pnl_pct: float
    wfo_score: float          # oos_sharpe / is_sharpe (ideally > 0.5)
    objective_used: str
    optimized_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    all_results: List[GridResult] = field(default_factory=list, repr=False)

    def passed_wfo(self, min_score: float = 0.5) -> bool:
        """True daca parametrii rezista la walk-forward test."""
        return self.wfo_score >= min_score and self.oos_sharpe > 0

    def to_config_dict(self) -> Dict[str, Any]:
        """Format pentru config/pairs/PAIR.json."""
        return {
            "entry_z": self.entry_z,
            "exit_z": self.exit_z,
            "stop_z": self.stop_z,
            "lookback": self.lookback,
            "_optimized_at": self.optimized_at,
            "_oos_sharpe": round(self.oos_sharpe, 4),
            "_wfo_score": round(self.wfo_score, 4),
        }


# ---------------------------------------------------------------------------
# Grid definition
# ---------------------------------------------------------------------------

@dataclass
class GridSpace:
    """
    Defineste spatiul de cautare pentru grid search.
    Valorile implicite sunt calibrate pe perechi crypto cu volatilitate medie.
    """
    entry_z_values: List[float] = field(
        default_factory=lambda: [1.0, 1.5, 2.0, 2.5, 3.0]
    )
    exit_z_values: List[float] = field(
        default_factory=lambda: [0.0, 0.25, 0.5, 0.75, 1.0]
    )
    stop_z_values: List[float] = field(
        default_factory=lambda: [3.0, 4.0, 5.0]
    )
    lookback_values: List[int] = field(
        default_factory=lambda: [20, 30, 45, 60]
    )

    def all_combinations(self) -> List[GridParams]:
        combos = list(itertools.product(
            self.entry_z_values,
            self.exit_z_values,
            self.stop_z_values,
            self.lookback_values,
        ))
        valid = [
            GridParams(entry_z=ez, exit_z=xz, stop_z=sz, lookback=lb)
            for ez, xz, sz, lb in combos
            if xz < ez < sz   # exit < entry < stop (regula de baza)
        ]
        logger.info(
            "[GridOptimizer] GridSpace: {} combinatii valide din {} totale",
            len(valid), len(combos),
        )
        return valid

    @classmethod
    def fine(cls, entry_z_center: float = 2.0) -> "GridSpace":
        """Grid fin in jurul unui entry_z cunoscut."""
        ez = entry_z_center
        return cls(
            entry_z_values=[ez - 0.3, ez - 0.15, ez, ez + 0.15, ez + 0.3],
            exit_z_values=[0.0, 0.1, 0.25, 0.4, 0.5],
            stop_z_values=[ez + 1.0, ez + 1.5, ez + 2.0],
            lookback_values=[25, 30, 35, 40],
        )

    @classmethod
    def coarse(cls) -> "GridSpace":
        """Grid larg pentru explorare initiala."""
        return cls(
            entry_z_values=[1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
            exit_z_values=[0.0, 0.5, 1.0],
            stop_z_values=[3.5, 4.5, 5.5],
            lookback_values=[20, 40, 60],
        )


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------

class ParamGridOptimizer:
    """
    Grid search paralel cu asyncio pe parametrii pairs-trading.
    Foloseste backtest engine existent (backtest/engine.py).
    """

    def __init__(
        self,
        engine,                          # backtest.engine.BacktestEngine
        pairs: List[str],
        grid: Optional[GridSpace] = None,
        objective: str = "sharpe",       # sharpe | calmar | pnl | profit_factor | sortino
        in_sample_pct: float = 0.70,     # 70% in-sample, 30% OOS
        wfo_min_score: float = 0.50,     # minim oos/is ratio
        max_concurrent: int = 8,         # thread pool size
        reports_dir: str = "backtest/reports",
        config_dir: str = "config/pairs",
        notifier_bus=None,
    ) -> None:
        self._engine = engine
        self._pairs = pairs
        self._grid = grid or GridSpace()
        self._objective = objective
        self._is_pct = in_sample_pct
        self._wfo_min = wfo_min_score
        self._max_concurrent = max_concurrent
        self._reports_dir = Path(reports_dir)
        self._config_dir = Path(config_dir)
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._bus = notifier_bus
        self._semaphore = asyncio.Semaphore(max_concurrent)

    @classmethod
    def from_engine(
        cls,
        engine,
        pairs: List[str],
        objective: str = "sharpe",
        grid: Optional[GridSpace] = None,
        notifier_bus=None,
    ) -> "ParamGridOptimizer":
        return cls(
            engine=engine,
            pairs=pairs,
            grid=grid,
            objective=objective,
            notifier_bus=notifier_bus,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_all(
        self,
        pairs: Optional[List[str]] = None,
        days: int = 180,
    ) -> Dict[str, OptimalParams]:
        """
        Ruleaza grid search pentru toate perechile in paralel.
        Returneaza {pair: OptimalParams}.
        """
        target_pairs = pairs or self._pairs
        logger.info(
            "[GridOptimizer] START: {} perechi | obiectiv={} | days={}",
            len(target_pairs), self._objective, days,
        )
        await self._alert(
            f"\U0001f50d *Grid Optimizer pornit*\n"
            f"  Perechi: {len(target_pairs)} | Obiectiv: `{self._objective}`\n"
            f"  Combinatii: `{len(self._grid.all_combinations())}` per pereche\n"
            f"  WFO split: `{int(self._is_pct*100)}% IS / "
            f"{int((1-self._is_pct)*100)}% OOS`"
        )

        tasks = [
            self._optimize_pair(pair, days=days)
            for pair in target_pairs
        ]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        results: Dict[str, OptimalParams] = {}
        for pair, res in zip(target_pairs, results_list):
            if isinstance(res, Exception):
                logger.error(
                    "[GridOptimizer] {} failed: {}", pair, res
                )
            else:
                results[pair] = res

        self._export_summary_csv(results)
        await self._notify_results(results)
        return results

    async def run_pair(
        self, pair: str, days: int = 180
    ) -> OptimalParams:
        """Ruleaza grid search pentru o singura pereche."""
        return await self._optimize_pair(pair, days=days)

    # ------------------------------------------------------------------
    # Core optimization
    # ------------------------------------------------------------------

    async def _optimize_pair(
        self, pair: str, days: int
    ) -> OptimalParams:
        """Grid search + WFO pentru o pereche."""
        combos = self._grid.all_combinations()
        logger.info(
            "[GridOptimizer] {} : {} combinatii x {} zile",
            pair, len(combos), days,
        )

        # Ruleaza toate combinatiile in paralel (limitat de semaphore)
        is_tasks = [
            self._eval_params(pair, p, days=days, is_split=True)
            for p in combos
        ]
        is_results: List[GridResult] = [
            r for r in await asyncio.gather(*is_tasks, return_exceptions=True)
            if isinstance(r, GridResult)
        ]

        if not is_results:
            logger.warning(
                "[GridOptimizer] {} : niciun rezultat valid", pair
            )
            return self._empty_optimal(pair)

        # Sorteaza dupa obiectiv (in-sample)
        best_is = max(
            is_results,
            key=lambda r: r.objective.get(self._objective, 0),
        )

        # Evalueaza out-of-sample cu parametrii best_is
        oos_result = await self._eval_params(
            pair, best_is.params, days=days, is_split=False
        )

        # WFO score
        is_score = best_is.objective.get(self._objective, 0)
        oos_score = oos_result.objective.get(self._objective, 0) if oos_result else 0
        wfo_score = (oos_score / is_score) if is_score > 0 else 0.0

        optimal = OptimalParams(
            pair=pair,
            entry_z=best_is.params.entry_z,
            exit_z=best_is.params.exit_z,
            stop_z=best_is.params.stop_z,
            lookback=best_is.params.lookback,
            is_sharpe=best_is.sharpe,
            is_calmar=best_is.calmar,
            is_pnl_pct=best_is.pnl_pct,
            oos_sharpe=oos_result.sharpe if oos_result else 0,
            oos_calmar=oos_result.calmar if oos_result else 0,
            oos_pnl_pct=oos_result.pnl_pct if oos_result else 0,
            wfo_score=wfo_score,
            objective_used=self._objective,
            all_results=is_results,
        )

        logger.info(
            "[GridOptimizer] {} BEST: entry_z={:.1f} exit_z={:.1f} "
            "stop_z={:.1f} lb={} | IS_sharpe={:.3f} OOS_sharpe={:.3f} "
            "WFO={:.2f} {}",
            pair,
            optimal.entry_z, optimal.exit_z,
            optimal.stop_z, optimal.lookback,
            optimal.is_sharpe, optimal.oos_sharpe,
            optimal.wfo_score,
            "\u2705 PASS" if optimal.passed_wfo(self._wfo_min) else "\u26a0\ufe0f OVERFIT",
        )

        # Export rapoarte
        self._export_pair_csv(pair, is_results)
        self._export_pair_heatmap(pair, is_results, optimal)

        # Auto-update config daca trece WFO
        if optimal.passed_wfo(self._wfo_min):
            self._write_pair_config(pair, optimal)

        return optimal

    async def _eval_params(
        self,
        pair: str,
        params: GridParams,
        days: int,
        is_split: bool = True,   # True = in-sample, False = out-of-sample
    ) -> Optional[GridResult]:
        """
        Evalueaza un set de parametrii pe backtest engine.
        Foloseste split IS/OOS pe baza is_split flag.
        """
        async with self._semaphore:
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    self._run_engine_sync,
                    pair, params, days, is_split,
                )
                return result
            except Exception as exc:
                logger.debug(
                    "[GridOptimizer] eval_params {} {}: {}",
                    pair, params.label(), exc,
                )
                return None

    def _run_engine_sync(
        self,
        pair: str,
        params: GridParams,
        days: int,
        is_split: bool,
    ) -> GridResult:
        """
        Ruleaza backtest engine sincron (in executor thread).
        Interfata cu backtest/engine.py existent.
        """
        # Calculeaza indexul de split
        total_days = days
        split_day = int(total_days * self._is_pct)

        # Construieste cfg pentru engine
        engine_cfg = {
            "symbol_a": pair.split("-")[0],
            "symbol_b": pair.split("-")[1] if "-" in pair else pair[:6],
            "entry_z": params.entry_z,
            "exit_z": params.exit_z,
            "stop_z": params.stop_z,
            "lookback": params.lookback,
            "days": split_day if is_split else (total_days - split_day),
            "offset_days": 0 if is_split else split_day,
        }

        # Apeleaza engine.run_backtest() sau engine_adapter
        try:
            metrics = self._engine.run_backtest(pair=pair, cfg=engine_cfg)
        except TypeError:
            # Fallback: unele engine-uri accepta kwargs direct
            metrics = self._engine.run_backtest(**engine_cfg)

        pnl = float(metrics.get("total_pnl", metrics.get("pnl", 0)))
        start_eq = float(metrics.get("start_equity", 1000))
        pnl_pct = pnl / start_eq if start_eq > 0 else 0
        sharpe = float(metrics.get("sharpe", 0))
        calmar = float(metrics.get("calmar", 0))
        max_dd = float(metrics.get("max_drawdown_pct", 0))
        win_rate = float(metrics.get("win_rate", 0))
        pf = float(metrics.get("profit_factor", 0))
        sortino = float(metrics.get("sortino", 0))
        trades = int(metrics.get("trades", metrics.get("total_trades", 0)))

        return GridResult(
            params=params,
            sharpe=sharpe,
            calmar=calmar,
            pnl_usdt=pnl,
            pnl_pct=pnl_pct,
            max_drawdown_pct=max_dd,
            win_rate=win_rate,
            profit_factor=pf,
            sortino=sortino,
            trades=trades,
            is_out_of_sample=not is_split,
        )

    # ------------------------------------------------------------------
    # Config write
    # ------------------------------------------------------------------

    def _write_pair_config(
        self, pair: str, optimal: OptimalParams
    ) -> None:
        """Scrie parametrii optimi in config/pairs/PAIR.json."""
        config_path = self._config_dir / f"{pair}.json"
        existing: Dict[str, Any] = {}
        if config_path.exists():
            try:
                with open(config_path) as f:
                    existing = json.load(f)
            except Exception:
                pass

        # Pastreaza campurile existente, suprascrie doar parametrii optimi
        existing.update(optimal.to_config_dict())
        with open(config_path, "w") as f:
            json.dump(existing, f, indent=2)
        logger.info(
            "[GridOptimizer] Config actualizat: {} -> entry_z={} exit_z={}",
            config_path, optimal.entry_z, optimal.exit_z,
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export_pair_csv(
        self, pair: str, results: List[GridResult]
    ) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = self._reports_dir / f"grid_{pair}_{ts}.csv"
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "entry_z", "exit_z", "stop_z", "lookback",
                "sharpe", "calmar", "pnl_usdt", "pnl_pct",
                "max_dd_pct", "win_rate", "profit_factor", "sortino", "trades",
            ])
            for r in sorted(results, key=lambda x: -x.sharpe):
                p = r.params
                writer.writerow([
                    p.entry_z, p.exit_z, p.stop_z, p.lookback,
                    round(r.sharpe, 4), round(r.calmar, 4),
                    round(r.pnl_usdt, 2), round(r.pnl_pct * 100, 4),
                    round(r.max_drawdown_pct * 100, 4),
                    round(r.win_rate * 100, 2),
                    round(r.profit_factor, 4),
                    round(r.sortino, 4),
                    r.trades,
                ])
        return str(path)

    def _export_pair_heatmap(
        self,
        pair: str,
        results: List[GridResult],
        optimal: OptimalParams,
    ) -> str:
        """Genereaza heatmap HTML entry_z vs exit_z colorat dupa Sharpe."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = self._reports_dir / f"heatmap_{pair}_{ts}.html"

        # Colecteaza valori unice
        entry_zs = sorted({r.params.entry_z for r in results})
        exit_zs = sorted({r.params.exit_z for r in results})

        # Pivot: (entry_z, exit_z) -> best sharpe
        pivot: Dict[Tuple[float, float], float] = {}
        for r in results:
            key = (r.params.entry_z, r.params.exit_z)
            pivot[key] = max(pivot.get(key, -999), r.sharpe)

        all_sharpes = [v for v in pivot.values() if v > -999]
        min_s = min(all_sharpes) if all_sharpes else 0
        max_s = max(all_sharpes) if all_sharpes else 1
        rng_s = max_s - min_s or 1

        def color(s: float) -> str:
            t = max(0, min(1, (s - min_s) / rng_s))
            r = int(255 * (1 - t))
            g = int(200 * t)
            b = int(50 * (1 - t))
            return f"rgb({r},{g},{b})"

        # Build table
        header_cells = "".join(
            f"<th>ez={ez}</th>" for ez in entry_zs
        )
        rows_html = ""
        for xz in exit_zs:
            cells = ""
            for ez in entry_zs:
                s = pivot.get((ez, xz), None)
                if s is None:
                    cells += "<td style='background:#111'>-</td>"
                else:
                    is_best = (
                        abs(ez - optimal.entry_z) < 0.01
                        and abs(xz - optimal.exit_z) < 0.01
                    )
                    border = " border: 3px solid #fff;" if is_best else ""
                    cells += (
                        f"<td style='background:{color(s)};{border}"
                        f"color:#000;font-weight:600'>{s:.3f}</td>"
                    )
            rows_html += f"<tr><th>xz={xz}</th>{cells}</tr>"

        wfo_color = "#4ade80" if optimal.passed_wfo(self._wfo_min) else "#f87171"
        html = f"""<!DOCTYPE html>
<html><head><meta charset='UTF-8'>
<title>Heatmap {pair}</title>
<style>
  body{{background:#0f0f1a;color:#e0e0ff;
       font-family:Inter,system-ui,sans-serif;padding:32px}}
  table{{border-collapse:collapse;font-size:12px}}
  th,td{{padding:6px 10px;border:1px solid #1a1a2a;min-width:60px;text-align:center}}
  th{{background:#1a1a3e;color:#aaa}}
</style></head><body>
<h2>&#128200; Heatmap: {pair}</h2>
<p style='color:#888'>Obiectiv: <b>{self._objective}</b> |
Sharpe range: [{min_s:.3f}, {max_s:.3f}] |
Combinatie optima: <b style='color:#fff'>entry_z={optimal.entry_z} exit_z={optimal.exit_z}</b></p>
<p>IS Sharpe: <b>{optimal.is_sharpe:.3f}</b> |
OOS Sharpe: <b>{optimal.oos_sharpe:.3f}</b> |
WFO score: <b style='color:{wfo_color}'>{optimal.wfo_score:.2f}</b>
({'PASS &#9989;' if optimal.passed_wfo(self._wfo_min) else 'OVERFIT &#9888;'})</p>
<table>
  <thead><tr><th>xz \ ez</th>{header_cells}</tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<p style='color:#444;font-size:11px;margin-top:16px'>
  Culori: <span style='color:green'>verde=Sharpe ridicat</span> |
  <span style='color:red'>rosu=Sharpe negativ</span> |
  <span style='border:2px solid white;padding:1px 4px'>alb=optim ales</span>
</p>
</body></html>"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("[GridOptimizer] Heatmap exportat: {}", path)
        return str(path)

    def _export_summary_csv(self, results: Dict[str, OptimalParams]) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = self._reports_dir / f"grid_summary_{ts}.csv"
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "pair", "entry_z", "exit_z", "stop_z", "lookback",
                "is_sharpe", "oos_sharpe", "wfo_score", "passed_wfo",
                "oos_pnl_pct", "objective",
            ])
            for pair, opt in sorted(results.items(), key=lambda x: -x[1].oos_sharpe):
                writer.writerow([
                    pair, opt.entry_z, opt.exit_z, opt.stop_z, opt.lookback,
                    round(opt.is_sharpe, 4), round(opt.oos_sharpe, 4),
                    round(opt.wfo_score, 3),
                    opt.passed_wfo(self._wfo_min),
                    round(opt.oos_pnl_pct * 100, 4),
                    opt.objective_used,
                ])
        logger.info("[GridOptimizer] Summary CSV: {}", path)
        return str(path)

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    async def _notify_results(
        self, results: Dict[str, OptimalParams]
    ) -> None:
        if not self._bus or not results:
            return
        passed = [p for p, o in results.items() if o.passed_wfo(self._wfo_min)]
        failed = [p for p, o in results.items() if not o.passed_wfo(self._wfo_min)]
        lines = [f"\U0001f50d *Grid Optimizer Finalizat*\n"]
        for pair, opt in sorted(results.items(), key=lambda x: -x[1].oos_sharpe):
            icon = "\u2705" if opt.passed_wfo(self._wfo_min) else "\u26a0\ufe0f"
            lines.append(
                f"  {icon} `{pair}`: ez=`{opt.entry_z}` xz=`{opt.exit_z}` "
                f"OOS_sharpe=`{opt.oos_sharpe:.3f}` WFO=`{opt.wfo_score:.2f}`"
            )
        lines.append(
            f"\nAprobate: `{len(passed)}` | Overfit: `{len(failed)}`"
        )
        await self._alert("\n".join(lines))

    async def _alert(self, msg: str) -> None:
        if not self._bus:
            return
        try:
            await self._bus.send_alert(msg, level="info")
        except Exception:
            pass

    def _empty_optimal(self, pair: str) -> OptimalParams:
        return OptimalParams(
            pair=pair, entry_z=2.0, exit_z=0.5, stop_z=4.0, lookback=30,
            is_sharpe=0, is_calmar=0, is_pnl_pct=0,
            oos_sharpe=0, oos_calmar=0, oos_pnl_pct=0,
            wfo_score=0, objective_used=self._objective,
        )
