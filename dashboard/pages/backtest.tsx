/**
 * pages/backtest.tsx
 * Pagina /backtest — configurare si rezultate backtest.
 */
'use client';
import React, { useState } from 'react';
import NavBar    from '../components/NavBar';
import StatsBar  from '../components/StatsBar';
import { Spinner } from '../components/ui';
import { useBacktest, BacktestParams } from '../hooks/useBacktest';

const DEFAULT_PARAMS: BacktestParams = {
  pair_y:       'BTCUSDT',
  pair_x:       'ETHUSDT',
  interval:     '60',
  entry_zscore: 2.0,
  exit_zscore:  0.5,
  warmup_bars:  60,
  initial_cap:  10000,
  start_date:   '',
  end_date:     '',
};

export default function BacktestPage() {
  const { result, loading, error, run, reset } = useBacktest();
  const [params, setParams] = useState<BacktestParams>(DEFAULT_PARAMS);

  const set = (k: keyof BacktestParams, v: string | number) =>
    setParams(p => ({ ...p, [k]: v }));

  const field = (label: string, key: keyof BacktestParams, type = 'text') => (
    <div className="flex flex-col gap-1">
      <label className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">
        {label}
      </label>
      <input
        type={type}
        value={params[key] as string | number}
        onChange={e => set(key, type === 'number' ? parseFloat(e.target.value) : e.target.value)}
        className="rounded-lg px-3 py-1.5 text-xs mono
                   bg-[var(--bg-body)] border border-[var(--border)]
                   text-[var(--text-primary)] focus:outline-none focus:border-[var(--purple)]"
      />
    </div>
  );

  const pct  = (v: number) => `${(v * 100).toFixed(2)}%`;
  const usd  = (v: number) => `$${v.toFixed(2)}`;
  const fmt2 = (v: number) => v.toFixed(2);

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-body)' }}>
      <NavBar />
      <StatsBar />
      <main className="px-6 py-6 max-w-[1400px] mx-auto">
        <h1 className="text-lg font-bold text-[var(--text-primary)] mb-6">🔬 Backtest</h1>

        {/* Config panel */}
        <div className="rounded-xl p-6 mb-6"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-5">
            {field('Pair Y',      'pair_y')}
            {field('Pair X',      'pair_x')}
            {field('Interval (min)', 'interval', 'number')}
            {field('Entry Z',     'entry_zscore', 'number')}
            {field('Exit Z',      'exit_zscore',  'number')}
            {field('Warmup Bars', 'warmup_bars',  'number')}
            {field('Capital $',   'initial_cap',  'number')}
            {field('Start Date',  'start_date')}
            {field('End Date',    'end_date')}
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => run(params)}
              disabled={loading}
              className="px-5 py-2 rounded-lg text-sm font-semibold
                         bg-[var(--purple)] hover:opacity-90 text-white
                         disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
            >
              {loading ? 'Running...' : '▶ Run Backtest'}
            </button>
            {result.status !== 'idle' && (
              <button onClick={reset}
                className="px-4 py-2 rounded-lg text-sm text-[var(--text-muted)]
                           border border-[var(--border)] hover:border-[var(--text-muted)]"
              >Reset</button>
            )}
          </div>
        </div>

        {loading && result.status === 'running' && (
          <div className="flex items-center gap-3 text-sm text-[var(--text-muted)] py-4">
            <Spinner size="sm" /> Running backtest...
          </div>
        )}
        {error && (
          <p className="text-sm text-red-400 mb-4">⚠ {error}</p>
        )}

        {result.status === 'done' && (
          <>
            {/* Summary cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              {[
                { label: 'Total PnL',     value: usd(result.total_pnl),
                  color: result.total_pnl >= 0 ? 'var(--green)' : 'var(--red)' },
                { label: 'Win Rate',      value: pct(result.win_rate),      color: 'var(--cyan)' },
                { label: 'Max Drawdown',  value: pct(result.max_drawdown),   color: 'var(--red)' },
                { label: 'Sharpe',        value: fmt2(result.sharpe),        color: 'var(--purple)' },
                { label: 'Profit Factor', value: fmt2(result.profit_factor), color: 'var(--cyan)' },
                { label: 'Total Trades',  value: `${result.total_trades}`,   color: 'var(--text-primary)' },
                { label: 'Wins',          value: `${result.wins}`,           color: 'var(--green)' },
                { label: 'Losses',        value: `${result.losses}`,         color: 'var(--red)' },
              ].map(({ label, value, color }) => (
                <div key={label}
                  className="rounded-xl p-4"
                  style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}
                >
                  <div className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider mb-1">{label}</div>
                  <div className="text-xl font-bold mono" style={{ color }}>{value}</div>
                </div>
              ))}
            </div>

            {/* Trades table */}
            {result.trades.length > 0 && (
              <div className="rounded-xl overflow-hidden"
                   style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                <div className="px-5 py-3 text-xs font-bold text-[var(--text-muted)] uppercase">
                  Trade Log ({result.trades.length})
                </div>
                <div className="overflow-x-auto max-h-[420px] overflow-y-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-[var(--border)]">
                        {['Entry', 'Exit', 'Side', 'PnL (USD)', 'Fees', 'Result'].map(h => (
                          <th key={h}
                            className="px-4 py-2 text-left text-[var(--text-muted)] font-normal"
                          >{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.trades.map((t, i) => (
                        <tr key={i}
                          className="border-b border-[var(--border)] hover:bg-white/[0.02]"
                        >
                          <td className="px-4 py-2 mono">{new Date(t.entry_ts).toLocaleDateString()}</td>
                          <td className="px-4 py-2 mono">{new Date(t.exit_ts).toLocaleDateString()}</td>
                          <td className="px-4 py-2">
                            <span className={t.side === 'long' ? 'text-green-400' : 'text-red-400'}>
                              {t.side.toUpperCase()}
                            </span>
                          </td>
                          <td className={`px-4 py-2 mono font-semibold ${
                            t.pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'
                          }`}>
                            {t.pnl_usd >= 0 ? '+' : ''}{t.pnl_usd.toFixed(2)}
                          </td>
                          <td className="px-4 py-2 mono text-[var(--text-muted)]">
                            {t.fees_usd.toFixed(4)}
                          </td>
                          <td className="px-4 py-2">
                            <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${
                              t.is_win
                                ? 'bg-green-900/40 text-green-400'
                                : 'bg-red-900/40 text-red-400'
                            }`}>
                              {t.is_win ? 'WIN' : 'LOSS'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
