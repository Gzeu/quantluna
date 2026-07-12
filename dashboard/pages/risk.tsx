/**
 * pages/risk.tsx
 * Pagina /risk — metrici de risc complete.
 * Polling /risk/dashboard la 5s.
 */
'use client';
import React from 'react';
import NavBar      from '../components/NavBar';
import StatsBar    from '../components/StatsBar';
import MetricsBadge from '../components/MetricsBadge';
import TradeBreakdown from '../components/TradeBreakdown';
import { useRiskMetrics } from '../hooks/useRiskMetrics';
import { Spinner }  from '../components/ui';

export default function RiskPage() {
  const { metrics, loading, error } = useRiskMetrics(5_000);

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-body)' }}>
      <NavBar />
      <StatsBar />
      <main className="px-6 py-6 max-w-[1600px] mx-auto">
        <h1 className="text-lg font-bold text-[var(--text-primary)] mb-6">
          🛡 Risk Dashboard
        </h1>

        {loading && !metrics && (
          <div className="flex justify-center py-20"><Spinner size="lg" /></div>
        )}
        {error && !metrics && (
          <div className="rounded-xl p-6 text-center text-sm text-red-400"
               style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
            ⚠ {error}
          </div>
        )}

        {metrics && (
          <div className="grid gap-5">
            {/* Top badge row */}
            <MetricsBadge />

            {/* Core risk grid */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                { label: 'Equity',      value: `$${metrics.equity_usd?.toFixed(2) ?? '—'}`, color: 'var(--cyan)' },
                { label: 'Daily PnL',   value: `$${metrics.daily_pnl?.toFixed(2) ?? '—'}`,
                  color: (metrics.daily_pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' },
                { label: 'Max DD',      value: `${((metrics.max_drawdown ?? 0) * 100).toFixed(2)}%`,
                  color: (metrics.max_drawdown ?? 0) > 0.05 ? 'var(--red)' : 'var(--text-primary)' },
                { label: 'Sharpe',      value: metrics.rolling_sharpe?.toFixed(3) ?? '—', color: 'var(--purple)' },
                { label: 'Win Rate',    value: `${((metrics.win_rate ?? 0) * 100).toFixed(1)}%`, color: 'var(--green)' },
                { label: 'Profit Factor', value: metrics.profit_factor?.toFixed(2) ?? '—', color: 'var(--cyan)' },
                { label: 'Avg Win',     value: `$${metrics.avg_win_usd?.toFixed(2) ?? '—'}`, color: 'var(--green)' },
                { label: 'Avg Loss',    value: `$${metrics.avg_loss_usd?.toFixed(2) ?? '—'}`, color: 'var(--red)' },
              ].map(({ label, value, color }) => (
                <div key={label}
                  className="rounded-xl p-4"
                  style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}
                >
                  <div className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider mb-1">
                    {label}
                  </div>
                  <div className="text-xl font-bold mono" style={{ color }}>
                    {value}
                  </div>
                </div>
              ))}
            </div>

            {/* Streak */}
            <div className="rounded-xl p-5"
                 style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
              <div className="text-xs text-[var(--text-muted)] mb-3 uppercase tracking-wider">
                Consecutive Streak
              </div>
              <div className="flex gap-8">
                <div>
                  <span className="text-[10px] text-[var(--text-muted)]">Current</span>
                  <div className={`text-2xl font-bold mono ${
                    (metrics.current_streak ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
                  }`}>
                    {(metrics.current_streak ?? 0) >= 0 ? '+' : ''}{metrics.current_streak ?? 0}
                  </div>
                </div>
                <div>
                  <span className="text-[10px] text-[var(--text-muted)]">Max Wins</span>
                  <div className="text-2xl font-bold mono text-green-400">
                    {metrics.max_consecutive_wins ?? 0}
                  </div>
                </div>
                <div>
                  <span className="text-[10px] text-[var(--text-muted)]">Max Losses</span>
                  <div className="text-2xl font-bold mono text-red-400">
                    {metrics.max_consecutive_losses ?? 0}
                  </div>
                </div>
                <div>
                  <span className="text-[10px] text-[var(--text-muted)]">Total Trades</span>
                  <div className="text-2xl font-bold mono text-[var(--text-primary)]">
                    {metrics.total_trades ?? 0}
                  </div>
                </div>
              </div>
            </div>

            {/* Per-pair breakdown */}
            <TradeBreakdown />
          </div>
        )}
      </main>
    </div>
  );
}
