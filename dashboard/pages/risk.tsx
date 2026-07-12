/**
 * pages/risk.tsx
 * Pagina /risk — metrici de risc complete.
 * Polling /risk/dashboard la 5s.
 */
import type { NextPage }    from 'next';
import Head                 from 'next/head';
import NavBar               from '../components/NavBar';
import { StatsBar }         from '../components/StatsBar';
import { MetricsBadge }     from '../components/MetricsBadge';
import { TradeBreakdown }   from '../components/TradeBreakdown';
import { Spinner }          from '../components/ui';
import { useRiskMetrics }   from '../hooks/useRiskMetrics';

const RiskPage: NextPage = () => {
  const { metrics, loading, error } = useRiskMetrics(5_000);

  return (
    <>
      <Head><title>Risk — QuantLuna</title></Head>
      <NavBar />
      <StatsBar />
      <main
        className="animate-fade-in"
        style={{
          background: 'var(--bg-body)',
          minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
          padding: '16px 20px 40px',
          maxWidth: 1600,
          margin: '0 auto',
          width: '100%',
          display: 'flex',
          flexDirection: 'column',
          gap: 20,
        }}
      >
        <div className="flex items-center gap-3">
          <span className="text-2xl">🛡</span>
          <h1 className="text-lg font-bold text-[var(--text-primary)]">Risk Dashboard</h1>
          {loading && metrics && (
            <span className="ql-pill ql-pill-gray"><Spinner size="sm" /> Refreshing…</span>
          )}
        </div>

        {/* Loading state inicial */}
        {loading && !metrics && (
          <div className="flex justify-center py-20">
            <Spinner size="lg" />
          </div>
        )}

        {/* Error state */}
        {error && !metrics && (
          <div className="ql-card ql-card-danger px-6 py-5 text-sm text-red-400 text-center">
            ⚠ {error}
          </div>
        )}

        {metrics && (
          <>
            {/* Top badge row */}
            <MetricsBadge />

            {/* Core risk grid */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                { label: 'Equity',        value: `$${metrics.equity_usd?.toFixed(2) ?? '—'}`,
                  color: 'var(--cyan)' },
                { label: 'Daily PnL',     value: `$${metrics.daily_pnl?.toFixed(2) ?? '—'}`,
                  color: (metrics.daily_pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' },
                { label: 'Max DD',        value: `${((metrics.max_drawdown ?? 0) * 100).toFixed(2)}%`,
                  color: (metrics.max_drawdown ?? 0) > 0.05 ? 'var(--red)' : 'var(--text-primary)' },
                { label: 'Sharpe',        value: metrics.rolling_sharpe?.toFixed(3) ?? '—',
                  color: 'var(--purple)' },
                { label: 'Win Rate',      value: `${((metrics.win_rate ?? 0) * 100).toFixed(1)}%`,
                  color: 'var(--green)' },
                { label: 'Profit Factor', value: metrics.profit_factor?.toFixed(2) ?? '—',
                  color: 'var(--cyan)' },
                { label: 'Avg Win',       value: `$${metrics.avg_win_usd?.toFixed(2) ?? '—'}`,
                  color: 'var(--green)' },
                { label: 'Avg Loss',      value: `$${metrics.avg_loss_usd?.toFixed(2) ?? '—'}`,
                  color: 'var(--red)' },
              ].map(({ label, value, color }) => (
                <div key={label} className="ql-card p-4">
                  <div className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider mb-1">
                    {label}
                  </div>
                  <div className="text-xl font-bold mono" style={{ color }}>{value}</div>
                </div>
              ))}
            </div>

            {/* Streak card */}
            <div className="ql-card p-5">
              <div className="ql-card-header mb-4">Consecutive Streak</div>
              <div className="flex flex-wrap gap-8">
                {[
                  {
                    label: 'Current',
                    value: `${(metrics.current_streak ?? 0) >= 0 ? '+' : ''}${metrics.current_streak ?? 0}`,
                    cls: (metrics.current_streak ?? 0) >= 0 ? 'text-green-400' : 'text-red-400',
                  },
                  { label: 'Max Wins',   value: String(metrics.max_consecutive_wins   ?? 0), cls: 'text-green-400' },
                  { label: 'Max Losses', value: String(metrics.max_consecutive_losses ?? 0), cls: 'text-red-400' },
                  { label: 'Total Trades', value: String(metrics.total_trades ?? 0),         cls: 'text-[var(--text-primary)]' },
                ].map(({ label, value, cls }) => (
                  <div key={label}>
                    <div className="text-[10px] text-[var(--text-muted)] mb-1">{label}</div>
                    <div className={`text-2xl font-bold mono ${cls}`}>{value}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Per-pair breakdown */}
            <TradeBreakdown />
          </>
        )}
      </main>
    </>
  );
};

export default RiskPage;
