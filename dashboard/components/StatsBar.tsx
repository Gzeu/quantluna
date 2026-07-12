/**
 * StatsBar.tsx — S37 metrics expansion
 * +4 stat-uri noi: Win%, Trades, Profit Factor, Unrealized PnL.
 * Flash pe schimbare, data-tooltip, responsive.
 */
import React, { useEffect, useRef, useState } from 'react';
import { useQuantLunaStore } from '../store/quantlunaStore';
import { Kbd } from './ui/Kbd';

function useFlash(value: number | string | boolean | null | undefined) {
  const prev  = useRef(value);
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (prev.current !== undefined && prev.current !== value) {
      setFlash(true);
      const t = setTimeout(() => setFlash(false), 600);
      return () => clearTimeout(t);
    }
    prev.current = value;
  }, [value]);
  return flash;
}

function Stat({
  label, value, sub, color, flash, hideMobile, tooltip,
}: {
  label: string; value: string; sub?: string;
  color?: string; flash?: boolean;
  hideMobile?: boolean; tooltip?: string;
}) {
  return (
    <div
      data-tooltip={tooltip}
      className={`
        flex flex-col items-center justify-center
        px-4 h-[var(--stats-h)]
        border-r border-[var(--border-subtle)] last:border-r-0
        transition-colors duration-150
        ${flash ? 'bg-cyan-950/30' : ''}
        ${hideMobile ? 'hide-mobile' : ''}
      `}
    >
      <span className="text-[9px] uppercase tracking-widest text-[var(--text-muted)] leading-none mb-0.5">
        {label}
      </span>
      <span className={`text-[12px] font-bold tabular leading-none ${
        flash ? 'text-cyan-300' : (color ?? 'text-[var(--text-primary)]')
      }`}>
        {value}
      </span>
      {sub && (
        <span className="text-[9px] text-[var(--text-muted)] leading-none mt-0.5">{sub}</span>
      )}
    </div>
  );
}

export function StatsBar() {
  const pnl        = useQuantLunaStore(s => s.pnl);
  const regime     = useQuantLunaStore(s => s.regime);
  const pairs      = useQuantLunaStore(s => s.pairs);
  const tradeStats = useQuantLunaStore(s => s.tradeStats);

  const equity      = pnl?.total       ?? 0;
  const dailyPnl    = pnl?.dailyPnl    ?? 0;
  const dailyPct    = pnl?.dailyPct    ?? 0;
  const unrealized  = pnl?.unrealized  ?? 0;
  const latency     = regime?.latencyMs ?? null;
  const cbOpen      = regime?.cbOpen    ?? false;
  const regimeName  = regime?.regime    ?? null;
  const activePairs = pairs.filter(p => p.position !== 'FLAT').length;

  const winRate      = tradeStats ? tradeStats.win_rate * 100 : null;
  const totalTrades  = tradeStats?.total_trades ?? null;
  const profitFactor = tradeStats?.profit_factor ?? null;

  const flashEquity  = useFlash(Math.round(equity));
  const flashPnl     = useFlash(Math.round(dailyPnl * 100));
  const flashUnreal  = useFlash(Math.round(unrealized * 10));
  const flashCb      = useFlash(cbOpen);
  const flashLatency = useFlash(latency !== null ? Math.round(latency / 10) * 10 : null);

  const pnlColor = dailyPnl >= 0 ? 'text-green-400' : 'text-red-400';
  const uColor   = unrealized >= 0 ? 'text-teal-400' : 'text-orange-400';
  const latColor = latency === null ? 'text-[var(--text-muted)]'
                 : latency < 80    ? 'text-green-400'
                 : latency < 200   ? 'text-yellow-400'
                 : 'text-red-400';
  const regColor = regimeName === 'LOW'     ? 'text-green-400'
                 : regimeName === 'HIGH'    ? 'text-yellow-400'
                 : regimeName === 'EXTREME' ? 'text-red-400'
                 : 'text-[var(--text-secondary)]';
  const wrColor  = winRate === null    ? 'text-[var(--text-muted)]'
                 : winRate >= 55       ? 'text-green-400'
                 : winRate >= 45       ? 'text-yellow-400'
                 : 'text-red-400';
  const pfColor  = profitFactor === null  ? 'text-[var(--text-muted)]'
                 : profitFactor >= 1.5   ? 'text-green-400'
                 : profitFactor >= 1     ? 'text-yellow-400'
                 : 'text-red-400';

  return (
    <div
      className="w-full flex items-stretch overflow-x-auto"
      style={{
        background: 'var(--bg-surface)',
        borderBottom: '1px solid var(--border-subtle)',
        height: 'var(--stats-h)',
        minHeight: 'var(--stats-h)',
      }}
    >
      {/* ── Core ── */}
      <Stat label="Equity"
            value={`$${equity.toLocaleString('en-US', { maximumFractionDigits: 0 })}`}
            flash={flashEquity} tooltip="Total equity USD" />
      <Stat label="Daily P&L"
            value={`${dailyPnl >= 0 ? '+' : ''}$${Math.abs(dailyPnl).toFixed(2)}`}
            sub={`${dailyPct >= 0 ? '+' : ''}${dailyPct.toFixed(3)}%`}
            color={pnlColor} flash={flashPnl}
            tooltip="Profit/Loss de azi" />
      <Stat label="Unrealized" hideMobile
            value={`${unrealized >= 0 ? '+' : ''}$${Math.abs(unrealized).toFixed(2)}`}
            color={uColor} flash={flashUnreal}
            tooltip="PnL pozitii deschise (nerealizat)" />

      {/* ── Trade stats ── */}
      <Stat label="Win %" hideMobile
            value={winRate !== null ? `${winRate.toFixed(1)}%` : '—'}
            sub={totalTrades !== null
              ? `${tradeStats!.wins}W ${tradeStats!.losses}L`
              : undefined}
            color={wrColor}
            tooltip="Win rate total trade-uri inchise" />
      <Stat label="Trades" hideMobile
            value={totalTrades !== null ? String(totalTrades) : '—'}
            tooltip="Total trade-uri executate" />
      <Stat label="Prof. Factor" hideMobile
            value={profitFactor !== null && profitFactor > 0
              ? profitFactor.toFixed(2) : '—'}
            color={pfColor}
            tooltip="Profit Factor = gross profit / gross loss" />

      {/* ── System ── */}
      <Stat label="Pairs" hideMobile
            value={`${activePairs}/${pairs.length}`}
            sub="active"
            tooltip="Perechi cu pozitie deschisa" />
      <Stat label="Regime" hideMobile
            value={regimeName ?? '—'}
            color={regColor}
            tooltip="Regim volatilitate curent" />
      <Stat label="Latency" hideMobile
            value={latency !== null ? `${latency}ms` : '—'}
            color={latColor} flash={flashLatency}
            tooltip="Latenta WebSocket" />
      <Stat label="CB" hideMobile
            value={cbOpen ? 'OPEN' : 'OK'}
            color={cbOpen ? 'text-red-400' : 'text-green-400'}
            flash={flashCb}
            tooltip="Circuit Breaker status" />

      <div className="ml-auto flex items-center gap-1 px-3 hide-mobile">
        <span className="text-[var(--text-muted)] text-[9px] uppercase tracking-widest">shortcuts</span>
        <Kbd>?</Kbd>
      </div>
    </div>
  );
}
