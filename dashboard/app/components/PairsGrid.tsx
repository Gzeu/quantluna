'use client';
/**
 * QuantLuna Dashboard — Pairs Grid
 * Sprint 30
 *
 * Tabel perechi active: status, PnL, Sharpe, alocare, correlatie.
 * Auto-refresh la 5s via polling.
 */
import { useEffect, useState } from 'react';
import { getPairsStatus, type PairStatus } from '../lib/api';
import clsx from 'clsx';

const STATUS_COLOR = {
  active:  'bg-success/20 text-success',
  halted:  'bg-danger/20 text-danger',
  idle:    'bg-slate-700 text-slate-400',
};

export function PairsGrid() {
  const [pairs, setPairs]     = useState<PairStatus[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetch = async () => {
      try {
        const data = await getPairsStatus();
        setPairs(data);
      } catch { /* API down */ }
      finally { setLoading(false); }
    };
    fetch();
    const id = setInterval(fetch, 5000);
    return () => clearInterval(id);
  }, []);

  if (loading) return <div className="text-slate-500 text-sm">Se incarca perechile...</div>;

  return (
    <div className="bg-card rounded-xl border border-border overflow-hidden">
      <div className="px-5 py-3 border-b border-border">
        <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Perechi Active</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-slate-400 text-xs uppercase border-b border-border">
              <th className="px-4 py-2 text-left">Pereche</th>
              <th className="px-4 py-2 text-left">Status</th>
              <th className="px-4 py-2 text-right">PnL (USDT)</th>
              <th className="px-4 py-2 text-right">Sharpe</th>
              <th className="px-4 py-2 text-right">Alocare</th>
              <th className="px-4 py-2 text-right">Corelatie</th>
              <th className="px-4 py-2 text-right">Trades</th>
            </tr>
          </thead>
          <tbody>
            {pairs.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-6 text-center text-slate-500">Nicio pereche activa</td></tr>
            )}
            {pairs.map((p) => (
              <tr key={p.pair} className="border-b border-border/50 hover:bg-slate-800/50 transition-colors">
                <td className="px-4 py-2.5 font-mono text-white font-medium">{p.pair}</td>
                <td className="px-4 py-2.5">
                  <span className={clsx('px-2 py-0.5 rounded text-xs font-medium', STATUS_COLOR[p.status])}>
                    {p.status.toUpperCase()}
                  </span>
                </td>
                <td className={clsx('px-4 py-2.5 text-right font-mono', p.pnl_usdt >= 0 ? 'text-success' : 'text-danger')}>
                  {p.pnl_usdt >= 0 ? '+' : ''}{p.pnl_usdt.toFixed(2)}
                </td>
                <td className={clsx('px-4 py-2.5 text-right font-mono',
                  p.sharpe >= 1 ? 'text-success' : p.sharpe >= 0 ? 'text-warning' : 'text-danger'
                )}>
                  {p.sharpe.toFixed(3)}
                </td>
                <td className="px-4 py-2.5 text-right text-slate-300 font-mono">
                  ${p.alloc_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </td>
                <td className="px-4 py-2.5 text-right font-mono">
                  {p.correlation !== null ? (
                    <span className={clsx(Math.abs(p.correlation) > 0.80 ? 'text-danger' : 'text-slate-300')}>
                      {p.correlation.toFixed(3)}
                    </span>
                  ) : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-2.5 text-right text-slate-400">{p.n_trades}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
