/**
 * QuantLuna — LiveTraderWidget
 * Sprint 24
 *
 * Displays live trader status via SSE stream (/live/stream).
 * Shows: state, mode, active strategy, regime, P&L, position, uptime.
 *
 * Props:
 *   apiBase — API base URL (default: "")
 */
import React, { useEffect, useRef, useState } from "react";
import { useLiveStream } from "../hooks/useLiveStream";

interface LiveTraderWidgetProps {
  apiBase?: string;
}

const STATE_COLORS: Record<string, string> = {
  running:  "#10b981",
  stopping: "#f59e0b",
  stopped:  "#6b7280",
  idle:     "#6b7280",
  error:    "#ef4444",
};

const SIDE_LABEL: Record<number, string> = { 1: "▲ LONG", "-1": "▼ SHORT", 0: "— FLAT" };

export const LiveTraderWidget: React.FC<LiveTraderWidgetProps> = ({ apiBase = "" }) => {
  const { status, lastBar, connected } = useLiveStream(apiBase);

  return (
    <div className="ql-widget">
      <div className="ql-widget__header">
        <h3 className="ql-widget__title">Live Trader</h3>
        <span
          className="ql-badge"
          style={{ backgroundColor: STATE_COLORS[status?.state ?? "idle"] }}
        >
          {connected ? status?.state ?? "idle" : "disconnected"}
        </span>
      </div>

      {status ? (
        <>
          {/* Pair + mode */}
          <div className="ql-live__meta">
            <span>{status.sym_y}/{status.sym_x}</span>
            <span className="ql-live__mode">{status.mode}</span>
            <span>{status.bar_freq}</span>
          </div>

          {/* P&L row */}
          <div className="ql-live__pnl">
            <div className="ql-stat">
              <span>Realised</span>
              <strong style={{ color: status.realised_pnl >= 0 ? "#10b981" : "#ef4444" }}>
                {status.realised_pnl >= 0 ? "+" : ""}{status.realised_pnl.toFixed(4)} USDT
              </strong>
            </div>
            <div className="ql-stat">
              <span>Unrealised</span>
              <strong style={{ color: status.unrealised_pnl >= 0 ? "#10b981" : "#ef4444" }}>
                {status.unrealised_pnl >= 0 ? "+" : ""}{status.unrealised_pnl.toFixed(4)} USDT
              </strong>
            </div>
          </div>

          {/* Strategy + regime */}
          <div className="ql-live__strategy">
            <span className="ql-stat">Strategy: <strong>{status.active_strategy}</strong></span>
            <span className="ql-stat">Regime: <strong>{status.regime}</strong></span>
            <span className="ql-stat">
              Position: <strong>{SIDE_LABEL[status.position_side] ?? "—"}</strong>
            </span>
          </div>

          {/* Stats row */}
          <div className="ql-live__stats">
            <span className="ql-stat">Trades: <strong>{status.n_trades}</strong></span>
            <span className="ql-stat">Bars: <strong>{status.bars_processed}</strong></span>
            <span className="ql-stat">Uptime: <strong>{Math.round(status.uptime_s)}s</strong></span>
          </div>

          {/* Last bar */}
          {lastBar && (
            <div className="ql-live__last-bar">
              <span className="ql-stat">Last bar: {new Date(lastBar.ts).toLocaleTimeString()}</span>
              <span className="ql-stat">Spread: {lastBar.spread?.toFixed(4)}</span>
              <span className="ql-stat">Z: {lastBar.zscore?.toFixed(2)}</span>
            </div>
          )}
        </>
      ) : (
        <p className="ql-live__idle">
          No trader running.
          <br />
          POST /live/start to begin paper trading.
        </p>
      )}
    </div>
  );
};

export default LiveTraderWidget;
