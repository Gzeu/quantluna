/**
 * StrategyScores.tsx — S37
 * Tabel cu scoruri AutoSelector per pereche
 * Date din /api/optimizer/results (polling 10s)
 */
'use client';
import React, { useEffect, useState } from 'react';
import { useStrategyScores } from '../hooks/useStrategyScores';

interface PairScore {
  pair:        string;
  strategy:    string;
  score:       number;
  sharpe:      number;
  win_rate:    number;
  total_trades: number;
  active:      boolean;
}

function ScoreBadge({ v }: { v: number }) {
  const color = v >= 0.7 ? 'text-green-400' : v >= 0.4 ? 'text-yellow-400' : 'text-red-400';
  return <span className={`font-bold ${color}`}>{v.toFixed(3)}</span>;
}

export function StrategyScores() {
  const { scores, loading, error, lastUpdated } = useStrategyScores();

  return (
    <div className="bg-gray-900 rounded-2xl p-5 w-full">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-white font-semibold text-lg">Strategy Scores</h2>
        {lastUpdated && (
          <span className="text-xs text-gray-500">
            Updated {new Date(lastUpdated).toLocaleTimeString()}
          </span>
        )}
      </div>

      {error && <p className="text-red-400 text-sm mb-2">{error}</p>}

      {loading && scores.length === 0 ? (
        <p className="text-gray-500 text-sm">Loading scores…</p>
      ) : scores.length === 0 ? (
        <p className="text-gray-500 text-sm">No optimizer results yet. Run an optimization first.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-400 border-b border-gray-800">
                <th className="text-left py-2 pr-4">Pair</th>
                <th className="text-left py-2 pr-4">Strategy</th>
                <th className="text-right py-2 pr-4">Score</th>
                <th className="text-right py-2 pr-4">Sharpe</th>
                <th className="text-right py-2 pr-4">Win%</th>
                <th className="text-right py-2 pr-4">Trades</th>
                <th className="text-center py-2">Active</th>
              </tr>
            </thead>
            <tbody>
              {scores.map((s) => (
                <tr key={s.pair} className="border-b border-gray-800 hover:bg-gray-800 transition-colors">
                  <td className="py-2 pr-4 font-mono text-cyan-400">{s.pair}</td>
                  <td className="py-2 pr-4 text-gray-300">{s.strategy}</td>
                  <td className="py-2 pr-4 text-right"><ScoreBadge v={s.score} /></td>
                  <td className="py-2 pr-4 text-right text-gray-300">{s.sharpe.toFixed(2)}</td>
                  <td className="py-2 pr-4 text-right text-gray-300">{(s.win_rate * 100).toFixed(1)}%</td>
                  <td className="py-2 pr-4 text-right text-gray-300">{s.total_trades}</td>
                  <td className="py-2 text-center">
                    <span className={`inline-block w-2 h-2 rounded-full ${
                      s.active ? 'bg-green-400' : 'bg-gray-600'
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
