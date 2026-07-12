/**
 * WatchdogPanel.tsx — S37 polish
 * Integrat cu useToast pentru feedback la Enable/Disable/Silence
 * Collapse/expand, badge count, level colors, fullPage prop
 */
'use client';
import React, { useCallback, useState } from 'react';
import { useWatchdog } from '../hooks/useWatchdog';
import { useToast }   from './Toast';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

async function postAction(path: string): Promise<boolean> {
  try {
    const r = await fetch(`${API}${path}`, { method: 'POST' });
    return r.ok;
  } catch {
    return false;
  }
}

function levelColor(level?: string) {
  if (level === 'critical') return 'text-red-400';
  if (level === 'warning')  return 'text-yellow-400';
  return 'text-gray-400';
}
function levelBg(level?: string) {
  if (level === 'critical') return 'bg-red-900/40';
  if (level === 'warning')  return 'bg-yellow-900/30';
  return '';
}

interface Props { fullPage?: boolean; }

export function WatchdogPanel({ fullPage }: Props) {
  const { status, alerts, loading, error, refetch } = useWatchdog();
  const toast    = useToast();
  const [expanded, setExpanded] = useState(true);

  const criticalCount = alerts.filter(a => a.level === 'critical').length;
  const warningCount  = alerts.filter(a => a.level === 'warning').length;

  const enable = useCallback(async () => {
    const ok = await postAction('/api/watchdog/enable');
    toast(ok ? 'Watchdog enabled' : 'Enable failed — API error', ok ? 'success' : 'error');
    refetch();
  }, [refetch, toast]);

  const disable = useCallback(async () => {
    const ok = await postAction('/api/watchdog/disable');
    toast(ok ? 'Watchdog disabled' : 'Disable failed — API error', ok ? 'warn' : 'error');
    refetch();
  }, [refetch, toast]);

  const silence = useCallback(async () => {
    const ok = await postAction('/api/watchdog/silence');
    toast(ok ? 'Alerts silenced for 1h' : 'Silence failed — API error', ok ? 'info' : 'error');
    refetch();
  }, [refetch, toast]);

  return (
    <div className="bg-gray-900 rounded-2xl p-5 w-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <h2 className="text-white font-semibold text-lg">Watchdog</h2>
          {criticalCount > 0 && (
            <span className="text-xs font-bold bg-red-900 text-red-300 px-2 py-0.5 rounded-full animate-pulse">
              {criticalCount} CRIT
            </span>
          )}
          {warningCount > 0 && criticalCount === 0 && (
            <span className="text-xs font-semibold bg-yellow-900 text-yellow-300 px-2 py-0.5 rounded-full">
              {warningCount} WARN
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            status?.enabled ? 'bg-green-900 text-green-300' : 'bg-gray-700 text-gray-400'
          }`}>
            {status?.enabled ? '◉ Active' : '○ Disabled'}
          </span>
          <button onClick={() => setExpanded(e => !e)}
            className="text-gray-500 hover:text-gray-300 text-xs px-2 py-1 rounded-lg hover:bg-gray-800 transition-colors">
            {expanded ? '▲' : '▼'}
          </button>
        </div>
      </div>

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

      {/* KPI */}
      {status && (
        <div className="flex flex-wrap gap-3 mb-4">
          <div className="bg-gray-800 rounded-xl px-4 py-2 flex flex-col items-center min-w-[90px]">
            <span className="text-gray-400 text-xs">Alerts total</span>
            <span className="text-white font-bold text-lg tabular-nums">{status.alerts_total ?? 0}</span>
          </div>
          <div className="bg-gray-800 rounded-xl px-4 py-2 flex flex-col items-center min-w-[90px]">
            <span className="text-gray-400 text-xs">Halted pairs</span>
            <span className="text-white font-bold text-lg tabular-nums">{status.halted_pairs?.length ?? 0}</span>
          </div>
          {(status.halted_pairs?.length ?? 0) > 0 &&
            (status.halted_pairs as string[]).map(p => (
              <span key={p} className="self-center text-xs bg-red-900 text-red-300 px-2 py-0.5 rounded-full">{p}</span>
            ))
          }
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-wrap gap-2 mb-5">
        <button onClick={enable}  className="px-3 py-1.5 text-xs rounded-lg bg-green-900 hover:bg-green-800 text-green-200 transition-colors">Enable</button>
        <button onClick={disable} className="px-3 py-1.5 text-xs rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-200 transition-colors">Disable</button>
        <button onClick={silence} className="px-3 py-1.5 text-xs rounded-lg bg-yellow-900 hover:bg-yellow-800 text-yellow-200 transition-colors">Silence 1h</button>
      </div>

      {/* Alerts */}
      {expanded && (
        <>
          <h3 className="text-gray-400 text-xs uppercase tracking-wider mb-2">
            Recent Alerts
            {alerts.length > 0 && <span className="ml-2 text-gray-600">({alerts.length})</span>}
          </h3>
          {loading && alerts.length === 0 ? (
            <p className="text-gray-500 text-sm">Loading…</p>
          ) : alerts.length === 0 ? (
            <p className="text-gray-500 text-sm">✔ No alerts recorded.</p>
          ) : (
            <ul className={`space-y-1 overflow-y-auto ${fullPage ? '' : 'max-h-52'}`}>
              {alerts.slice(0, fullPage ? 200 : 25).map((a, i) => (
                <li key={i} className={`flex items-start gap-2 text-xs rounded-lg px-2 py-1 ${levelBg(a.level)}`}>
                  <span className="text-gray-500 shrink-0 tabular-nums">
                    {a.ts ? new Date(a.ts).toLocaleTimeString() : '--:--'}
                  </span>
                  <span className={`shrink-0 font-semibold ${levelColor(a.level)}`}>
                    [{a.level ?? 'info'}]
                  </span>
                  <span className="text-gray-300 break-words">{a.message ?? JSON.stringify(a)}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
