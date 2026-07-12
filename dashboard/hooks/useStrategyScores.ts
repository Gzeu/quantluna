/**
 * useStrategyScores.ts — S37
 * Hook polling /api/optimizer/results (interval 10s)
 * Normalizeaza raspunsul in array PairScore[]
 */
import { useEffect, useState, useCallback } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export interface PairScore {
  pair:         string;
  strategy:     string;
  score:        number;
  sharpe:       number;
  win_rate:     number;
  total_trades: number;
  active:       boolean;
}

function normalize(raw: unknown): PairScore[] {
  if (Array.isArray(raw)) return raw as PairScore[];
  if (raw && typeof raw === 'object') {
    const r = raw as Record<string, unknown>;
    if (Array.isArray(r.results))  return r.results  as PairScore[];
    if (Array.isArray(r.scores))   return r.scores   as PairScore[];
    if (Array.isArray(r.pairs))    return r.pairs     as PairScore[];
  }
  return [];
}

export function useStrategyScores(intervalMs = 10_000) {
  const [scores,      setScores]      = useState<PairScore[]>([]);
  const [loading,     setLoading]     = useState(true);
  const [error,       setError]       = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  const fetchScores = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/optimizer/results`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const raw = await r.json();
      setScores(normalize(raw));
      setLastUpdated(Date.now());
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Optimizer unavailable');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchScores();
    const id = setInterval(fetchScores, intervalMs);
    return () => clearInterval(id);
  }, [fetchScores, intervalMs]);

  return { scores, loading, error, lastUpdated };
}
