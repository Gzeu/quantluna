/**
 * TradeBreakdown.tsx — S37 new component
 * Tabel per-pair: Wins, Losses, Win%, Total PnL, Avg PnL, Avg Win, Avg Loss, R:R.
 * Sortare pe orice coloană. Filter activ/inactiv.
 * Date din useQuantLunaStore.tradeStats.pair_breakdown (populat de useRiskMetrics).
 */
import React, { useState, useMemo } from 'react';
import { useQuantLunaStore }  from '../store/quantlunaStore';
import type { PairBreakdown } from '../types/dashboard';
import { Card }               from './ui/Card';
import { Spinner }            from './ui/Spinner';

type SortKey = keyof Pick<
  PairBreakdown,
  'pair' | 'wins' | 'losses' | 'win_rate' | 'total_pnl' | 'avg_pnl' | 'avg_win' | 'avg_loss'
>;

function usd(n: number, sign = false) {
  const s = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (!sign) return `$${s}`;
  return `${n >= 0 ? '+' : '-'}$${s}`;
}

function SortTh({
  label, col, current, dir, onClick, right = true,
}: {
  label: string; col: SortKey; current: SortKey;
  dir: 'asc' | 'desc'; onClick: (c: SortKey) => void;
  right?: boolean;
}) {
  const active = current === col;
  return (
    <th
      className={`py-2 pr-3 cursor-pointer select-none text-[9px] uppercase tracking-wider
                  transition-colors text-[var(--text-muted)] hover:text-[var(--text-primary)]
                  ${right ? 'text-right' : 'text-left'}`}
      onClick={() => onClick(col)}
    >
      {label}
      <span className="ml-0.5 text-[var(--text-disabled)]">
        {active ? (dir === 'desc' ? '↓' : '↑') : '↕'}
      </span>
    </th>
  );
}

