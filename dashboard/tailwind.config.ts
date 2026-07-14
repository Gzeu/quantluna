import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './store/**/*.{js,ts,jsx,tsx}',
    './hooks/**/*.{js,ts,jsx,tsx}',
    './pages/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        bg:       '#080812',
        surface:  '#0C0C1A',
        elevated: '#111128',
        card:     '#151530',
        border:   'rgba(255,255,255,0.07)',
        purple:   { DEFAULT: '#8B5CF6', bright: '#A78BFA', dim: '#5B21B6' },
        cyan:     { DEFAULT: '#22D3EE', bright: '#67E8F9', dim: '#0E7490' },
        green:    { DEFAULT: '#34D399', bright: '#6EE7B7', dim: '#065F46' },
        red:      { DEFAULT: '#F87171', bright: '#FCA5A5', dim: '#7F1D1D' },
        yellow:   { DEFAULT: '#FBBF24', dim: '#78350F' },
        orange:   { DEFAULT: '#FB923C' },
        pink:     { DEFAULT: '#F472B6' },
        text:     { primary: '#E8E8F0', secondary: '#9898B8', muted: '#585878' },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Cascadia Code', 'ui-monospace', 'monospace'],
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
      },
      fontSize: {
        '3xs':  '8px',
        '2xs':  '9.5px',
        'xs':   '11px',
        'sm':   '12px',
        'base': '13px',
        'lg':   '14px',
      },
      borderRadius: {
        'sm': '10px',
        'md': '16px',
        'lg': '24px',
        'xl': '32px',
      },
      boxShadow: {
        'card':   '0 2px 8px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.2)',
        'glow-p': '0 0 30px rgba(139,92,246,0.12)',
        'glow-c': '0 0 30px rgba(34,211,238,0.1)',
        'glow-g': '0 0 20px rgba(52,211,153,0.12)',
        'glow-r': '0 0 20px rgba(248,113,113,0.15)',
      },
      animation: {
        'fade-in':        'fade-in 0.35s ease-out both',
        'fade-up':        'fade-up 0.3s ease-out both',
        'fade-scale':     'fade-scale 0.3s ease-out both',
        'glow':           'glow-pulse 2.5s ease-in-out infinite',
        'border-glow':    'border-glow 3s ease-in-out infinite',
        'live-pulse':     'live-pulse 1.5s ease-in-out infinite',
        'float':          'float 3s ease-in-out infinite',
        'number-pop':     'number-pop 0.4s ease-out',
        'gradient-shift': 'gradient-shift 4s ease infinite',
        'spin-slow':      'spin 2s linear infinite',
        'ping-once':      'ping-once 0.8s ease-out forwards',
      },
    },
  },
  plugins: [],
}

export default config
