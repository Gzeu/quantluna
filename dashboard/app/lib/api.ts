/**
 * QuantLuna Dashboard — API Client
 * Sprint 30
 *
 * Wrapper fetch pentru backend QuantLuna API.
 * Toate request-urile merg la NEXT_PUBLIC_API_URL.
 */

const BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) throw new Error(`API ${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

// ---- typed helpers ----

export interface RiskSnapshot {
  timestamp:        string;
  equity_usdt:      number;
  pnl_usdt:         number;
  sharpe_rolling:   number;
  max_drawdown:     number;
  current_drawdown: number;
  win_rate:         number;
  total_trades:     number;
  open_positions:   number;
  exposure_pct:     number;
}

export interface PairStatus {
  pair:          string;
  status:        'active' | 'halted' | 'idle';
  pnl_usdt:      number;
  n_trades:      number;
  sharpe:        number;
  alloc_usd:     number;
  correlation:   number | null;
}

export interface EquityPoint {
  ts:     string;
  equity: number;
  pnl:    number;
}

export interface AlertItem {
  event_type: string;
  severity:   string;
  timestamp:  string;
  payload:    Record<string, unknown>;
}

export const getRiskSnapshot  = () => apiFetch<RiskSnapshot>('/risk/snapshot');
export const getPairsStatus   = () => apiFetch<PairStatus[]>('/pairs/status');
export const getEquityCurve   = () => apiFetch<EquityPoint[]>('/risk/equity_curve');
export const getRebalancerStatus = () => apiFetch('/rebalancer/status');
export const triggerRebalance = () => apiFetch('/rebalancer/run', { method: 'POST' });