export function TradeBreakdown() {
  const tradeStats = useQuantLunaStore(s => s.tradeStats);
  const [sortKey,  setSortKey]  = useState<SortKey>('total_pnl');
  const [sortDir,  setSortDir]  = useState<'asc' | 'desc'>('desc');
  const [filter,   setFilter]   = useState<'all' | 'active' | 'inactive'>('all');

  const handleSort = (col: SortKey) => {
    if (col === sortKey) setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    else { setSortKey(col); setSortDir('desc'); }
  };

  const rows = useMemo(() => {
    const base: PairBreakdown[] = tradeStats?.pair_breakdown ?? [];
    const filtered = filter === 'all'     ? base
                   : filter === 'active'  ? base.filter(r => r.active)
                   : base.filter(r => !r.active);
    return [...filtered].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      const cmp = typeof av === 'string'
        ? (av as string).localeCompare(bv as string)
        : (av as number) - (bv as number);
      return sortDir === 'desc' ? -cmp : cmp;
    });
  }, [tradeStats, sortKey, sortDir, filter]);

  const loading = tradeStats === null;

  return (
    <Card>
      <Card.Header>
        <div className="flex items-center gap-2">
          <Card.Title>Trade Breakdown</Card.Title>
          {tradeStats && (
            <span className="text-[10px] bg-[var(--bg-elevated)] text-[var(--text-muted)]
                             rounded-full px-2 py-0.5 border border-[var(--border)]">
              {tradeStats.total_trades} trades · {tradeStats.wins}W {tradeStats.losses}L
            </span>
          )}
          {loading && <Spinner size="sm" />}
        </div>
        {/* Filter */}
        <div className="flex rounded-lg overflow-hidden border border-[var(--border)] text-xs">
          {(['all', 'active', 'inactive'] as const).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-2.5 py-1 capitalize transition-colors ${
                filter === f
                  ? 'bg-[var(--purple-dim)] text-purple-200'
                  : 'bg-[var(--bg-elevated)] text-[var(--text-muted)] hover:bg-[var(--bg-card-hover)]'
              }`}
            >{f}</button>
          ))}
        </div>
      </Card.Header>

      {loading ? (
        <div className="space-y-2">
          {[1,2,3,4].map(i => <div key={i} className="skeleton h-8 rounded" />)}
        </div>
      ) : rows.length === 0 ? (
        <p className="text-[var(--text-muted)] text-sm py-6 text-center">
          Nicio pereche cu trade-uri înregistrate.
        </p>
      ) : (
        <div className="overflow-x-auto max-h-72 overflow-y-auto">
          <table className="ql-table w-full text-xs">
            <thead style={{ position: 'sticky', top: 0, background: 'var(--bg-card)' }}>
              <tr>
                <SortTh label="Pair"     col="pair"      current={sortKey} dir={sortDir} onClick={handleSort} right={false} />
                <SortTh label="W"        col="wins"      current={sortKey} dir={sortDir} onClick={handleSort} />
                <SortTh label="L"        col="losses"    current={sortKey} dir={sortDir} onClick={handleSort} />
                <SortTh label="Win%"     col="win_rate"  current={sortKey} dir={sortDir} onClick={handleSort} />
                <SortTh label="Total PnL" col="total_pnl" current={sortKey} dir={sortDir} onClick={handleSort} />
                <SortTh label="Avg/Trade" col="avg_pnl"  current={sortKey} dir={sortDir} onClick={handleSort} />
                <SortTh label="Avg Win"  col="avg_win"   current={sortKey} dir={sortDir} onClick={handleSort} />
                <SortTh label="Avg Loss" col="avg_loss"  current={sortKey} dir={sortDir} onClick={handleSort} />
                <th className="py-2 text-center text-[9px] uppercase tracking-wider
                               text-[var(--text-muted)]">R:R</th>
                <th className="py-2 text-center text-[9px] uppercase tracking-wider
                               text-[var(--text-muted)]">●</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const wrPct = r.win_rate * 100;
                const rr    = r.avg_loss > 0 ? r.avg_win / r.avg_loss : null;
                const pnlColor = r.total_pnl >= 0 ? 'text-green-400' : 'text-red-400';
                const wrColor  = wrPct >= 55 ? 'text-green-400' : wrPct >= 45 ? 'text-yellow-400' : 'text-red-400';
                const rrColor  = rr === null ? '' : rr >= 1.5 ? 'text-green-400' : rr >= 1 ? 'text-yellow-400' : 'text-red-400';
                return (
                  <tr key={r.pair} className="border-b border-[var(--border)] hover:bg-[var(--bg-elevated)]
                                              transition-colors">
                    <td className="py-2 pr-3 font-mono text-cyan-400 whitespace-nowrap">{r.pair}</td>
                    <td className="py-2 pr-3 text-right text-green-400 font-bold tabular">{r.wins}</td>
                    <td className="py-2 pr-3 text-right text-red-400 font-bold tabular">{r.losses}</td>
                    <td className={`py-2 pr-3 text-right font-semibold tabular ${wrColor}`}>
                      {wrPct.toFixed(1)}%
                    </td>
                    <td className={`py-2 pr-3 text-right tabular font-semibold ${pnlColor}`}>
                      {usd(r.total_pnl, true)}
                    </td>
                    <td className={`py-2 pr-3 text-right tabular ${
                      r.avg_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                    }`}>
                      {usd(r.avg_pnl, true)}
                    </td>
                    <td className="py-2 pr-3 text-right tabular text-green-400">
                      {r.avg_win > 0 ? usd(r.avg_win) : '—'}
                    </td>
                    <td className="py-2 pr-3 text-right tabular text-red-400">
                      {r.avg_loss > 0 ? usd(r.avg_loss) : '—'}
                    </td>
                    <td className={`py-2 text-center tabular font-semibold ${rrColor}`}>
                      {rr !== null ? rr.toFixed(2) : '—'}
                    </td>
                    <td className="py-2 text-center">
                      <span className={`inline-block w-2 h-2 rounded-full ${
                        r.active ? 'bg-green-400 shadow-[0_0_4px_#4ade80]' : 'bg-[var(--text-disabled)]'
                      }`} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
