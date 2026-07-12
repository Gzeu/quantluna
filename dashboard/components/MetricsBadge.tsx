/**
 * MetricsBadge.tsx — S37 metrics expansion
 * 10 card-uri: Equity, Daily P&L, Sharpe, Drawdown, Win Rate,
 * Profit Factor, Avg Win, Avg Loss, Consecutive, Unrealized.
 * Folosește useRiskMetrics hook (polling 5s).
 */
import React, { useRef, useState, useEffect } from 'react';
import { useRiskMetrics } from '../hooks/useRiskMetrics';
import { Spinner }        from './ui/Spinner';

function useFlash(val: number) {
  const prev  = useRef(val);
  const [on,  setOn]  = useState(false);
  useEffect(() => {
    if (Math.abs(val - prev.current) > 0.005) {
      setOn(true);
      const t = setTimeout(() => setOn(false), 750);
      return () => clearTimeout(t);
    }
    prev.current = val;
  }, [val]);
  return on;
}

function Bar({ pct, color }: { pct: number; color: string }) {
  return (
    <div className="w-full rounded-full overflow-hidden mt-2"
         style={{ height: 2, background: 'rgba(255,255,255,0.06)' }}>
      <div className={`h-full rounded-full transition-all duration-700 ${color}`}
           style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
    </div>
  );
}

function MetricSkeleton() {
  return (
    <div className="flex flex-wrap gap-3">
      {Array.from({ length: 10 }).map((_, i) => (
        <div key={i} className="skeleton rounded-xl" style={{ width: 130, height: 82 }} />
      ))}
    </div>
  );
}

interface CardProps {
  label: string; value: string; sub?: string;
  pct?: number; barColor?: string;
  accent: string; flash?: boolean; tooltip: string;
  warn?: boolean;
}

function MetricCard({ label, value, sub, pct, barColor, accent, flash, tooltip, warn }: CardProps) {
  return (
    <div
      data-tooltip={tooltip}
      className={`
        ql-card flex flex-col px-4 py-3 min-w-[128px] cursor-default
        border-t-2 ${accent} transition-all duration-300
        ${flash ? 'ring-1 ring-cyan-500/40 ring-offset-1 ring-offset-[var(--bg-base)]' : ''}
        ${warn  ? 'ring-1 ring-red-500/30' : ''}
      `}
    >
      <span className="text-[9px] uppercase tracking-widest text-[var(--text-muted)] mb-1">
        {label}
      </span>
      <span className={`text-xl font-bold tabular leading-none ${
        flash ? 'text-cyan-300' : warn ? 'text-red-400' : 'text-white'
      }`}>
        {value}
      </span>
      {sub && (
        <span className="text-[10px] text-[var(--text-muted)] mt-0.5 leading-tight">{sub}</span>
      )}
      {pct !== undefined && barColor && <Bar pct={pct} color={barColor} />}
    </div>
  );
}

