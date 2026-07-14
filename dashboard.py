#!/usr/bin/env python3
"""
quantluna-dashboard — Live TUI dashboard for QuantLuna trading bot.

Reads the latest log file, polls the health endpoint, and displays
a real-time terminal dashboard.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
API_PORT = 8000
HEALTH_PORT = 8081


# ── helpers ──────────────────────────────────────────────────────────────────

def _pid_and_uptime() -> tuple[int | None, str]:
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "main.py.*--pair"], text=True, timeout=3
        ).strip()
        if not out:
            return None, "stopped"
        pid = int(out.splitlines()[0])
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().split()
            clktck = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
            utime = int(parts[13]) / clktck
            return pid, _format_duration(utime)
    except Exception:
        return None, "stopped"


def _format_duration(seconds: float) -> str:
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m:02d}m{s:02d}s"


def _tail_log(n: int = 30) -> list[str]:
    """Return the last n lines from the newest log file."""
    logs = sorted(LOG_DIR.glob("quantluna_*.log"), reverse=True)
    if not logs:
        return ["(no log files)"]
    try:
        with open(logs[0]) as f:
            lines = f.readlines()
        return [l.rstrip("\n") for l in lines[-n:]]
    except Exception:
        return ["(error reading log)"]


def _count_log_errors() -> int:
    logs = sorted(LOG_DIR.glob("quantluna_*.log"), reverse=True)
    if not logs:
        return 0
    try:
        with open(logs[0]) as f:
            text = f.read()
        return len(re.findall(r"ERROR|CRITICAL", text))
    except Exception:
        return 0


def _parse_signals(lines: list[str]) -> list[str]:
    """Extract SignalGen lines from log tail."""
    return [l for l in lines if "SignalGen" in l or "entry signal" in l or "exit signal" in l]


def _parse_ws_warnings(lines: list[str]) -> list[str]:
    return [l for l in lines if "WsWatchdog" in l or "dead feed" in l or "Stale" in l]


def _get_account_balance() -> dict:
    try:
        import urllib.request, json
        req = urllib.request.urlopen(f"http://localhost:{API_PORT}/risk/status", timeout=3)
        return json.loads(req.read())
    except Exception:
        return {"equity_usd": "N/A"}


def _get_dashboard() -> dict:
    try:
        import urllib.request, json
        req = urllib.request.urlopen(f"http://localhost:{API_PORT}/risk/dashboard", timeout=3)
        return json.loads(req.read())
    except Exception:
        return {}


# ── renderers ────────────────────────────────────────────────────────────────

from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.style import Style
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()


def make_header(pid: int | None, uptime: str) -> Panel:
    if pid:
        status = Text("● RUNNING", style="bold green")
    else:
        status = Text("● STOPPED", style="bold red")

    t = Table.grid(padding=(0, 2))
    t.add_row(
        Text("QuantLuna Bot", style="bold cyan"),
        status,
        Text(f"PID {pid or '—'}", style="dim"),
        Text(f"uptime {uptime}", style="dim"),
        Text(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), style="dim"),
    )
    return Panel(t, style="bold white", box=box.ROUNDED)


def make_account_panel(dash: dict, status: dict) -> Panel:
    balance = status.get("equity_usd", "N/A")
    equity = dash.get("equity_usd", balance)
    daily_pnl = dash.get("daily_pnl", 0)
    unrealized = dash.get("unrealized_pnl", 0)
    errors = _count_log_errors()

    table = Table.grid(padding=(0, 1))
    table.add_row(Text("Equity:", style="bold"), Text(f"${equity}", style="green"))
    table.add_row(Text("Daily PnL:"), Text(f"${daily_pnl:+.2f}" if isinstance(daily_pnl, (int, float)) else str(daily_pnl), style="green" if daily_pnl and daily_pnl >= 0 else "red"))
    table.add_row(Text("Unrealized:"), Text(f"${unrealized:+.2f}" if isinstance(unrealized, (int, float)) else str(unrealized), style="yellow"))
    table.add_row(Text("Errors today:"), Text(str(errors), style="red" if errors else "dim"))
    return Panel(table, title="📊 Account", border_style="green", box=box.ROUNDED)


def make_positions_panel(dash: dict) -> Panel:
    pairs = dash.get("pair_breakdown", [])
    if not pairs:
        return Panel(Text("No active positions", style="dim"), title="📦 Positions", border_style="cyan", box=box.ROUNDED)

    table = Table(box=box.SIMPLE, header_style="bold cyan")
    table.add_column("Pair")
    table.add_column("Trades")
    table.add_column("Win Rate")
    table.add_column("PnL")
    table.add_column("Exposure")
    for p in pairs[:5]:
        table.add_row(
            p.get("pair", "?"),
            str(p.get("trade_count", 0)),
            f"{p.get('win_rate', 0)*100:.1f}%",
            f"${p.get('total_pnl', 0):+.2f}",
            f"${p.get('exposure_usd', 0):.0f}",
        )
    # Add summary row
    table = Panel(table, title="📦 Positions", border_style="cyan", box=box.ROUNDED)

    # Add trade summary below
    summary = Table.grid(padding=(0, 2))
    summary.add_row(
        Text(f"Wins: {dash.get('wins', 0)}", style="green"),
        Text(f"Losses: {dash.get('losses', 0)}", style="red"),
        Text(f"Win Rate: {dash.get('win_rate', 0)*100:.1f}%"),
        Text(f"Avg Win: ${dash.get('avg_win_usd', 0):.2f}", style="green"),
        Text(f"Avg Loss: -${dash.get('avg_loss_usd', 0):.2f}", style="red"),
    )
    return Group(table, Panel(summary, border_style="dim", box=box.ROUNDED))


def make_signals_panel(log_tail: list[str]) -> Panel:
    signals = _parse_signals(log_tail)
    if not signals:
        return Panel(Text("No recent signals", style="dim"), title="📡 Signals", border_style="yellow", box=box.ROUNDED)
    content = Text()
    for s in signals[-8:]:
        # Color by signal type
        if "entry" in s.lower() or "BUY" in s or "SELL" in s:
            content.append(s[:100] + "\n", style="green")
        elif "EXIT" in s:
            content.append(s[:100] + "\n", style="yellow")
        else:
            content.append(s[:100] + "\n", style="dim")
    return Panel(content, title="📡 Signals (last 8)", border_style="yellow", box=box.ROUNDED)


def make_ws_panel(log_tail: list[str]) -> Panel:
    ws_lines = _parse_ws_warnings(log_tail)
    if not ws_lines:
        return Panel(Text("✅ Healthy", style="green"), title="🔌 WebSocket", border_style="blue", box=box.ROUNDED)
    content = Text()
    for w in ws_lines[-3:]:
        if "STALE" in w or "CRITICAL" in w or "dead" in w:
            content.append(w[:90] + "\n", style="red")
        else:
            content.append(w[:90] + "\n", style="yellow")
    return Panel(content, title="🔌 WebSocket", border_style="blue", box=box.ROUNDED)


def make_log_panel(log_tail: list[str]) -> Panel:
    content = Text()
    for line in log_tail[-15:]:
        level_match = re.search(r"(DEBUG|INFO|WARNING|ERROR|CRITICAL)", line)
        if level_match:
            level = level_match.group(1)
            if level == "ERROR":
                style = "red"
            elif level == "WARNING":
                style = "yellow"
            elif level == "DEBUG":
                style = "dim"
            else:
                style = "white"
        else:
            style = "white"
        content.append(line[:140] + "\n", style=style)
    return Panel(content, title="📋 Live Log", border_style="white", box=box.ROUNDED)


def make_runner_info(log_tail: list[str]) -> Panel:
    """Extract runner status info from logs."""
    info_lines = []
    for line in log_tail:
        if "first bar" in line:
            info_lines.append(line)
        if "Kalman" in line and "ready" in line.lower():
            info_lines.append(line)
        if "circuit" in line.lower():
            info_lines.append(line)
        if "ProfitGuard" in line and "registered" in line:
            info_lines.append(line[:80])

    content = Text()
    if not info_lines:
        content.append("(waiting for runner)", style="dim")
    else:
        for line in info_lines[-5:]:
            content.append(line[:100] + "\n", style="cyan")
    return Panel(content, title="⚙️ Runner Status", border_style="cyan", box=box.ROUNDED)


# ── main loop ────────────────────────────────────────────────────────────────

def build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=1),
    )
    layout["body"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=2),
    )
    layout["body"]["left"].split_column(
        Layout(name="account", size=8),
        Layout(name="positions", size=10),
        Layout(name="ws", size=6),
    )
    layout["body"]["right"].split_column(
        Layout(name="runner", size=8),
        Layout(name="signals", size=10),
        Layout(name="log", size=12),
    )
    return layout


def main():
    layout = build_layout()

    with Live(layout, refresh_per_second=2, screen=True):
        while True:
            try:
                pid, uptime = _pid_and_uptime()
                log_tail = _tail_log(40)
                dash = _get_dashboard()
                status = _get_account_balance()

                layout["header"].update(make_header(pid, uptime))
                layout["account"].update(make_account_panel(dash, status))
                layout["positions"].update(make_positions_panel(dash))
                layout["ws"].update(make_ws_panel(log_tail))
                layout["runner"].update(make_runner_info(log_tail))
                layout["signals"].update(make_signals_panel(log_tail))
                layout["log"].update(make_log_panel(log_tail))
                layout["footer"].update(
                    Panel(
                        Text("Ctrl+C to exit  |  QuantLuna v0.33.0", style="dim"),
                        box=box.ROUNDED, style="dim",
                    )
                )
                time.sleep(0.5)
            except KeyboardInterrupt:
                break
            except Exception:
                time.sleep(1)


if __name__ == "__main__":
    main()
