/**
 * usePnlStream.ts — S37
 * Hook SSE pentru /risk/stream
 * Reconnect automat la disconnect cu backoff exponential
 */
import { useEffect, useRef, useState } from 'react';

export interface PnlPoint {
  ts:     number;   // epoch ms
  equity: number;
  net_pnl?: number;
}

interface State {
  data:      PnlPoint[];
  connected: boolean;
  error:     string | null;
}

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export function usePnlStream(maxPoints = 200) {
  const [state, setState] = useState<State>({ data: [], connected: false, error: null });
  const retryMs = useRef(1_000);
  const esRef   = useRef<EventSource | null>(null);
  const alive   = useRef(true);

  useEffect(() => {
    alive.current = true;

    function connect() {
      if (!alive.current) return;
      const es = new EventSource(`${API}/risk/stream`);
      esRef.current = es;

      es.onopen = () => {
        retryMs.current = 1_000;
        setState(s => ({ ...s, connected: true, error: null }));
      };

      es.onmessage = (e) => {
        try {
          const payload = JSON.parse(e.data);
          const point: PnlPoint = {
            ts:      payload.ts ?? Date.now(),
            equity:  payload.equity_usd ?? payload.equity ?? 0,
            net_pnl: payload.net_pnl_usd ?? payload.net_pnl,
          };
          setState(s => ({
            ...s,
            data: [...s.data.slice(-(maxPoints - 1)), point],
          }));
        } catch {
          // malformed frame — skip
        }
      };

      es.onerror = () => {
        es.close();
        setState(s => ({ ...s, connected: false, error: 'Stream disconnected' }));
        const delay = Math.min(retryMs.current, 30_000);
        retryMs.current = delay * 2;
        setTimeout(connect, delay);
      };
    }

    connect();
    return () => {
      alive.current = false;
      esRef.current?.close();
    };
  }, [maxPoints]);

  return state;
}
