"use client"
import React from 'react';

export default function SizingPanel() {
  return (
    <div style={{ padding: 16, background: 'var(--card-bg)', borderRadius: 8, border: '1px solid var(--border)' }}>
      <h3 style={{ margin: 0, fontSize: 14, color: 'var(--text-muted)' }}>
        Sizing Panel
      </h3>
      <p style={{ margin: 8, fontSize: 12, color: 'var(--text-muted)' }}>
        Position sizing controls will appear here
      </p>
    </div>
  );
}
