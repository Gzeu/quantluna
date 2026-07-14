/**
 * components/index.ts — barrel export toate componentele QuantLuna
 */
export { default as NavBar }          from './NavBar';
export { StatsBar }                   from './StatsBar';
export { MetricsBadge }               from './MetricsBadge';
export { PnlChart }                   from './PnlChart';
export { DrawdownChart }              from './DrawdownChart';
export { ThresholdEditor }            from './ThresholdEditor';
export { TradeBreakdown }             from './TradeBreakdown';
export { StrategyScores }             from './StrategyScores';
export { WatchdogPanel }              from './WatchdogPanel';
export { default as BalanceTracker }     from './BalanceTracker';
export { default as ArbitragePanel }     from './ArbitragePanel';
export { default as SpreadMonitorPanel } from './SpreadMonitorPanel';
export { default as ExecutionLog }       from './ExecutionLog';
export { default as MarketHeatmap }      from './MarketHeatmap';
export { default as CandlestickChart }   from './CandlestickChart';
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
