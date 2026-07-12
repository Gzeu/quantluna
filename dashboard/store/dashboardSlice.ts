/**
 * dashboardSlice.ts — S37
 * Zustand store slice pentru:
 *   - PnL history (equity curve points)
 *   - Watchdog state cache
 *   - UI preferences (dark mode, sidebar collapsed)
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { PnlPoint } from '../hooks/usePnlStream';
import type { WatchdogStatus, WatchdogAlert } from '../hooks/useWatchdog';

const MAX_PNL_HISTORY = 500;

interface DashboardState {
  // PnL
  pnlHistory:    PnlPoint[];
  appendPnl:     (point: PnlPoint) => void;
  clearPnl:      () => void;

  // Watchdog cache
  watchdogStatus: WatchdogStatus | null;
  watchdogAlerts: WatchdogAlert[];
  setWatchdog:    (status: WatchdogStatus, alerts: WatchdogAlert[]) => void;

  // UI
  darkMode:         boolean;
  sidebarCollapsed: boolean;
  toggleDark:       () => void;
  toggleSidebar:    () => void;
}

export const useDashboardStore = create<DashboardState>()(
  persist(
    (set) => ({
      // PnL
      pnlHistory: [],
      appendPnl: (point) =>
        set((s) => ({
          pnlHistory: [...s.pnlHistory.slice(-(MAX_PNL_HISTORY - 1)), point],
        })),
      clearPnl: () => set({ pnlHistory: [] }),

      // Watchdog
      watchdogStatus: null,
      watchdogAlerts: [],
      setWatchdog: (status, alerts) => set({ watchdogStatus: status, watchdogAlerts: alerts }),

      // UI
      darkMode:         true,
      sidebarCollapsed: false,
      toggleDark:    () => set((s) => ({ darkMode:         !s.darkMode })),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
    }),
    {
      name: 'quantluna-dashboard',
      partialize: (s) => ({ darkMode: s.darkMode, sidebarCollapsed: s.sidebarCollapsed }),
    },
  ),
);
