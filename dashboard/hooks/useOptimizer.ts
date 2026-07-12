/**
 * useOptimizer.ts
 * Polling GET /api/optimizer/status.
 * Expune run() si stop() via POST.
 */
import { useState, useEffect, useCallback } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export interface OptimizerStatus {
  running:               boolean;
  auto_reoptimizer_active: boolean;
  last_run_ts?:          number;
  best_params?:          Record<string, number>;
  best_score?:           number;
  iterations?:           number;
  error?:                string;
}

const EMPTY: OptimizerStatus = { running: false, auto_reoptimizer_active: false };

export function useOptimizer(intervalMs = 4_000) {
  const [status,  setStatus]  = useState<OptimizerStatus>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);

  const poll = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/optimizer/status`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setStatus(await res.json());
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? 'Optimizer offline');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, intervalMs);
    return () => clearInterval(id);
  }, [poll, intervalMs]);

  const run = useCallback(async (params?: Record<string, unknown>) => {
    await fetch(`${API}/api/optimizer/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params ?? {}),
    }).catch(() => {});
    poll();
  }, [poll]);

  const stop = useCallback(async () => {
    await fetch(`${API}/api/optimizer/stop`, { method: 'POST' }).catch(() => {});
    poll();
  }, [poll]);

  return { status, loading, error, run, stop, refetch: poll };
}
