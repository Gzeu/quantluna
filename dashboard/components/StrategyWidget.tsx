/**
 * QuantLuna — StrategyWidget
 * Sprint 24
 *
 * Displays AutoSelector state:
 *   - Active strategy badge
 *   - Strategy scores bar chart (live polling)
 *   - Regime badge
 *   - Switch history timeline (last 5)
 *
 * Props:
 *   selectorId  — which selector to query (default: "live")
 *   pollInterval — ms between polls (default: 5000)
 */
import React, { useEffect, useState } from "react";
import { useStrategyScores } from "../hooks/useStrategyScores";

interface StrategyWidgetProps {
  selectorId?: string;
  pollInterval?: number;
}

const REGIME_COLORS: Record<string, string> = {
  ranging:  "#3b82f6",  // blue
  trending: "#10b981",  // green
  breakout: "#f59e0b",  // amber
  unknown:  "#6b7280",  // gray
  NORMAL:   "#3b82f6",
  HIGH_VOL: "#f97316",
  BREAKDOWN:"#ef4444",
  TRANSITION:"#a78bfa",
};

const STRATEGY_COLORS = ["#6366f1", "#10b981", "#f59e0b", "#ef4444", "#3b82f6"];

export const StrategyWidget: React.FC<StrategyWidgetProps> = ({
  selectorId = "live",
  pollInterval = 5000,
}) => {
  const { data, error, loading } = useStrategyScores(selectorId, pollInterval);

  if (loading) return <div className="ql-widget ql-widget--loading">Loading strategy…</div>;
  if (error)   return <div className="ql-widget ql-widget--error">Strategy unavailable</div>;
  if (!data)   return null;

  const scoreEntries = Object.entries(data.scores).sort(([, a], [, b]) => b - a);
  const maxScore = Math.max(...scoreEntries.map(([, v]) => v), 0.01);

  return (
    <div className="ql-widget">
      {/* Header */}
      <div className="ql-widget__header">
        <h3 className="ql-widget__title">AutoSelector</h3>
        <div className="ql-widget__badges">
          <span
            className="ql-badge ql-badge--strategy"
            title="Active strategy"
          >
            ● {data.active_strategy}
          </span>
        </div>
      </div>

      {/* Scores bar chart */}
      <div className="ql-widget__scores">
        {scoreEntries.map(([name, score], idx) => (
          <div key={name} className="ql-score-row">
            <span className="ql-score-row__name" title={name}>
              {name === data.active_strategy ? <strong>{name}</strong> : name}
            </span>
            <div className="ql-score-row__bar-bg">
              <div
                className="ql-score-row__bar"
                style={{
                  width: `${(score / maxScore) * 100}%`,
                  backgroundColor: STRATEGY_COLORS[idx % STRATEGY_COLORS.length],
                }}
              />
            </div>
            <span className="ql-score-row__value">{score.toFixed(3)}</span>
          </div>
        ))}
      </div>

      {/* Footer stats */}
      <div className="ql-widget__footer">
        <span className="ql-stat">
          Win rate: <strong>{(data.recent_win_rate * 100).toFixed(1)}%</strong>
        </span>
        <span className="ql-stat">
          Bars: <strong>{data.total_bars.toLocaleString()}</strong>
        </span>
        <span className="ql-stat">
          Switches: <strong>{data.switch_history.length}</strong>
        </span>
      </div>

      {/* Switch history timeline */}
      {data.switch_history.length > 0 && (
        <div className="ql-widget__timeline">
          <p className="ql-timeline__label">Recent switches</p>
          {[...data.switch_history].reverse().slice(0, 5).map((sw, i) => (
            <div key={i} className="ql-timeline__item">
              <span className="ql-timeline__from">{sw.from}</span>
              <span className="ql-timeline__arrow">→</span>
              <span className="ql-timeline__to">{sw.to}</span>
              {sw.manual && <span className="ql-timeline__manual">(manual)</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default StrategyWidget;
