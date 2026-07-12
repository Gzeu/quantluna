/**
 * types/dashboard.ts — S37 metrics expansion
 * Toate tipurile centralizate. Un singur loc de adevăr.
 */

export interface PnlPoint {
  ts:       number;
  equity:   number;
  net_pnl?: number;
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

/** Metrici per-pair pentru TradeBreakdown */
export interface PairBreakdown {
  pair:         string;
  wins:         number;
  losses:       number;
  total_trades: number;
  win_rate:     number;   // 0-1
  total_pnl:    number;   // USD
  avg_pnl:      number;   // USD per trade
  avg_win:      number;   // USD
  avg_loss:     number;   // USD (pozitiv = pierdere)
  max_loss:     number;   // USD (worst single trade)
  active:       boolean;
}

/** Răspuns complet de la /risk/dashboard */
export interface RiskMetrics {
  // Existente
  rolling_sharpe:    number;
  drawdown_current:  number;
  win_rate:          number;
  exposure_usd:      number;
  equity_usd:        number;

  // Noi — trade statistics
  wins:              number;   // trade-uri câștigătoare total
  losses:            number;   // trade-uri pierzătoare total
  total_trades:      number;
  avg_win_usd:       number;   // câștig mediu per trade câștigat
  avg_loss_usd:      number;   // pierdere medie per trade pierdut (valoare pozitivă)
  profit_factor:     number;   // gross profit / gross loss
  max_drawdown:      number;   // max DD istoric 0-1
  max_consecutive_wins:   number;
  max_consecutive_losses: number;
  current_streak:    number;   // pozitiv = wins, negativ = losses
  unrealized_pnl:   number;   // USD
  daily_pnl:        number;   // USD
  daily_pct:        number;   // 0-1

  // Per-pair breakdown (opțional — poate lipsi din backend vechi)
  pair_breakdown?: PairBreakdown[];
}
