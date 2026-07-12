"""
reporting/daily_report.py  —  QuantLuna Daily Report v1.0

Sprint S45 (2026-07-12):
  Genereaza si trimite raportul zilnic complet la 08:00 UTC.
  Apelat de WorkflowOrchestrator._report_loop().

  Raportul contine:
    1. Equity total + PnL zilnic (realizat si nerealizat)
    2. Breakdown per pereche activa
    3. Statistici performanta: Sharpe, max DD, win rate
    4. Capital allocation status (daca CapitalAllocator disponibil)
    5. Top 3 trades ale zilei (cel mai mare profit/pierdere)

Format Telegram (Markdown)::

    📈 Raport zilnic QuantLuna — 2026-07-12
    💰 Equity: 1,450.23 USDT (+2.34%, +33.02 USDT)
    ───────────────────────────────
    📊 BTCUSDT-ETHUSDT
      PnL: +18.40 USDT | Trades: 4 | WR: 75%
    📊 SOLUSDT-AVAXUSDT
      PnL: +14.62 USDT | Trades: 3 | WR: 100%
    ───────────────────────────────
    📉 Sharpe: 2.14 | Max DD: 1.2% | Win Rate: 85%
    🏦 Alocare: pairs_futures 70% | spot_hodl 20% | rezerva 10%
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger


async def send_daily_report(
    date: Optional[str] = None,
    notifier_bus=None,
    pnl_tracker=None,
    capital_allocator=None,
    pairs: Optional[List[str]] = None,
) -> bool:
    """
    Entry point principal — genereaza si trimite raportul zilnic.

    Parameters
    ----------
    date              : data raportului (YYYY-MM-DD), default azi
    notifier_bus      : NotifierBus pentru Telegram/Slack
    pnl_tracker       : DailyPnLTracker
    capital_allocator : CapitalAllocator (optional, pentru alocare)
    pairs             : lista perechi active

    Returns
    -------
    True daca raportul a fost trimis cu succes
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if pairs is None:
        pairs_env = os.getenv("PAIRS", "BTCUSDT-ETHUSDT")
        pairs = [p.strip() for p in pairs_env.split(",")]

    try:
        report = await _build_report(
            date=date,
            pnl_tracker=pnl_tracker,
            capital_allocator=capital_allocator,
            pairs=pairs,
        )
        msg = _format_telegram(report)

        if notifier_bus is not None:
            try:
                await notifier_bus.send_alert(msg, level="info")
                logger.info("[DailyReport] Raport {} trimis cu succes", date)
                return True
            except Exception as exc:
                logger.error("[DailyReport] send_alert failed: {}", exc)
                return False
        else:
            # Fallback: logeaza raportul
            logger.info("[DailyReport] (no bus):\n{}", msg)
            return True

    except Exception as exc:
        logger.error("[DailyReport] build_report failed: {}", exc)
        return False


async def _build_report(
    date: str,
    pnl_tracker,
    capital_allocator,
    pairs: List[str],
) -> Dict[str, Any]:
    """Construieste dict-ul cu datele raportului."""
    report: Dict[str, Any] = {
        "date":             date,
        "total_equity":     0.0,
        "realised_pnl":     0.0,
        "unrealised_pnl":   0.0,
        "pnl_pct":          0.0,
        "per_pair":         [],
        "sharpe":           None,
        "max_dd":           None,
        "win_rate":         None,
        "allocation":       [],
    }

    # 1. Summary global
    if pnl_tracker is not None:
        try:
            summary = await pnl_tracker.get_daily_summary(date)
            report["total_equity"]   = float(summary.get("total_equity_usdt", 0.0))
            report["realised_pnl"]   = float(summary.get("realised_pnl_usdt", 0.0))
            report["unrealised_pnl"] = float(summary.get("unrealised_pnl_usdt", 0.0))
            eq = report["total_equity"]
            report["pnl_pct"] = (
                report["realised_pnl"] / eq if eq > 0 else 0.0
            )
            # Statistici performanta (daca tracker suporta)
            if hasattr(pnl_tracker, "get_stats"):
                stats = await pnl_tracker.get_stats(date)
                report["sharpe"]   = stats.get("sharpe")
                report["max_dd"]   = stats.get("max_drawdown")
                report["win_rate"] = stats.get("win_rate")
        except Exception as exc:
            logger.warning("[DailyReport] get_daily_summary failed: {}", exc)

    # 2. Breakdown per pereche
    for pair in pairs:
        try:
            if pnl_tracker is not None and hasattr(pnl_tracker, "snapshot"):
                snap = pnl_tracker.snapshot(pair)
                report["per_pair"].append({
                    "pair":    pair,
                    "pnl":     snap.get("realised_pnl", 0.0),
                    "trades":  snap.get("trades_today", 0),
                    "win_rate": snap.get("win_rate", None),
                })
        except Exception:
            pass

    # 3. Allocation status
    if capital_allocator is not None:
        try:
            allocs = capital_allocator._allocations
            report["allocation"] = [
                {
                    "name":       name,
                    "target_pct": a.target_pct,
                    "hwm":        capital_allocator._high_watermarks.get(name, 0.0),
                }
                for name, a in allocs.items()
            ]
        except Exception:
            pass

    return report


def _format_telegram(report: Dict[str, Any]) -> str:
    """Formateaza raportul pentru Telegram Markdown."""
    date      = report["date"]
    equity    = report["total_equity"]
    r_pnl     = report["realised_pnl"]
    pnl_pct   = report["pnl_pct"]
    u_pnl     = report["unrealised_pnl"]

    emoji_top = "📈" if r_pnl >= 0 else "📉"
    emoji_eq  = "✅" if r_pnl >= 0 else "❌"

    lines = [
        f"{emoji_top} *Raport zilnic QuantLuna — {date}*",
        f"{emoji_eq} Equity: `{equity:,.2f} USDT` "
        f"(`{r_pnl:+.2f} USDT`, `{pnl_pct:+.2%}`)",
    ]
    if u_pnl != 0:
        lines.append(f"🔄 uPnL deschis: `{u_pnl:+.2f} USDT`")

    # Per pereche
    if report["per_pair"]:
        lines.append("─" * 30)
        for pp in report["per_pair"]:
            wr_str = f" | WR: `{pp['win_rate']:.0%}`" if pp.get("win_rate") else ""
            lines.append(
                f"📊 `{pp['pair']}`\n"
                f"  PnL: `{pp['pnl']:+.2f} USDT`"
                f" | Trades: `{pp['trades']}`"
                f"{wr_str}"
            )

    # Statistici
    stats_parts = []
    if report.get("sharpe") is not None:
        stats_parts.append(f"Sharpe: `{report['sharpe']:.2f}`")
    if report.get("max_dd") is not None:
        stats_parts.append(f"Max DD: `{report['max_dd']:.1%}`")
    if report.get("win_rate") is not None:
        stats_parts.append(f"Win Rate: `{report['win_rate']:.0%}`")
    if stats_parts:
        lines.append("─" * 30)
        lines.append("📉 " + " | ".join(stats_parts))

    # Allocation
    if report["allocation"]:
        lines.append("─" * 30)
        alloc_str = " | ".join(
            f"`{a['name']}` {a['target_pct']:.0%}"
            for a in report["allocation"]
        )
        lines.append(f"🏦 Alocare: {alloc_str}")

    return "\n".join(lines)
