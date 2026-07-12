/**
 * WatchdogPanel.tsx — S37 polish
 * Migrat la design system: ql-card, ql-btn, CSS vars.
 * Empty state cu icon. useToast pe actiuni.
 */
'use client';
import React, { useCallback, useState } from 'react';
import { useWatchdog }     from '../hooks/useWatchdog';
import { useToast }        from './Toast';
import { Card }            from './ui/Card';
import { Badge }           from './ui/Badge';
import { Spinner }         from './ui/Spinner';

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
  return 'text-[var(--text-muted)]';
}
function levelBg(level?: string) {
  if (level === 'critical') return 'bg-red-900/30';
  if (level === 'warning')  return 'bg-yellow-900/20';
  return '';
}

interface Props { fullPage?: boolean; }

export function WatchdogPanel({ fullPage }: Props) {
  const { status, alerts, loading, error, refetch } = useWatchdog();
  const toast = useToast();
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
    <Card variant={criticalCount > 0 ? 'danger' : 'default'}>
      {/* Header */}
      <Card.Header>
        <div className="flex items-center gap-2">
          <Card.Title>Watchdog</Card.Title>
          {criticalCount > 0 && (
            <Badge variant="red" dot pulse>{criticalCount} CRIT</Badge>
          )}
          {warningCount > 0 && criticalCount === 0 && (
            <Badge variant="yellow" dot>{warningCount} WARN</Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={status?.enabled ? 'green' : 'gray'} dot={status?.enabled} pulse={status?.enabled}>
            {status?.enabled ? 'Active' : 'Disabled'}
          </Badge>
          <button
            onClick={() => setExpanded(e => !e)}
            className="ql-btn ql-btn-ghost px-2 py-1"
            aria-label={expanded ? 'Collapse' : 'Expand'}
          >
            {expanded ? '▲' : '▼'}
          </button>
        </div>
      </Card.Header>

      {error && (
        <p className="text-red-400 text-sm mb-3">{error}</p>
      )}

      {/* KPI mini-cards */}
      {status && (
        <div className="flex flex-wrap gap-3 mb-4">
          <div className="ql-card bg-[var(--bg-elevated)] px-4 py-2 flex flex-col items-center min-w-[90px]">
            <span className="text-[var(--text-muted)] text-[10px] uppercase tracking-wider">Total alerts</span>
            <span className="text-white font-bold text-lg tabular">{status.alerts_total ?? 0}</span>
          </div>
          <div className="ql-card bg-[var(--bg-elevated)] px-4 py-2 flex flex-col items-center min-w-[90px]">
            <span className="text-[var(--text-muted)] text-[10px] uppercase tracking-wider">Halted pairs</span>
            <span className={`font-bold text-lg tabular ${
              (status.halted_pairs?.length ?? 0) > 0 ? 'text-red-400' : 'text-white'
            }`}>{status.halted_pairs?.length ?? 0}</span>
          </div>
          {(status.halted_pairs as string[] | undefined)?.map(p => (
            <Badge key={p} variant="red" className="self-center">{p}</Badge>
          ))}
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-wrap gap-2 mb-5">
        <button onClick={enable}  className="ql-btn ql-btn-success">Enable</button>
        <button onClick={disable} className="ql-btn ql-btn-ghost">Disable</button>
        <button onClick={silence} className="ql-btn ql-btn-warn">Silence 1h</button>
      </div>

      {/* Alerts list */}
      {expanded && (
        <>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[var(--text-muted)] text-[10px] uppercase tracking-widest">
              Recent Alerts
            </span>
            {alerts.length > 0 && (
              <span className="text-[var(--text-muted)] text-[10px]">({alerts.length})</span>
            )}
            {loading && <Spinner size="sm" />}
          </div>

          {loading && alerts.length === 0 ? (
            <div className="space-y-1.5">
              {[1,2,3].map(i => <div key={i} className="skeleton h-6 rounded" />)}
            </div>
          ) : alerts.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 gap-2">
              <span className="text-3xl">&#10003;</span>
              <p className="text-[var(--text-muted)] text-sm">No alerts recorded</p>
            </div>
          ) : (
            <ul className={`space-y-1 overflow-y-auto ${
              fullPage ? '' : 'max-h-52'
            }`}>
              {alerts.slice(0, fullPage ? 200 : 25).map((a, i) => (
                <li
                  key={i}
                  className={`flex items-start gap-2 text-xs rounded-lg px-2 py-1.5 ${
                    levelBg(a.level)
                  }`}
                >
                  <span className="text-[var(--text-muted)] shrink-0 tabular mono">
                    {a.ts ? new Date(a.ts).toLocaleTimeString() : '--:--'}
                  </span>
                  <span className={`shrink-0 font-semibold ${levelColor(a.level)}`}>
                    [{a.level ?? 'info'}]
                  </span>
                  <span className="text-[var(--text-secondary)] break-words">
                    {a.message ?? JSON.stringify(a)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Card>
  );
}
