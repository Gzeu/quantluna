/**
 * StrategyScores.tsx — S37 polish
 * Migrat la design system: ql-card via Card, ql-btn-ghost filter, CSS vars.
 * Sortare pe coloana, filter activ/inactiv, highlight best score.
 */
'use client';
import React, { useState, useMemo } from 'react';
import { useStrategyScores }  from '../hooks/useStrategyScores';
import type { PairScore }     from '../types/dashboard';
import { Card }               from './ui/Card';
import { Spinner }            from './ui/Spinner';

type SortKey = keyof Pick<PairScore, 'score' | 'sharpe' | 'win_rate' | 'total_trades' | 'pair'>;

function ScoreBadge({ v, best }: { v: number; best: boolean }) {
  const color = v >= 0.7 ? 'text-green-400' : v >= 0.4 ? 'text-yellow-400' : 'text-red-400';
  return (
    <span
      className={`font-bold tabular ${color} ${
        best ? 'underline underline-offset-2' : ''
      }`}
      title={best ? 'Best score in set' : undefined}
    >
      {v.toFixed(3)}
    </span>
  );
}

function SortTh({
  label, col, current, dir, onClick,
}: {
  label: string; col: SortKey;
  current: SortKey; dir: 'asc' | 'desc';
  onClick: (c: SortKey) => void;
}) {
  const active = current === col;
  return (
    <th
      className="text-right py-2 pr-4 cursor-pointer select-none
                 text-[var(--text-muted)] hover:text-[var(--text-primary)]
                 text-[10px] uppercase tracking-wider transition-colors"
      onClick={() => onClick(col)}
    >
      {label}
      <span className="ml-1 text-[var(--text-disabled)]">
        {active ? (dir === 'desc' ? '↓' : '↑') : '↕'}
      </span>
    </th>
  );
}

interface Props { fullPage?: boolean; }

export function StrategyScores({ fullPage }: Props) {
  const { scores, loading, error, lastUpdated } = useStrategyScores();
  const [sortKey, setSortKey] = useState<SortKey>('score');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [filter,  setFilter]  = useState<'all' | 'active' | 'inactive'>('all');

  const handleSort = (col: SortKey) => {
    if (col === sortKey) setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    else { setSortKey(col); setSortDir('desc'); }
  };

  const sorted = useMemo(() => {
    const base = filter === 'all'      ? scores
               : filter === 'active'   ? scores.filter(x => x.active)
               : scores.filter(x => !x.active);
    return [...base].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      const cmp = typeof av === 'string'
        ? (av as string).localeCompare(bv as string)
        : (av as number) - (bv as number);
      return sortDir === 'desc' ? -cmp : cmp;
    });
  }, [scores, sortKey, sortDir, filter]);

  const bestScore = useMemo(
    () => Math.max(...scores.map(s => s.score), 0),
    [scores]
  );

  return (
    <Card>
      <Card.Header>
        <div className="flex items-center gap-2">
          <Card.Title>Strategy Scores</Card.Title>
          {scores.length > 0 && (
            <span className="text-[10px] bg-[var(--bg-elevated)] text-[var(--text-muted)]
                             rounded-full px-2 py-0.5 border border-[var(--border)]">
              {scores.filter(s => s.active).length}/{scores.length} active
            </span>
          )}
          {loading && <Spinner size="sm" />}
        </div>
        <div className="flex items-center gap-2">
          {/* Filter group */}
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
              >
                {f}
              </button>
            ))}
          </div>
          {lastUpdated && (
            <span className="text-[10px] text-[var(--text-muted)] tabular mono">
              {new Date(lastUpdated).toLocaleTimeString()}
            </span>
          )}
        </div>
      </Card.Header>

      {error && <p className="text-red-400 text-sm mb-2">{error}</p>}

      {loading && sorted.length === 0 ? (
        <div className="space-y-2">
          {[1,2,3,4].map(i => <div key={i} className="skeleton h-8 rounded" />)}
        </div>
      ) : sorted.length === 0 ? (
        <p className="text-[var(--text-muted)] text-sm py-6 text-center">
          No optimizer results yet.
        </p>
      ) : (
        <div className={`overflow-x-auto ${
          fullPage ? '' : 'max-h-72 overflow-y-auto'
        }`}>
          <table className="ql-table">
            <thead style={{ position: 'sticky', top: 0, background: 'var(--bg-card)' }}>
              <tr>
                <th
                  className="text-left py-2 pr-4 cursor-pointer select-none
                             text-[var(--text-muted)] hover:text-[var(--text-primary)]
                             text-[10px] uppercase tracking-wider transition-colors"
                  onClick={() => handleSort('pair')}
                >
                  Pair
                  <span className="ml-1 text-[var(--text-disabled)]">
                    {sortKey === 'pair' ? (sortDir === 'desc' ? '↓' : '↑') : '↕'}
                  </span>
                </th>
                <th className="text-left py-2 pr-4 text-[var(--text-muted)]
                               text-[10px] uppercase tracking-wider">Strategy</th>
                <SortTh label="Score"  col="score"        current={sortKey} dir={sortDir} onClick={handleSort} />
                <SortTh label="Sharpe" col="sharpe"       current={sortKey} dir={sortDir} onClick={handleSort} />
                <SortTh label="Win%"   col="win_rate"     current={sortKey} dir={sortDir} onClick={handleSort} />
                <SortTh label="Trades" col="total_trades" current={sortKey} dir={sortDir} onClick={handleSort} />
                <th className="text-center py-2 text-[var(--text-muted)]
                               text-[10px] uppercase tracking-wider">Active</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(s => (
                <tr
                  key={s.pair}
                  className={s.score === bestScore && bestScore > 0
                    ? 'bg-[var(--bg-elevated)]'
                    : ''}
                >
                  <td className="py-2 pr-4 font-mono text-cyan-400">{s.pair}</td>
                  <td className="py-2 pr-4 text-[var(--text-secondary)] text-xs">{s.strategy}</td>
                  <td className="py-2 pr-4 text-right">
                    <ScoreBadge v={s.score} best={s.score === bestScore && bestScore > 0} />
                  </td>
                  <td className="py-2 pr-4 text-right text-[var(--text-secondary)] tabular">
                    {s.sharpe.toFixed(2)}
                  </td>
                  <td className="py-2 pr-4 text-right text-[var(--text-secondary)] tabular">
                    {(s.win_rate * 100).toFixed(1)}%
                  </td>
                  <td className="py-2 pr-4 text-right text-[var(--text-secondary)] tabular">
                    {s.total_trades}
                  </td>
                  <td className="py-2 text-center">
                    <span className={`inline-block w-2 h-2 rounded-full ${
                      s.active
                        ? 'bg-green-400 shadow-[0_0_4px_#4ade80]'
                        : 'bg-[var(--text-disabled)]'
                    }`} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
