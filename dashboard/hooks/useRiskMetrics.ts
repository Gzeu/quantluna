/**
 * hooks/useRiskMetrics.ts — S37 metrics expansion
 * Hook dedicat polling /risk/dashboard cu toate câmpurile noi.
 * Populează store.tradeStats și returnează RiskMetrics direct.
 */
import { useEffect, useState, useCallback } from 'react';
import { useQuantLunaStore } from '../store/quantlunaStore';
import type { RiskMetrics } from '../types/dashboard';

const API     = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const INTERVAL = 5_000;

const EMPTY: RiskMetrics = {
  rolling_sharpe: 0, drawdown_current: 0, win_rate: 0,
  exposure_usd: 0, equity_usd: 0,
  wins: 0, losses: 0, total_trades: 0,
  avg_win_usd: 0, avg_loss_usd: 0,
  profit_factor: 0, max_drawdown: 0,
  max_consecutive_wins: 0, max_consecutive_losses: 0,
  current_streak: 0, unrealized_pnl: 0,
  daily_pnl: 0, daily_pct: 0,
};

export function useRiskMetrics() {
  const setTradeStats = useQuantLunaStore(s => s.setTradeStats);
  const [data,    setData]    = useState<RiskMetrics>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/risk/dashboard`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const json: RiskMetrics = await r.json();
      setData(json);
      setError(null);
      // Sync relevant fields to global store
      setTradeStats({
        wins:                   json.wins   ?? 0,
        losses:                 json.losses ?? 0,
        total_trades:           json.total_trades ?? 0,
        win_rate:               json.win_rate,
        avg_win_usd:            json.avg_win_usd   ?? 0,
        avg_loss_usd:           json.avg_loss_usd  ?? 0,
        profit_factor:          json.profit_factor ?? 0,
        max_drawdown:           json.max_drawdown  ?? 0,
        max_consecutive_wins:   json.max_consecutive_wins   ?? 0,
        max_consecutive_losses: json.max_consecutive_losses ?? 0,
        current_streak:         json.current_streak ?? 0,
        pair_breakdown:         json.pair_breakdown ?? [],
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'fetch error');
    } finally {
      setLoading(false);
    }
  }, [setTradeStats]);

  useEffect(() => {
    load();
    const id = setInterval(load, INTERVAL);
    return () => clearInterval(id);
  }, [load]);

  return { data, loading, error, refetch: load };
}
