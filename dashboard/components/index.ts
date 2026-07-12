/**
 * components/index.ts — barrel export toate componentele QuantLuna
 */
export { default as NavBar }          from './NavBar';
export { StatsBar }                   from './StatsBar';
export { MetricsBadge }               from './MetricsBadge';
export { PnlChart }                   from './PnlChart';
export { TradeBreakdown }             from './TradeBreakdown';
export { StrategyScores }             from './StrategyScores';
export { WatchdogPanel }              from './WatchdogPanel';
export { BalanceTracker }             from './BalanceTracker';
export { ArbitragePanel }             from './ArbitragePanel';
export { SpreadMonitorPanel }         from './SpreadMonitorPanel';
export { ExecutionLog }               from './ExecutionLog';
export { MarketHeatmap }              from './MarketHeatmap';
export { CandlestickChart }           from './CandlestickChart';
export { ErrorBoundary }              from './ErrorBoundary';
export { ToastContainer, useToast }   from './Toast';

/* UI primitives */
export { Card, Badge, Spinner, Kbd }  from './ui';

/* Modals */
export {
  ConfirmModal,
  SettingsModal,
  ShortcutsModal,
  CircuitBreakerModal,
  OrderManagerModal,
  ModalsHost,
} from './modals';
