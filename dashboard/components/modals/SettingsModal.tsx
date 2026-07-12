/**
 * SettingsModal.tsx
 * Modal setari bot: entry/exit zscore, qty, dry_run, interval.
 * Trimite PATCH /api/config cu noile valori.
 */
'use client';
import React, { useEffect, useState, useRef } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

interface BotConfig {
  entry_zscore: number;
  exit_zscore:  number;
  base_qty:     number;
  interval:     string;
  dry_run:      boolean;
  max_drawdown_pct: number;
}

interface Props {
  open:    boolean;
  onClose: () => void;
}

export default function SettingsModal({ open, onClose }: Props) {
  const [cfg,     setCfg]     = useState<BotConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [saved,   setSaved]   = useState(false);
  const [error,   setError]   = useState<string | null>(null);
  const firstRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    fetch(`${API}/api/config`)
      .then(r => r.json())
      .then(d => { setCfg(d); setLoading(false); })
      .catch(() => {
        setCfg({ entry_zscore: 2.0, exit_zscore: 0.5, base_qty: 0.01,
                 interval: '60', dry_run: true, max_drawdown_pct: 5.0 });
        setLoading(false);
      });
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (open) setTimeout(() => firstRef.current?.focus(), 50);
  }, [open]);

  if (!open) return null;

  const save = async () => {
    if (!cfg) return;
    setSaved(false); setError(null);
    try {
      const res = await fetch(`${API}/api/config`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfg),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSaved(true);
      setTimeout(() => { setSaved(false); onClose(); }, 1_200);
    } catch (e: any) {
      setError(e?.message ?? 'Save failed');
    }
  };

  const field = (label: string, key: keyof BotConfig, type: 'number' | 'text' | 'checkbox' = 'number') => (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <label className="text-xs text-[var(--text-muted)] min-w-[140px]">{label}</label>
      {type === 'checkbox' ? (
        <input
          type="checkbox"
          checked={!!cfg?.[key]}
          onChange={e => setCfg(p => p ? { ...p, [key]: e.target.checked } : p)}
          className="w-4 h-4 accent-[var(--purple)]"
        />
      ) : (
        <input
          ref={key === 'entry_zscore' ? firstRef : undefined}
          type={type}
          value={cfg?.[key] as string | number ?? ''}
          onChange={e => setCfg(p => p ? {
            ...p,
            [key]: type === 'number' ? parseFloat(e.target.value) : e.target.value
          } : p)}
          className="w-28 rounded-lg px-2 py-1 text-xs mono
                     bg-[var(--bg-body)] border border-[var(--border)]
                     text-[var(--text-primary)] focus:outline-none
                     focus:border-[var(--purple)]"
        />
      )}
    </div>
  );

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
      onClick={onClose}
    >
      <div
        className="rounded-xl p-6 shadow-2xl w-[400px]"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}
        onClick={e => e.stopPropagation()}
      >
        <h2 className="text-base font-bold text-[var(--text-primary)] mb-4">⚙ Bot Settings</h2>
        {loading ? (
          <p className="text-xs text-[var(--text-muted)]">Loading config...</p>
        ) : (
          <div>
            {field('Entry Z-Score',    'entry_zscore')}
            {field('Exit Z-Score',     'exit_zscore')}
            {field('Base Qty (USDT)',  'base_qty')}
            {field('Interval (min)',   'interval', 'text')}
            {field('Max Drawdown %',  'max_drawdown_pct')}
            {field('Dry Run',         'dry_run', 'checkbox')}
          </div>
        )}
        {error && <p className="text-xs text-red-400 mt-2">{error}</p>}
        {saved && <p className="text-xs text-green-400 mt-2">✓ Saved!</p>}
        <div className="flex justify-end gap-3 mt-6">
          <button onClick={onClose}
            className="px-4 py-1.5 rounded-lg text-sm text-[var(--text-muted)]
                       border border-[var(--border)] hover:border-[var(--text-muted)]"
          >Cancel</button>
          <button onClick={save}
            className="px-4 py-1.5 rounded-lg text-sm font-semibold
                       bg-[var(--purple)] hover:opacity-90 text-white"
          >Save</button>
        </div>
      </div>
    </div>
  );
}
