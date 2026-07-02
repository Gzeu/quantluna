'use client';
/**
 * QuantLuna Dashboard — SSE Hook
 * Sprint 30
 *
 * Consuma Server-Sent Events de la /risk/stream.
 * Auto-reconnect la 3s dupa disconnect.
 * Tipat generic: useSSE<RiskSnapshot>('/risk/stream')
 */
import { useEffect, useRef, useState } from 'react';

const BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export function useSSE<T>(path: string, initialValue: T): T {
  const [data, setData] = useState<T>(initialValue);
  const esRef           = useRef<EventSource | null>(null);
  const timerRef        = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let active = true;

    function connect() {
      if (!active) return;
      const es = new EventSource(`${BASE}${path}`);
      esRef.current = es;

      es.onmessage = (e) => {
        try {
          const parsed = JSON.parse(e.data) as T;
          setData(parsed);
        } catch { /* ignore parse errors */ }
      };

      es.onerror = () => {
        es.close();
        if (active) {
          timerRef.current = setTimeout(connect, 3000);
        }
      };
    }

    connect();

    return () => {
      active = false;
      esRef.current?.close();
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [path]);

  return data;
}
