/**
 * ThresholdEditor.tsx — S39 watchdog threshold editor
 * Componentă pentru editarea threshold-urilor MonitoringWatchdog din UI.
 * Sursa: /api/watchdog/thresholds și /api/watchdog/thresholds/{pair}
 */
'use client';
import React, { useState, useCallback } from 'react';
import { Card } from './ui/Card';
import { Badge } from './ui/Badge';
import { Spinner } from './ui/Spinner';
import { useToast } from './Toast';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

interface Threshold {
  sharpe_min?: number;
  max_drawdown?: number;
  z_max?: number;
  hl_max?: number;
  loss_streak?: number;
  action?: string;
  silenced_until?: string;
}

interface ThresholdsResponse {
  thresholds: Record<string, Threshold>;
}

interface Props { fullPage?: boolean; }

export function ThresholdEditor({ fullPage }: Props) {
  const [thresholds, setThresholds] = useState<Record<string, Threshold>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<Threshold>({});
  const toast = useToast();

  const loadThresholds = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/watchdog/thresholds`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const json: ThresholdsResponse = await r.json();
      setThresholds(json.thresholds || {});
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'fetch error');
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    loadThresholds();
    const id = setInterval(loadThresholds, 10000); // refresh la 10s
    return () => clearInterval(id);
  }, [loadThresholds]);

  const startEdit = (pair: string) => {
    setEditing(pair);
    setEditForm({ ...thresholds[pair] });
  };

  const cancelEdit = () => {
    setEditing(null);
    setEditForm({});
  };

  const saveEdit = async () => {
    if (!editing) return;
    try {
      const r = await fetch(`${API}/api/watchdog/thresholds/${editing}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(editForm),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      toast('Thresholds updated successfully', 'success');
      setEditing(null);
      loadThresholds();
    } catch (e) {
      toast('Failed to update thresholds', 'error');
    }
  };

  const silencePair = async (pair: string, minutes: number = 60) => {
    try {
      const r = await fetch(`${API}/api/watchdog/silence/${pair}?minutes=${minutes}`, {
        method: 'POST',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      toast(`Alerts silenced for ${minutes} minutes`, 'info');
      loadThresholds();
    } catch (e) {
      toast('Failed to silence pair', 'error');
    }
  };

  const unsilencePair = async (pair: string) => {
    try {
      const r = await fetch(`${API}/api/watchdog/unsilence/${pair}`, {
        method: 'POST',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      toast('Alerts unsilenced', 'info');
      loadThresholds();
    } catch (e) {
      toast('Failed to unsilence pair', 'error');
    }
  };

  const isSilenced = (pair: string) => {
    const silenced = thresholds[pair]?.silenced_until;
    if (!silenced) return false;
    return new Date(silenced) > new Date();
  };

  return (
    <Card>
      <Card.Header>
        <div className="flex items-center gap-2">
          <Card.Title>Watchdog Thresholds</Card.Title>
          {loading && <Spinner size="sm" />}
        </div>
        <button
          onClick={loadThresholds}
          className="ql-btn ql-btn-ghost px-2 py-1"
          disabled={loading}
        >
          ↻ Refresh
        </button>
      </Card.Header>

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

      {loading && Object.keys(thresholds).length === 0 ? (
        <div className="space-y-2">
          {[1,2,3].map(i => <div key={i} className="skeleton h-12 rounded" />)}
        </div>
      ) : Object.keys(thresholds).length === 0 ? (
        <p className="text-[var(--text-muted)] text-sm py-6 text-center">
          No thresholds configured yet.
        </p>
      ) : (
        <div className={`space-y-3 overflow-y-auto ${
          fullPage ? '' : 'max-h-96'
        }`}>
          {Object.entries(thresholds).map(([pair, thresh]) => (
            <div
              key={pair}
              className="p-3 border border-[var(--border)] rounded-lg bg-[var(--bg-elevated)]"
            >
              {editing === pair ? (
                /* Edit mode */
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-cyan-400">{pair}</span>
                    <div className="flex gap-2">
                      <button
                        onClick={saveEdit}
                        className="ql-btn ql-btn-success px-2 py-1 text-xs"
                      >
                        Save
                      </button>
                      <button
                        onClick={cancelEdit}
                        className="ql-btn ql-btn-ghost px-2 py-1 text-xs"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <label className="text-[var(--text-muted)] block mb-1">Sharpe Min</label>
                      <input
                        type="number"
                        step="0.1"
                        value={editForm.sharpe_min ?? ''}
                        onChange={e => setEditForm({ ...editForm, sharpe_min: parseFloat(e.target.value) || undefined })}
                        className="w-full bg-[var(--bg-body)] border border-[var(--border)] rounded px-2 py-1 text-[var(--text-primary)]"
                      />
                    </div>
                    <div>
                      <label className="text-[var(--text-muted)] block mb-1">Max DD %</label>
                      <input
                        type="number"
                        step="0.01"
                        value={editForm.max_drawdown !== undefined ? (editForm.max_drawdown * 100).toFixed(2) : ''}
                        onChange={e => setEditForm({ ...editForm, max_drawdown: parseFloat(e.target.value) / 100 || undefined })}
                        className="w-full bg-[var(--bg-body)] border border-[var(--border)] rounded px-2 py-1 text-[var(--text-primary)]"
                      />
                    </div>
                    <div>
                      <label className="text-[var(--text-muted)] block mb-1">Z Max</label>
                      <input
                        type="number"
                        step="0.5"
                        value={editForm.z_max ?? ''}
                        onChange={e => setEditForm({ ...editForm, z_max: parseFloat(e.target.value) || undefined })}
                        className="w-full bg-[var(--bg-body)] border border-[var(--border)] rounded px-2 py-1 text-[var(--text-primary)]"
                      />
                    </div>
                    <div>
                      <label className="text-[var(--text-muted)] block mb-1">HL Max (h)</label>
                      <input
                        type="number"
                        step="1"
                        value={editForm.hl_max ?? ''}
                        onChange={e => setEditForm({ ...editForm, hl_max: parseInt(e.target.value) || undefined })}
                        className="w-full bg-[var(--bg-body)] border border-[var(--border)] rounded px-2 py-1 text-[var(--text-primary)]"
                      />
                    </div>
                    <div>
                      <label className="text-[var(--text-muted)] block mb-1">Loss Streak</label>
                      <input
                        type="number"
                        step="1"
                        value={editForm.loss_streak ?? ''}
                        onChange={e => setEditForm({ ...editForm, loss_streak: parseInt(e.target.value) || undefined })}
                        className="w-full bg-[var(--bg-body)] border border-[var(--border)] rounded px-2 py-1 text-[var(--text-primary)]"
                      />
                    </div>
                    <div>
                      <label className="text-[var(--text-muted)] block mb-1">Action</label>
                      <select
                        value={editForm.action ?? ''}
                        onChange={e => setEditForm({ ...editForm, action: e.target.value || undefined })}
                        className="w-full bg-[var(--bg-body)] border border-[var(--border)] rounded px-2 py-1 text-[var(--text-primary)]"
                      >
                        <option value="">Default</option>
                        <option value="ALERT_ONLY">Alert Only</option>
                        <option value="REDUCE_SIZE">Reduce Size</option>
                        <option value="HALT">Halt</option>
                      </select>
                    </div>
                  </div>
                </div>
              ) : (
                /* View mode */
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-cyan-400">{pair}</span>
                    <div className="flex items-center gap-2">
                      {isSilenced(pair) && (
                        <Badge variant="yellow" dot>
                          Silenced
                        </Badge>
                      )}
                      <Badge variant={thresh.action === 'HALT' ? 'red' : thresh.action === 'REDUCE_SIZE' ? 'yellow' : 'green'}>
                        {thresh.action || 'Default'}
                      </Badge>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-xs text-[var(--text-secondary)]">
                    <div>Sharpe ≥ {thresh.sharpe_min?.toFixed(2) ?? '—'}</div>
                    <div>DD ≤ {thresh.max_drawdown !== undefined ? (thresh.max_drawdown * 100).toFixed(1) + '%' : '—'}</div>
                    <div>\|Z\| ≤ {thresh.z_max?.toFixed(1) ?? '—'}</div>
                    <div>HL ≤ {thresh.hl_max ?? '—'}h</div>
                    <div>Streak ≤ {thresh.loss_streak ?? '—'}</div>
                  </div>
                  <div className="flex gap-2 pt-1">
                    <button
                      onClick={() => startEdit(pair)}
                      className="ql-btn ql-btn-ghost px-2 py-1 text-xs"
                    >
                      Edit
                    </button>
                    {isSilenced(pair) ? (
                      <button
                        onClick={() => unsilencePair(pair)}
                        className="ql-btn ql-btn-ghost px-2 py-1 text-xs text-green-400"
                      >
                        Unsilence
                      </button>
                    ) : (
                      <button
                        onClick={() => silencePair(pair, 60)}
                        className="ql-btn ql-btn-ghost px-2 py-1 text-xs text-yellow-400"
                      >
                        Silence 1h
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
