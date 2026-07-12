/**
 * StrategyScores.tsx — S37 improved
 * Sortare pe coloană (click header), filter activ/inactiv, highlight best score
 * Prop fullPage: afiseaza fără height limit pe alerte
 */
'use client';
import React, { useState, useMemo } from 'react';
import { useStrategyScores } from '../hooks/useStrategyScores';
import type { PairScore } from '../types/dashboard';

type SortKey = keyof Pick<PairScore, 'score' | 'sharpe' | 'win_rate' | 'total_trades' | 'pair'>;

function ScoreBadge({ v, best }: { v: number; best: boolean }) {
  const color = v >= 0.7 ? 'text-green-400' : v >= 0.4 ? 'text-yellow-400' : 'text-red-400';
  return (
    <span className={`font-bold ${color} ${best ? 'underline underline-offset-2' : ''}`}
          title={best ? 'Best score in set' : undefined}>
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
      className="text-right py-2 pr-4 cursor-pointer select-none hover:text-gray-200 transition-colors"
      onClick={() => onClick(col)}
    >
      {label}
      <span className="ml-1 text-gray-600">{active ? (dir === 'desc' ? '↓' : '↑') : '↕'}</span>
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
    let s = filter === 'all'      ? scores
          : filter === 'active'   ? scores.filter(x => x.active)
          : scores.filter(x => !x.active);
    return [...s].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      const cmp = typeof av === 'string'
        ? (av as string).localeCompare(bv as string)
        : (av as number) - (bv as number);
      return sortDir === 'desc' ? -cmp : cmp;
    });
  }, [scores, sortKey, sortDir, filter]);

  const bestScore = useMemo(() => Math.max(...scores.map(s => s.score), 0), [scores]);

  return (
    <div className="bg-gray-900 rounded-2xl p-5 w-full">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-2">
          <h2 className="text-white font-semibold text-lg">Strategy Scores</h2>
          {scores.length > 0 && (
            <span className="text-xs bg-gray-800 text-gray-400 rounded-full px-2 py-0.5">
              {scores.filter(s => s.active).length}/{scores.length} active
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* Filter */}
          <div className="flex rounded-lg overflow-hidden border border-gray-700 text-xs">
            {(['all', 'active', 'inactive'] as const).map(f => (
              <button key={f}
                onClick={() => setFilter(f)}
                className={`px-2.5 py-1 capitalize transition-colors ${
                  filter === f ? 'bg-indigo-900 text-indigo-200' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                }`}
              >{f}</button>
            ))}
          </div>
          {lastUpdated && (
            <span className="text-xs text-gray-600">
              {new Date(lastUpdated).toLocaleTimeString()}
            </span>
          )}
        </div>
      </div>

      {error && <p className="text-red-400 text-sm mb-2">{error}</p>}

      {loading && sorted.length === 0 ? (
        <p className="text-gray-500 text-sm">Loading scores…</p>
      ) : sorted.length === 0 ? (
        <p className="text-gray-500 text-sm">No optimizer results yet.</p>
      ) : (
        <div className={`overflow-x-auto ${fullPage ? '' : 'max-h-72 overflow-y-auto'}`}>
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-gray-900">
              <tr className="text-gray-400 border-b border-gray-800">
                <th
                  className="text-left py-2 pr-4 cursor-pointer select-none hover:text-gray-200"
                  onClick={() => handleSort('pair')}
                >
                  Pair <span className="text-gray-600">{sortKey==='pair'?(sortDir==='desc'?'↓':'↑'):'↕'}</span>
                </th>
                <th className="text-left py-2 pr-4 text-gray-400">Strategy</th>
                <SortTh label="Score"  col="score"       current={sortKey} dir={sortDir} onClick={handleSort} />
                <SortTh label="Sharpe" col="sharpe"      current={sortKey} dir={sortDir} onClick={handleSort} />
                <SortTh label="Win%"   col="win_rate"    current={sortKey} dir={sortDir} onClick={handleSort} />
                <SortTh label="Trades" col="total_trades" current={sortKey} dir={sortDir} onClick={handleSort} />
                <th className="text-center py-2">Active</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(s => (
                <tr key={s.pair}
                    className={`border-b border-gray-800 hover:bg-gray-800 transition-colors ${
                      s.score === bestScore && bestScore > 0 ? 'bg-gray-800/50' : ''
                    }`}>
                  <td className="py-2 pr-4 font-mono text-cyan-400">{s.pair}</td>
                  <td className="py-2 pr-4 text-gray-300 text-xs">{s.strategy}</td>
                  <td className="py-2 pr-4 text-right">
                    <ScoreBadge v={s.score} best={s.score === bestScore && bestScore > 0} />
                  </td>
                  <td className="py-2 pr-4 text-right text-gray-300">{s.sharpe.toFixed(2)}</td>
                  <td className="py-2 pr-4 text-right text-gray-300">{(s.win_rate*100).toFixed(1)}%</td>
                  <td className="py-2 pr-4 text-right text-gray-300">{s.total_trades}</td>
                  <td className="py-2 text-center">
                    <span className={`inline-block w-2 h-2 rounded-full ${
                      s.active ? 'bg-green-400 shadow-[0_0_4px_#4ade80]' : 'bg-gray-600'
                    }`} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
