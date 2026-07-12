/**
 * dashboardSlice.ts — S37 (review fix: importă din types/ → elimina coupling circular)
 * Zustand persist slice: PnL history, watchdog cache, UI prefs.
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { PnlPoint, WatchdogStatus, WatchdogAlert } from '../types/dashboard';

const MAX_PNL_HISTORY = 500;

interface DashboardState {
  // PnL
  pnlHistory: PnlPoint[];
  appendPnl:  (point: PnlPoint) => void;
  clearPnl:   () => void;

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
      pnlHistory: [],
      appendPnl: (point) =>
        set((s) => ({ pnlHistory: [...s.pnlHistory.slice(-(MAX_PNL_HISTORY - 1)), point] })),
      clearPnl: () => set({ pnlHistory: [] }),

      watchdogStatus: null,
      watchdogAlerts: [],
      setWatchdog: (status, alerts) => set({ watchdogStatus: status, watchdogAlerts: alerts }),

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
