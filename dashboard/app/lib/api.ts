/**
 * QuantLuna Dashboard — API Client
 * Sprint S46 (2026-07-12) — versiune 0.31.0
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

/** GET /sizing/live_status — SizingEngine v2.5 */
export interface SizingLiveStatus {
  enabled:           boolean;
  source:            string;
  status?:           string;
  capital_usdt?:     number;
  max_leverage?:     number;
  kelly_fraction?:   string;
  // campuri expuse de get_status() daca engineul e prezent
  last_multiplier?:  number;
  current_streak?:   number;
  current_drawdown?: number;
  kelly_cap?:        number;
  [key: string]:     unknown;
}

/** GET /api/decision/status — DecisionEngine v2.5 */
export interface DecisionStatus {
  enabled:               boolean;
  status?:               string;
  entry_zscore?:         number | null;
  exit_zscore?:          number | null;
  partial_exit_zscore?:  number | null;
  scale_in_zscore?:      number | null;
  base_qty_y?:           number | null;
  base_qty_x?:           number | null;
  current_streak?:       number;
  current_drawdown?:     number;
  in_position?:          boolean;
}

export const getRiskSnapshot     = () => apiFetch<RiskSnapshot>('/risk/snapshot');
export const getPairsStatus      = () => apiFetch<PairStatus[]>('/pairs/status');
export const getEquityCurve      = () => apiFetch<EquityPoint[]>('/risk/equity_curve');
export const getRebalancerStatus = () => apiFetch('/rebalancer/status');
export const triggerRebalance    = () => apiFetch('/rebalancer/run', { method: 'POST' });

/** S46: Sizing + Decision endpoints pentru dashboard unificat */
export const getSizingLiveStatus = () => apiFetch<SizingLiveStatus>('/sizing/live_status');
export const getDecisionStatus   = () => apiFetch<DecisionStatus>('/api/decision/status');
