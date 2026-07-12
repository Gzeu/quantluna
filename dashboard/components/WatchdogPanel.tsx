/**
 * WatchdogPanel.tsx — S37
 * Status watchdog + alerte recente + butoane enable/disable/silence
 */
'use client';
import React, { useCallback } from 'react';
import { useWatchdog } from '../hooks/useWatchdog';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

function postAction(path: string) {
  return fetch(`${API}${path}`, { method: 'POST' }).catch(console.error);
}

export function WatchdogPanel() {
  const { status, alerts, loading, error, refetch } = useWatchdog();

  const enable  = useCallback(() => postAction('/api/watchdog/enable').then(refetch),  [refetch]);
  const disable = useCallback(() => postAction('/api/watchdog/disable').then(refetch), [refetch]);
  const silence = useCallback(() => postAction('/api/watchdog/silence').then(refetch), [refetch]);

  return (
    <div className="bg-gray-900 rounded-2xl p-5 w-full">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-white font-semibold text-lg">Watchdog</h2>
        <span className={`text-xs px-2 py-1 rounded-full font-medium ${
          status?.enabled
            ? 'bg-green-800 text-green-300'
            : 'bg-gray-700 text-gray-400'
        }`}>
          {status?.enabled ? '● Active' : '○ Disabled'}
        </span>
      </div>

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

      {/* KPI row */}
      {status && (
        <div className="flex gap-4 mb-4">
          <div className="bg-gray-800 rounded-xl px-4 py-2 flex flex-col items-center">
            <span className="text-gray-400 text-xs">Alerts total</span>
            <span className="text-white font-bold text-lg">{status.alerts_total ?? 0}</span>
          </div>
          <div className="bg-gray-800 rounded-xl px-4 py-2 flex flex-col items-center">
            <span className="text-gray-400 text-xs">Halted pairs</span>
            <span className="text-white font-bold text-lg">{status.halted_pairs?.length ?? 0}</span>
          </div>
          {(status.halted_pairs?.length ?? 0) > 0 && (
            <div className="flex flex-col justify-center">
              {status.halted_pairs.map((p: string) => (
                <span key={p} className="text-xs bg-red-900 text-red-300 px-2 py-0.5 rounded-full mb-1">{p}</span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-2 mb-5">
        <button
          onClick={enable}
          className="px-3 py-1.5 text-xs rounded-lg bg-green-800 hover:bg-green-700 text-green-200 transition-colors"
        >Enable</button>
        <button
          onClick={disable}
          className="px-3 py-1.5 text-xs rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-200 transition-colors"
        >Disable</button>
        <button
          onClick={silence}
          className="px-3 py-1.5 text-xs rounded-lg bg-yellow-800 hover:bg-yellow-700 text-yellow-200 transition-colors"
        >Silence 1h</button>
      </div>

      {/* Alerts list */}
      <h3 className="text-gray-400 text-xs uppercase tracking-wider mb-2">Recent Alerts</h3>
      {loading && alerts.length === 0 ? (
        <p className="text-gray-500 text-sm">Loading…</p>
      ) : alerts.length === 0 ? (
        <p className="text-gray-500 text-sm">No alerts recorded.</p>
      ) : (
        <ul className="space-y-1 max-h-48 overflow-y-auto">
          {alerts.slice(0, 20).map((a, i) => (
            <li key={i} className="flex items-start gap-2 text-xs">
              <span className="text-gray-500 shrink-0">
                {a.ts ? new Date(a.ts).toLocaleTimeString() : '--'}
              </span>
              <span className={`shrink-0 font-semibold ${
                a.level === 'critical' ? 'text-red-400' :
                a.level === 'warning'  ? 'text-yellow-400' : 'text-gray-400'
              }`}>[{a.level ?? 'info'}]</span>
              <span className="text-gray-300">{a.message ?? JSON.stringify(a)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
