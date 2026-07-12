/**
 * pages/backtest.tsx
 * Pagina /backtest — configurare si rezultate backtest.
 * Foloseste ql-input / ql-btn / ql-pill din design system.
 */
import React, { useState } from 'react';
import type { NextPage }  from 'next';
import Head               from 'next/head';
import NavBar             from '../components/NavBar';
import { StatsBar }       from '../components/StatsBar';
import { Spinner }        from '../components/ui';
import { useBacktest, BacktestParams } from '../hooks/useBacktest';

const DEFAULT_PARAMS: BacktestParams = {
  pair_y:       'BTCUSDT',
  pair_x:       'ETHUSDT',
  interval:     '60',
  entry_zscore: 2.0,
  exit_zscore:  0.5,
  warmup_bars:  60,
  initial_cap:  10_000,
  start_date:   '',
  end_date:     '',
};

function Field({
  label, value, type = 'text', onChange,
}: {
  label: string; value: string | number; type?: string;
  onChange: (v: string | number) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider font-semibold">
        {label}
      </label>
      <input
        type={type}
        value={value}
        step={type === 'number' ? 'any' : undefined}
        onChange={e => onChange(type === 'number' ? parseFloat(e.target.value) : e.target.value)}
        className="ql-input mono"
      />
    </div>
  );
}

const BacktestPage: NextPage = () => {
  const { result, loading, error, run, reset } = useBacktest();
  const [params, setParams] = useState<BacktestParams>(DEFAULT_PARAMS);

  const set = (k: keyof BacktestParams, v: string | number) =>
    setParams(p => ({ ...p, [k]: v }));

  const pct  = (v: number) => `${(v * 100).toFixed(2)}%`;
  const usd  = (v: number) => `$${v.toFixed(2)}`;
  const fmt2 = (v: number) => v.toFixed(2);

  return (
    <>
      <Head><title>Backtest — QuantLuna</title></Head>
      <NavBar />
      <StatsBar />
      <main
        className="animate-fade-in"
        style={{
          background: 'var(--bg-body)',
          minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
          padding: '16px 20px 40px',
          maxWidth: 1400,
          margin: '0 auto',
          width: '100%',
          display: 'flex',
          flexDirection: 'column',
          gap: 20,
        }}
      >
        <div className="flex items-center gap-3">
          <span className="text-2xl">🔭</span>
          <h1 className="text-lg font-bold text-[var(--text-primary)]">Backtest</h1>
          {result.status === 'running' && (
            <span className="ql-pill ql-pill-cyan animate-pulse">
              <Spinner size="sm" /> Running
            </span>
          )}
          {result.status === 'done' && (
            <span className="ql-pill ql-pill-green">✔ Done</span>
          )}
        </div>

        {/* Config panel */}
        <div className="ql-card p-6">
          <div className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider font-semibold mb-4">
            Parametri
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-5">
            <Field label="Pair Y"        value={params.pair_y}       onChange={v => set('pair_y', v)} />
            <Field label="Pair X"        value={params.pair_x}       onChange={v => set('pair_x', v)} />
            <Field label="Interval (min)" value={params.interval}    type="number" onChange={v => set('interval', v)} />
            <Field label="Entry Z-Score" value={params.entry_zscore} type="number" onChange={v => set('entry_zscore', v)} />
            <Field label="Exit Z-Score"  value={params.exit_zscore}  type="number" onChange={v => set('exit_zscore', v)} />
            <Field label="Warmup Bars"   value={params.warmup_bars}  type="number" onChange={v => set('warmup_bars', v)} />
            <Field label="Capital $"     value={params.initial_cap}  type="number" onChange={v => set('initial_cap', v)} />
            <Field label="Start Date"    value={params.start_date}   onChange={v => set('start_date', v)} />
            <Field label="End Date"      value={params.end_date}     onChange={v => set('end_date', v)} />
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => run(params)}
              disabled={loading}
              className="ql-btn ql-btn-primary"
            >
              {loading ? <><Spinner size="sm" /> Running…</> : '▶ Run Backtest'}
            </button>
            {result.status !== 'idle' && (
              <button onClick={reset} className="ql-btn ql-btn-ghost">
                ↺ Reset
              </button>
            )}
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="ql-card ql-card-danger px-5 py-4 text-sm text-red-400">
            ⚠ {error}
          </div>
        )}

        {/* Results */}
        {result.status === 'done' && (
          <>
            {/* Summary cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                { label: 'Total PnL',     value: usd(result.total_pnl),
                  color: result.total_pnl >= 0 ? 'var(--green)' : 'var(--red)' },
                { label: 'Win Rate',      value: pct(result.win_rate),      color: 'var(--cyan)' },
                { label: 'Max Drawdown',  value: pct(result.max_drawdown),  color: 'var(--red)' },
                { label: 'Sharpe',        value: fmt2(result.sharpe),       color: 'var(--purple)' },
                { label: 'Profit Factor', value: fmt2(result.profit_factor), color: 'var(--cyan)' },
                { label: 'Total Trades',  value: `${result.total_trades}`,  color: 'var(--text-primary)' },
                { label: 'Wins',          value: `${result.wins}`,          color: 'var(--green)' },
                { label: 'Losses',        value: `${result.losses}`,        color: 'var(--red)' },
              ].map(({ label, value, color }) => (
                <div key={label} className="ql-card p-4">
                  <div className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider mb-1">
                    {label}
                  </div>
                  <div className="text-xl font-bold mono" style={{ color }}>{value}</div>
                </div>
              ))}
            </div>

            {/* Trades table */}
            {result.trades.length > 0 && (
              <div className="ql-card overflow-hidden">
                <div className="px-5 py-3 border-b border-[var(--border)] flex items-center gap-2">
                  <span className="text-xs font-bold text-[var(--text-muted)] uppercase tracking-wider">
                    Trade Log
                  </span>
                  <span className="ql-pill ql-pill-gray">{result.trades.length}</span>
                </div>
                <div className="overflow-x-auto" style={{ maxHeight: 420, overflowY: 'auto' }}>
                  <table className="ql-table">
                    <thead>
                      <tr>
                        {['Entry', 'Exit', 'Side', 'PnL (USD)', 'Fees', 'Result'].map(h => (
                          <th key={h}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.trades.map((t, i) => (
                        <tr key={i}>
                          <td className="mono">{new Date(t.entry_ts).toLocaleDateString('ro-RO')}</td>
                          <td className="mono">{new Date(t.exit_ts).toLocaleDateString('ro-RO')}</td>
                          <td>
                            <span className={t.side === 'long' ? 'ql-pill ql-pill-green' : 'ql-pill ql-pill-red'}>
                              {t.side.toUpperCase()}
                            </span>
                          </td>
                          <td className={`mono font-semibold ${
                            t.pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'
                          }`}>
                            {t.pnl_usd >= 0 ? '+' : ''}{t.pnl_usd.toFixed(2)}
                          </td>
                          <td className="mono text-[var(--text-muted)]">{t.fees_usd.toFixed(4)}</td>
                          <td>
                            <span className={t.is_win ? 'ql-pill ql-pill-green' : 'ql-pill ql-pill-red'}>
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
    </>
  );
};

export default BacktestPage;
