/**
 * useWatchdog.ts — S37 (review fix: importă din types/)
 * Polling paralel /api/watchdog/status + /api/watchdog/alerts (8s).
 */
import { useEffect, useState, useCallback } from 'react';
import type { WatchdogStatus, WatchdogAlert } from '../types/dashboard';

export type { WatchdogStatus, WatchdogAlert };

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export function useWatchdog(intervalMs = 8_000) {
  const [status,  setStatus]  = useState<WatchdogStatus | null>(null);
  const [alerts,  setAlerts]  = useState<WatchdogAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [sRes, aRes] = await Promise.all([
        fetch(`${API}/api/watchdog/status`),
        fetch(`${API}/api/watchdog/alerts`),
      ]);
      if (sRes.ok) setStatus(await sRes.json());
      if (aRes.ok) {
        const a = await aRes.json();
        setAlerts(Array.isArray(a) ? a : (a.alerts ?? []));
      }
      setError(null);
    } catch {
      setError('Watchdog unavailable');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, intervalMs);
    return () => clearInterval(id);
  }, [fetchAll, intervalMs]);

  return { status, alerts, loading, error, refetch: fetchAll };
}
