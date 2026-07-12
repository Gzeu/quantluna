/**
 * ui/Kbd.tsx — Keyboard key display
 * Folosit in tooltipuri shortcuturi si help modal
 */
import React from 'react';

export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="
      inline-flex items-center justify-center
      px-1.5 py-0.5 rounded-md
      bg-gray-800 border border-gray-700
      text-gray-300 text-[10px] font-mono font-medium
      shadow-[inset_0_-1px_0_rgba(0,0,0,0.5)]
    ">
      {children}
    </kbd>
  );
}
