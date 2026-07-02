/**
 * QuantLuna — useStrategyScores
 * Sprint 24
 *
 * React hook: polls GET /strategy/scores every `interval` ms.
 * Returns { data, error, loading }.
 *
 * Usage:
 *   const { data, error, loading } = useStrategyScores("live", 5000);
 */
import { useEffect, useRef, useState } from "react";

export interface StrategyScoresData {
  active_strategy: string;
  scores:          Record<string, number>;
  recent_win_rate: number;
  switch_history:  Array<{ from: string; to: string; manual?: boolean }>;
  total_bars:      number;
  selector_id:     string;
}

export function useStrategyScores(
  selectorId: string = "live",
  interval:   number  = 5000,
  apiBase:    string  = "",
): { data: StrategyScoresData | null; error: string | null; loading: boolean } {
  const [data,    setData]    = useState<StrategyScoresData | null>(null);
  const [error,   setError]   = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;

    const fetch_ = async () => {
      try {
        const res = await fetch(`${apiBase}/strategy/scores?selector_id=${encodeURIComponent(selectorId)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json() as StrategyScoresData;
        if (!cancelled) { setData(json); setError(null); }
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    fetch_();
    timerRef.current = setInterval(fetch_, interval);

    return () => {
      cancelled = true;
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [selectorId, interval, apiBase]);

  return { data, error, loading };
}
