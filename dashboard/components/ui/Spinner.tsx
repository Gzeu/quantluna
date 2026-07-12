/**
 * ui/Spinner.tsx — Loading spinner SVG
 * Size: sm (16px) | md (24px) | lg (40px)
 */
import React from 'react';

type Size = 'sm' | 'md' | 'lg';

const px: Record<Size, number> = { sm: 16, md: 24, lg: 40 };
const sw: Record<Size, number> = { sm: 2,  md: 2.5, lg: 3 };

export function Spinner({
  size = 'md', color = '#22d3ee', className = '',
}: {
  size?: Size; color?: string; className?: string;
}) {
  const s = px[size];
  const r = (s - sw[size] * 2) / 2;
  const c = s / 2;
  const circ = 2 * Math.PI * r;
  return (
    <svg
      width={s} height={s} viewBox={`0 0 ${s} ${s}`}
      className={`animate-spin ${className}`}
      style={{ flexShrink: 0 }}
      aria-label="Loading"
    >
      <circle cx={c} cy={c} r={r} fill="none"
        stroke="rgba(255,255,255,0.08)" strokeWidth={sw[size]} />
      <circle cx={c} cy={c} r={r} fill="none"
        stroke={color} strokeWidth={sw[size]}
        strokeDasharray={`${circ * 0.75} ${circ * 0.25}`}
        strokeLinecap="round"
        transform={`rotate(-90 ${c} ${c})`}
      />
    </svg>
  );
}
