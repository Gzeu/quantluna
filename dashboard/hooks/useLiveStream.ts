/**
 * QuantLuna — useLiveStream
 * Sprint 24
 *
 * React hook: consumes GET /live/stream (SSE).
 * Reconnects automatically on disconnect (exponential backoff).
 *
 * Returns { status, lastBar, connected }.
 *
 * Usage:
 *   const { status, lastBar, connected } = useLiveStream();
 */
import { useEffect, useRef, useState } from "react";

export interface LiveStatus {
  state:            string;
  mode:             string;
  sym_y:            string;
  sym_x:            string;
  bar_freq:         string;
  active_strategy:  string;
  regime:           string;
  position_side:    number;
  unrealised_pnl:   number;
  realised_pnl:     number;
  n_trades:         number;
  bars_processed:   number;
  last_bar_ts:      string | null;
  scores:           Record<string, number>;
  switch_history:   Array<{ from: string; to: string }>;
  uptime_s:         number;
  error:            string | null;
}

export interface LiveBarEvent {
  ts:              string;
  spread:          number;
  zscore:          number;
  regime:          string;
  signal:          number;
  active_strategy: string;
}

export function useLiveStream(apiBase: string = "") {
  const [status,    setStatus]    = useState<LiveStatus | null>(null);
  const [lastBar,   setLastBar]   = useState<LiveBarEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const esRef     = useRef<EventSource | null>(null);
  const backoffRef = useRef(1000);

  useEffect(() => {
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      const es = new EventSource(`${apiBase}/live/stream`);
      esRef.current = es;

      es.addEventListener("open", () => {
        if (!cancelled) { setConnected(true); backoffRef.current = 1000; }
      });

      es.addEventListener("status", (e: MessageEvent) => {
        if (!cancelled) {
          try { setStatus(JSON.parse(e.data) as LiveStatus); } catch {}
        }
      });

      es.addEventListener("bar", (e: MessageEvent) => {
        if (!cancelled) {
          try { setLastBar(JSON.parse(e.data) as LiveBarEvent); } catch {}
        }
      });

      es.addEventListener("heartbeat", () => {
        if (!cancelled) setConnected(true);
      });

      es.onerror = () => {
        es.close();
        if (!cancelled) {
          setConnected(false);
          const delay = Math.min(backoffRef.current, 30000);
          backoffRef.current = Math.min(delay * 2, 30000);
          setTimeout(connect, delay);
        }
      };
    };

    connect();
    return () => {
      cancelled = true;
      esRef.current?.close();
    };
  }, [apiBase]);

  return { status, lastBar, connected };
}
