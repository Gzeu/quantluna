/**
 * types/dashboard.ts — S37 review fix
 * Tipuri centralizate folosite în hooks, store și componente.
 * Un singur loc de adevăr — nu mai există re-definiri duplicate.
 */

export interface PnlPoint {
  ts:       number;   // epoch ms
  equity:   number;   // USD
  net_pnl?: number;   // USD, opțional
}

export interface WatchdogStatus {
  enabled:      boolean;
  alerts_total: number;
  halted_pairs: string[];
  [k: string]:  unknown;
}

export interface WatchdogAlert {
  ts?:      string;
  level?:   'critical' | 'warning' | 'info' | string;
  message?: string;
  [k: string]: unknown;
}

export interface PairScore {
  pair:         string;
  strategy:     string;
  score:        number;
  sharpe:       number;
  win_rate:     number;
  total_trades: number;
  active:       boolean;
}

export interface RiskMetrics {
  rolling_sharpe:    number;
  drawdown_current:  number;
  win_rate:          number;
  exposure_usd:      number;
  equity_usd:        number;
}
