/**
 * useBacktest.ts
 * Hook pentru pagina /backtest.
 * Trimite POST /api/backtest/run si polleaza GET /api/backtest/status.
 */
import { useState, useCallback, useRef } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export interface BacktestParams {
  pair_y:       string;
  pair_x:       string;
  interval:     string;
  entry_zscore: number;
  exit_zscore:  number;
  warmup_bars:  number;
  initial_cap:  number;
  start_date?:  string;
  end_date?:    string;
}

export interface BacktestTrade {
  entry_ts:  number;
  exit_ts:   number;
  side:      'long' | 'short';
  pnl_usd:   number;
  fees_usd:  number;
  is_win:    boolean;
}

export interface BacktestResult {
  status:          'idle' | 'running' | 'done' | 'error';
  total_trades:    number;
  wins:            number;
  losses:          number;
  win_rate:        number;
  total_pnl:       number;
  max_drawdown:    number;
  sharpe:          number;
  profit_factor:   number;
  trades:          BacktestTrade[];
  equity_curve:    { t: number; v: number }[];
  error?:          string;
}

const IDLE: BacktestResult = {
  status: 'idle', total_trades: 0, wins: 0, losses: 0,
  win_rate: 0, total_pnl: 0, max_drawdown: 0, sharpe: 0,
  profit_factor: 0, trades: [], equity_curve: [],
};

export function useBacktest() {
  const [result,  setResult]  = useState<BacktestResult>(IDLE);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  const stopPoll = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  const run = useCallback(async (params: BacktestParams) => {
    setLoading(true);
    setError(null);
    setResult(prev => ({ ...prev, status: 'running' }));
    stopPoll();
    try {
      const res = await fetch(`${API}/api/backtest/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // Polleaza status
      pollRef.current = setInterval(async () => {
        try {
          const s = await fetch(`${API}/api/backtest/status`).then(r => r.json());
          setResult(s as BacktestResult);
          if (s.status === 'done' || s.status === 'error') {
            stopPoll();
            setLoading(false);
          }
        } catch { stopPoll(); setLoading(false); }
      }, 1_500);
    } catch (e: any) {
      setError(e?.message ?? 'Backtest error');
      setResult(prev => ({ ...prev, status: 'error' }));
      setLoading(false);
    }
  }, []);

  const reset = useCallback(() => {
    stopPoll();
    setResult(IDLE);
    setError(null);
    setLoading(false);
  }, []);

  return { result, loading, error, run, reset };
}
