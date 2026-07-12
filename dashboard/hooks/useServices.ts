/**
 * useServices.ts
 * Polling GET /api/services/list la interval configurabil.
 * Returneaza lista servicii, status running/stopped, si loading/error.
 */
import { useState, useEffect, useCallback } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export interface ServiceInfo {
  name:    string;
  status:  'running' | 'stopped' | 'unknown';
  pid?:    number;
  uptime?: string;
  cpu?:    number;
  mem?:    number;
}

export interface ServicesData {
  services: ServiceInfo[];
  total:    number;
  running:  number;
  ts:       number;
}

const EMPTY: ServicesData = { services: [], total: 0, running: 0, ts: 0 };

export function useServices(intervalMs = 5_000) {
  const [data,    setData]    = useState<ServicesData>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/services/list`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json as ServicesData);
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? 'Backend offline');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetch_();
    const id = setInterval(fetch_, intervalMs);
    return () => clearInterval(id);
  }, [fetch_, intervalMs]);

  return { data, loading, error, refetch: fetch_ };
}
