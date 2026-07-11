"""
backtest/auto_reoptimizer.py  -  QuantLuna Auto Reoptimizer v1.0

Sprint S40 (2026-07-12):
  Scheduler automat care ruleaza ParamGridOptimizer saptamanal si aplica
  parametrii noi in productie daca trec testul WFO.

  Logica completa:
    1. Se trezeste duminica la 02:00 UTC (configurabil)
    2. Detecteaza perechile active din config sau runner_cfg
    3. Ruleaza ParamGridOptimizer cu GridSpace.coarse() sau fine()
    4. Pentru fiecare pereche:
       a. Daca oos_sharpe > min_sharpe_threshold AND wfo_score >= wfo_min_score:
          - Aplica parametrii noi in config/pairs/PAIR.json
          - Notifica Telegram cu diff (vechi vs noi parametrii)
       b. Daca oos_sharpe deteriorat (< 0) sau wfo fail:
          - ALERTA Telegram: pereche posibil degradata
          - NU modifica parametrii
    5. Salveaza historicul in state/reoptimizer_history.json
    6. Raporteaza summary per pereche pe Telegram

  Configurare via env:
    REOPT_SCHEDULE_DAY    : 6 (Sunday=6, Monday=0, ... default 6)
    REOPT_SCHEDULE_HOUR   : 2 (default 2 = 02:00 UTC)
    REOPT_DAYS_LOOKBACK   : 180 (zile de date pentru optimizare)
    REOPT_OBJECTIVE       : sharpe (sharpe/calmar/pnl)
    REOPT_WFO_MIN_SCORE   : 0.5
    REOPT_MIN_SHARPE      : 0.5 (minim oos_sharpe pentru aprobare)
    REOPT_GRID_TYPE       : coarse/fine (default coarse)
    REOPT_DRY_RUN         : false (true = simuleaza fara a scrie config)

Usage::

    scheduler = AutoReoptimizer.from_env(
        engine=backtest_engine,
        pairs=["BTCUSDT-ETHUSDT", "SOLUSDT-AVAXUSDT"],
        notifier_bus=bus,
    )
    await scheduler.run_loop()  # blocheaza, ruleaza saptamanal

    # Sau trigger manual:
    await scheduler.run_now(force=True)
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class AutoReoptimizer:
    """
    Scheduler automat pentru re-optimizare saptamanala a parametrilor.
    """

    def __init__(
        self,
        engine,
        pairs: List[str],
        notifier_bus=None,
        schedule_weekday: int = 6,      # 0=Luni, 6=Duminica
        schedule_hour: int = 2,         # 02:00 UTC
        days_lookback: int = 180,
        objective: str = "sharpe",
        wfo_min_score: float = 0.50,
        min_sharpe_threshold: float = 0.50,
        grid_type: str = "coarse",      # "coarse" | "fine"
        dry_run: bool = False,
        history_path: str = "state/reoptimizer_history.json",
        config_dir: str = "config/pairs",
        reports_dir: str = "backtest/reports",
    ) -> None:
        self._engine = engine
        self._pairs = pairs
        self._bus = notifier_bus
        self._weekday = schedule_weekday
        self._hour = schedule_hour
        self._days = days_lookback
        self._objective = objective
        self._wfo_min = wfo_min_score
        self._min_sharpe = min_sharpe_threshold
        self._grid_type = grid_type
        self._dry_run = dry_run
        self._history_path = Path(history_path)
        self._config_dir = Path(config_dir)
        self._reports_dir = Path(reports_dir)
        self._running = False
        self._history: List[Dict[str, Any]] = self._load_history()

    @classmethod
    def from_env(
        cls,
        engine,
        pairs: List[str],
        notifier_bus=None,
    ) -> "AutoReoptimizer":
        return cls(
            engine=engine,
            pairs=pairs,
            notifier_bus=notifier_bus,
            schedule_weekday=int(os.getenv("REOPT_SCHEDULE_DAY", "6")),
            schedule_hour=int(os.getenv("REOPT_SCHEDULE_HOUR", "2")),
            days_lookback=int(os.getenv("REOPT_DAYS_LOOKBACK", "180")),
            objective=os.getenv("REOPT_OBJECTIVE", "sharpe"),
            wfo_min_score=float(os.getenv("REOPT_WFO_MIN_SCORE", "0.5")),
            min_sharpe_threshold=float(os.getenv("REOPT_MIN_SHARPE", "0.5")),
            grid_type=os.getenv("REOPT_GRID_TYPE", "coarse"),
            dry_run=os.getenv("REOPT_DRY_RUN", "false").lower() == "true",
        )

    # ------------------------------------------------------------------
    # Scheduler loop
    # ------------------------------------------------------------------

    async def run_loop(self) -> None:
        """
        Loop principal: asteapta urmatoarea zi programata si ruleaza.
        Blocheaza indefinit (rulat ca task asyncio).
        """
        self._running = True
        logger.info(
            "[AutoReoptimizer] Loop pornit | Schedule: weekday={} hour={}:00 UTC",
            self._weekday, self._hour,
        )
        await self._alert(
            f"\U0001f504 *AutoReoptimizer activ*\n"
            f"  Schedule: "
            f"{['Lun','Mar','Mie','Joi','Vin','Sam','Dum'][self._weekday]} "
            f"{self._hour:02d}:00 UTC\n"
            f"  Perechi: {len(self._pairs)} | "
            f"Obiectiv: `{self._objective}` | "
            f"Dry-run: `{self._dry_run}`"
        )

        while self._running:
            wait_s = self._seconds_until_next_run()
            next_run = (
                datetime.now(timezone.utc) + timedelta(seconds=wait_s)
            ).strftime("%Y-%m-%d %H:%M UTC")
            logger.info(
                "[AutoReoptimizer] Urmatoarea rulare: {} (in {:.1f} ore)",
                next_run, wait_s / 3600,
            )
            try:
                await asyncio.sleep(wait_s)
            except asyncio.CancelledError:
                logger.info("[AutoReoptimizer] Cancelled")
                return
            if self._running:
                await self.run_now()

    def stop(self) -> None:
        self._running = False

    async def run_now(self, force: bool = False) -> Dict[str, Any]:
        """
        Trigger manual sau automat: ruleaza re-optimizarea acum.
        """
        start_ts = datetime.now(timezone.utc).isoformat()
        logger.info(
            "[AutoReoptimizer] RUN START | pairs={} days={} grid={}",
            len(self._pairs), self._days, self._grid_type,
        )
        await self._alert(
            f"\U0001f504 *Reoptimizer pornit* ({'MANUAL' if force else 'SCHEDULE'})\n"
            f"  Perechi: {len(self._pairs)} | Grid: `{self._grid_type}` | "
            f"Dry-run: `{self._dry_run}`"
        )

        # Construieste grid
        from backtest.param_grid_optimizer import (
            ParamGridOptimizer, GridSpace, OptimalParams
        )
        grid = (
            GridSpace.fine() if self._grid_type == "fine"
            else GridSpace.coarse()
        )

        optimizer = ParamGridOptimizer(
            engine=self._engine,
            pairs=self._pairs,
            grid=grid,
            objective=self._objective,
            wfo_min_score=self._wfo_min,
            reports_dir=str(self._reports_dir),
            config_dir=str(self._config_dir),
            notifier_bus=None,  # notificam noi
        )

        results = await optimizer.run_all(days=self._days)

        # Evalueaza si aplica
        applied: List[str] = []
        degraded: List[str] = []
        overfit: List[str] = []
        unchanged: List[str] = []

        for pair, opt in results.items():
            old_params = self._load_pair_config(pair)

            if opt.oos_sharpe >= self._min_sharpe and opt.passed_wfo(self._wfo_min):
                # Aplica parametrii noi
                if not self._dry_run:
                    self._apply_params(pair, opt)
                applied.append(pair)
                await self._notify_param_change(pair, old_params, opt)
                logger.info(
                    "[AutoReoptimizer] APLICAT {} entry_z={} exit_z={} "
                    "oos_sharpe={:.3f}",
                    pair, opt.entry_z, opt.exit_z, opt.oos_sharpe,
                )
            elif opt.oos_sharpe < 0:
                # Pereche degradata - alerta severa
                degraded.append(pair)
                await self._alert(
                    f"\u26a0\ufe0f *Pereche DEGRADATA* `{pair}`\n"
                    f"  OOS Sharpe: `{opt.oos_sharpe:.3f}` (negativ!)\n"
                    f"  Parametrii actuali PASTRATI.\n"
                    f"  Considera dezactivarea perechii.",
                )
                logger.warning(
                    "[AutoReoptimizer] DEGRADAT {} oos_sharpe={:.3f}",
                    pair, opt.oos_sharpe,
                )
            elif not opt.passed_wfo(self._wfo_min):
                # Overfit - nu aplica
                overfit.append(pair)
                logger.warning(
                    "[AutoReoptimizer] OVERFIT {} wfo_score={:.3f} < {}",
                    pair, opt.wfo_score, self._wfo_min,
                )
            else:
                unchanged.append(pair)

        # Salveaza history
        entry = {
            "timestamp": start_ts,
            "pairs_count": len(self._pairs),
            "applied": applied,
            "degraded": degraded,
            "overfit": overfit,
            "unchanged": unchanged,
            "dry_run": self._dry_run,
            "results": {
                pair: opt.to_config_dict()
                for pair, opt in results.items()
            },
        }
        self._history.append(entry)
        self._save_history()

        # Summary Telegram
        await self._alert(
            f"\u2705 *Reoptimizer Finalizat*\n"
            f"  \u2705 Aplicat: `{len(applied)}`\n"
            f"  \u26a0\ufe0f Overfit (neaplicat): `{len(overfit)}`\n"
            f"  \u274c Degradate (alerta): `{len(degraded)}`\n"
            f"  \u2014 Nemodificat: `{len(unchanged)}`\n"
            + (f"  \U0001f6ab DRY-RUN (nicio modificare reala)" if self._dry_run else "")
        )

        return entry

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _seconds_until_next_run(self) -> float:
        """
        Calculeaza secunde pana la urmatoarea rulare programata.
        Weekday: 0=Luni, 6=Duminica.
        """
        now = datetime.now(timezone.utc)
        days_ahead = (self._weekday - now.weekday()) % 7
        if days_ahead == 0 and now.hour >= self._hour:
            days_ahead = 7  # urmatoarea saptamana
        target = now.replace(
            hour=self._hour, minute=0, second=0, microsecond=0
        ) + timedelta(days=days_ahead)
        delta = (target - now).total_seconds()
        return max(delta, 60)  # minim 60s

    def _load_pair_config(self, pair: str) -> Dict[str, Any]:
        path = self._config_dir / f"{pair}.json"
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _apply_params(
        self, pair: str, opt
    ) -> None:
        path = self._config_dir / f"{pair}.json"
        existing = self._load_pair_config(pair)
        existing.update(opt.to_config_dict())
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(existing, f, indent=2)

    def _load_history(self) -> List[Dict[str, Any]]:
        if self._history_path.exists():
            try:
                with open(self._history_path) as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save_history(self) -> None:
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._history_path, "w") as f:
            json.dump(self._history[-100:], f, indent=2)  # pastram ultimele 100

    async def _notify_param_change(
        self,
        pair: str,
        old: Dict[str, Any],
        opt,
    ) -> None:
        """Trimite diff vechi vs noi parametrii pe Telegram."""
        old_ez = old.get("entry_z", "?") 
        old_xz = old.get("exit_z", "?")
        old_sz = old.get("stop_z", "?")
        old_lb = old.get("lookback", "?")
        changed = (
            old_ez != opt.entry_z
            or old_xz != opt.exit_z
            or old_sz != opt.stop_z
            or old_lb != opt.lookback
        )
        if not changed:
            return
        await self._alert(
            f"\U0001f504 *Parametrii actualizati* `{pair}`\n"
            f"  entry\_z: `{old_ez}` \u2192 `{opt.entry_z}`\n"
            f"  exit\_z:  `{old_xz}` \u2192 `{opt.exit_z}`\n"
            f"  stop\_z:  `{old_sz}` \u2192 `{opt.stop_z}`\n"
            f"  lookback: `{old_lb}` \u2192 `{opt.lookback}`\n"
            f"  OOS Sharpe: `{opt.oos_sharpe:.3f}` | "
            f"WFO: `{opt.wfo_score:.2f}`"
            + ("\n  \U0001f6ab DRY-RUN" if self._dry_run else "")
        )

    async def _alert(self, msg: str) -> None:
        if not self._bus:
            return
        try:
            await self._bus.send_alert(msg, level="info")
        except Exception:
            pass