const usd = (n: number) =>
  n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function MetricsBadge() {
  const { data: m, loading, error } = useRiskMetrics();
  const flashEq   = useFlash(m.equity_usd);
  const flashPnl  = useFlash(m.daily_pnl);

  if (error && !m.equity_usd) return (
    <div className="flex items-center gap-2 text-red-400 text-sm">
      <span>⚠️</span> Risk dashboard unavailable
    </div>
  );
  if (loading && !m.equity_usd) return <MetricSkeleton />;

  const ddPct    = m.drawdown_current * 100;
  const maxDdPct = m.max_drawdown     * 100;
  const wrPct    = m.win_rate         * 100;
  const expPct   = m.equity_usd > 0 ? (m.exposure_usd / m.equity_usd) * 100 : 0;
  const streakLabel = m.current_streak > 0
    ? `🔥 +${m.current_streak} wins`
    : m.current_streak < 0
      ? `❄️ ${m.current_streak} losses`
      : '—';
  const dailySign = m.daily_pnl >= 0 ? '+' : '';

  return (
    <div className="flex flex-wrap gap-3 stagger">

      {/* 1 — Equity */}
      <MetricCard
        label="Equity"
        value={`$${m.equity_usd.toLocaleString('en-US', { maximumFractionDigits: 0 })}`}
        accent="border-cyan-600"
        tooltip="Total equity USD — polling 5s"
        flash={flashEq}
        pct={100} barColor="bg-cyan-500"
      />

      {/* 2 — Daily P&L */}
      <MetricCard
        label="Daily P&L"
        value={`${dailySign}$${usd(Math.abs(m.daily_pnl))}`}
        sub={`${dailySign}${(m.daily_pct * 100).toFixed(3)}%`}
        accent={m.daily_pnl >= 0 ? 'border-green-600' : 'border-red-600'}
        tooltip="Profit/Loss de azi"
        flash={flashPnl}
        pct={Math.min(100, Math.abs(m.daily_pct * 100) * 5)}
        barColor={m.daily_pnl >= 0 ? 'bg-green-500' : 'bg-red-500'}
      />

      {/* 3 — Unrealized */}
      <MetricCard
        label="Unrealized"
        value={`${m.unrealized_pnl >= 0 ? '+' : ''}$${usd(m.unrealized_pnl)}`}
        accent={m.unrealized_pnl >= 0 ? 'border-teal-600' : 'border-orange-600'}
        tooltip="PnL pozitii deschise (nerealizat)"
        warn={m.unrealized_pnl < -500}
      />

      {/* 4 — Sharpe */}
      <MetricCard
        label="Sharpe 30d"
        value={m.rolling_sharpe.toFixed(2)}
        accent={m.rolling_sharpe > 1 ? 'border-green-600'
              : m.rolling_sharpe > 0 ? 'border-yellow-600'
              : 'border-red-600'}
        tooltip="Sharpe ratio rolling 30 zile; verde >1"
        pct={Math.min(100, m.rolling_sharpe * 50)}
        barColor={m.rolling_sharpe > 1 ? 'bg-green-500'
                : m.rolling_sharpe > 0 ? 'bg-yellow-500' : 'bg-red-500'}
      />

      {/* 5 — Win Rate */}
      <MetricCard
        label="Win Rate"
        value={`${wrPct.toFixed(1)}%`}
        sub={`${m.wins}W / ${m.losses}L / ${m.total_trades}T`}
        accent={wrPct >= 55 ? 'border-green-600' : wrPct >= 45 ? 'border-yellow-600' : 'border-red-600'}
        tooltip={`Win rate: ${m.wins} wins, ${m.losses} losses din ${m.total_trades} trade-uri`}
        pct={wrPct}
        barColor={wrPct >= 55 ? 'bg-green-500' : wrPct >= 45 ? 'bg-yellow-500' : 'bg-red-500'}
      />

      {/* 6 — Profit Factor */}
      <MetricCard
        label="Profit Factor"
        value={m.profit_factor > 0 ? m.profit_factor.toFixed(2) : '—'}
        sub={m.profit_factor >= 1.5 ? '✓ healthy' : m.profit_factor > 0 ? '⚠ low' : undefined}
        accent={m.profit_factor >= 1.5 ? 'border-green-600'
              : m.profit_factor >= 1   ? 'border-yellow-600'
              : 'border-red-600'}
        tooltip="Gross profit / gross loss. >1.5 healthy, <1 suboptimal"
        pct={Math.min(100, m.profit_factor * 33)}
        barColor={m.profit_factor >= 1.5 ? 'bg-green-500' : m.profit_factor >= 1 ? 'bg-yellow-500' : 'bg-red-500'}
        warn={m.profit_factor > 0 && m.profit_factor < 1}
      />

      {/* 7 — Avg Win */}
      <MetricCard
        label="Avg Win"
        value={m.avg_win_usd > 0 ? `+$${usd(m.avg_win_usd)}` : '—'}
        accent="border-green-700"
        tooltip="Câștig mediu per trade câștigat"
      />

      {/* 8 — Avg Loss */}
      <MetricCard
        label="Avg Loss"
        value={m.avg_loss_usd > 0 ? `-$${usd(m.avg_loss_usd)}` : '—'}
        sub={m.avg_win_usd > 0 && m.avg_loss_usd > 0
          ? `R:R ${(m.avg_win_usd / m.avg_loss_usd).toFixed(2)}`
          : undefined}
        accent="border-red-700"
        tooltip="Pierdere medie per trade pierdut. Sub-label: Risk:Reward ratio"
        warn={m.avg_loss_usd > m.avg_win_usd && m.avg_win_usd > 0}
      />

      {/* 9 — Max Drawdown */}
      <MetricCard
        label="Max Drawdown"
        value={`${maxDdPct.toFixed(1)}%`}
        sub={`current ${ddPct.toFixed(1)}%`}
        accent={maxDdPct > 20 ? 'border-red-600'
              : maxDdPct > 10 ? 'border-yellow-600'
              : 'border-green-600'}
        tooltip="Drawdown maxim istoric vs drawdown curent"
        pct={maxDdPct * 5}
        barColor={maxDdPct > 20 ? 'bg-red-500' : maxDdPct > 10 ? 'bg-yellow-500' : 'bg-green-500'}
        warn={maxDdPct > 20}
      />

      {/* 10 — Streak */}
      <MetricCard
        label="Streak"
        value={streakLabel}
        sub={`max ${m.max_consecutive_wins}W / ${m.max_consecutive_losses}L`}
        accent={m.current_streak > 2  ? 'border-green-600'
              : m.current_streak < -2 ? 'border-red-600'
              : 'border-gray-700'}
        tooltip={`Serie curentă: ${m.current_streak}. Max wins la rând: ${m.max_consecutive_wins}, max losses: ${m.max_consecutive_losses}`}
        warn={m.current_streak < -3}
      />

    </div>
  );
}
