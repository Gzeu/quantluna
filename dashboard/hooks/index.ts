/**
 * hooks/index.ts — barrel export toate hook-urile QuantLuna
 */
export { useQuantLunaWS }    from './useQuantLunaWS';
export { useRiskMetrics }    from './useRiskMetrics';
export { useServices }       from './useServices';
export type { ServiceInfo }  from './useServices';
export { useOptimizer }      from './useOptimizer';
export { useBacktest }       from './useBacktest';
export type { BacktestParams, BacktestResult } from './useBacktest';
export { useStrategyScores } from './useStrategyScores';
export { useWatchdog }       from './useWatchdog';
export { useLiveStream }     from './useLiveStream';
export { usePnlStream }      from './usePnlStream';
export { useRealtimeData }   from './useRealtimeData';
export { useKeyboardShortcuts } from './useKeyboardShortcuts';
