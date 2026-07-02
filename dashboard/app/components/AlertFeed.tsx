'use client';
/**
 * QuantLuna Dashboard — Alert Feed
 * Sprint 30
 *
 * Live feed alerte: consuma /risk/stream si afiseaza ultimele N events.
 * Colorat per severitate: verde/galben/rosu.
 */
import { useSSE } from '../hooks/useSSE';
import { useEffect, useRef, useState } from 'react';
import type { AlertItem } from '../lib/api';
import { format, parseISO } from 'date-fns';
import clsx from 'clsx';

const SEV_STYLE = {
  info:     'border-l-success   bg-success/5  text-success',
  warning:  'border-l-warning   bg-warning/5  text-warning',
  critical: 'border-l-danger    bg-danger/5   text-danger',
};

const EVENT_EMOJI: Record<string, string> = {
  TRADE_OPEN:   '✅',
  TRADE_CLOSE:  '🟢',
  DD_ALERT:     '⚠️',
  SHARPE_DROP:  '📉',
  HALT_CASCADE: '🔴',
  PAIR_START:   '▶️',
  PAIR_STOP:    '⏹️',
  SYSTEM_ERROR: '💥',
  SYSTEM_START: '🚀',
  TEST:         '🧪',
};

const MAX_ITEMS = 30;

export function AlertFeed() {
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const live = useSSE<AlertItem | null>('/risk/alerts/stream', null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!live) return;
    setAlerts((prev) => {
      const next = [live, ...prev].slice(0, MAX_ITEMS);
      return next;
    });
  }, [live]);

  const fmt = (ts: string) => {
    try { return format(parseISO(ts), 'HH:mm:ss'); }
    catch { return ts.slice(11, 19) || '--:--:--'; }
  };

  return (
    <div className="bg-card rounded-xl border border-border flex flex-col" style={{ maxHeight: 340 }}>
      <div className="px-5 py-3 border-b border-border flex items-center justify-between">
        <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Alert Feed</h2>
        <span className="text-xs text-slate-500">{alerts.length} events</span>
      </div>
      <div className="overflow-y-auto flex-1 px-3 py-2 space-y-1.5" ref={bottomRef}>
        {alerts.length === 0 && (
          <p className="text-slate-600 text-xs text-center py-6">Niciun alert inca...</p>
        )}
        {alerts.map((a, i) => (
          <div
            key={i}
            className={clsx(
              'border-l-2 pl-3 pr-2 py-1.5 rounded-r text-xs',
              SEV_STYLE[a.severity as keyof typeof SEV_STYLE] ?? SEV_STYLE.info
            )}
          >
            <div className="flex items-center gap-1.5">
              <span>{EVENT_EMOJI[a.event_type] ?? '🟡'}</span>
              <span className="font-mono font-semibold">{a.event_type.replace(/_/g, ' ')}</span>
              <span className="ml-auto text-slate-500 font-mono">{fmt(a.timestamp)}</span>
            </div>
            {Object.keys(a.payload).length > 0 && (
              <p className="text-slate-400 mt-0.5 truncate">
                {Object.entries(a.payload).slice(0, 3).map(([k, v]) =>
                  `${k}: ${String(v)}`
                ).join(' · ')}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
