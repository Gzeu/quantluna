/**
 * ui/Badge.tsx — Badge / pill QuantLuna
 * Variante: green | yellow | red | cyan | purple | gray
 * Props: dot (indicator), pulse (animatie)
 */
import React from 'react';

type Variant = 'green' | 'yellow' | 'red' | 'cyan' | 'purple' | 'gray';
type Size    = 'sm' | 'md';

interface BadgeProps {
  variant?:   Variant;
  size?:      Size;
  dot?:       boolean;
  pulse?:     boolean;
  className?: string;
  children:   React.ReactNode;
}

const styles: Record<Variant, string> = {
  green:  'bg-green-900/60  text-green-300  border-green-800/50',
  yellow: 'bg-yellow-900/60 text-yellow-300 border-yellow-800/50',
  red:    'bg-red-900/60    text-red-300    border-red-800/50',
  cyan:   'bg-cyan-900/60   text-cyan-300   border-cyan-800/50',
  purple: 'bg-purple-900/60 text-purple-300 border-purple-800/50',
  gray:   'bg-gray-800/80   text-gray-400   border-gray-700/50',
};

const dotColors: Record<Variant, string> = {
  green:  'bg-green-400',
  yellow: 'bg-yellow-400',
  red:    'bg-red-400',
  cyan:   'bg-cyan-400',
  purple: 'bg-purple-400',
  gray:   'bg-gray-500',
};

const sizes: Record<Size, string> = {
  sm: 'text-[10px] px-1.5 py-0.5',
  md: 'text-xs px-2.5 py-1',
};

export function Badge({
  variant = 'gray', size = 'sm', dot = false, pulse = false,
  className = '', children,
}: BadgeProps) {
  return (
    <span className={`
      inline-flex items-center gap-1.5
      font-semibold tracking-wide uppercase
      border rounded-full
      ${styles[variant]} ${sizes[size]} ${className}
    `}>
      {dot && (
        <span className={`
          w-1.5 h-1.5 rounded-full shrink-0
          ${dotColors[variant]}
          ${pulse ? 'animate-pulse' : ''}
        `} />
      )}
      {children}
    </span>
  );
}
