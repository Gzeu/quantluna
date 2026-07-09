import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './store/**/*.{js,ts,jsx,tsx}',
    './hooks/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        bg:      '#08080F',
        panel:   '#0D0D1A',
        border:  '#1A1A3E',
        green:   '#00FF88',
        magenta: '#FF00AA',
        blue:    '#0088FF',
        yellow:  '#FFAA00',
        red:     '#FF2244',
        text:    '#E0E0F0',
        text2:   '#666688',
        violet:  '#8844FF',
        orange:  '#FF6600',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Courier New', 'monospace'],
      },
      fontSize: {
        '2xs': '9px',
        'xs':  '10px',
        'sm':  '11px',
        'base':'12px',
      },
      animation: {
        'pulse-border-green': 'pulse-border-green 2s ease-in-out infinite',
        'pulse-border-red':   'pulse-border-red 2s ease-in-out infinite',
        'arb-pulse':          'arb-pulse 1s ease-in-out infinite',
        'flash-profit':       'flash-green 0.6s ease-out',
        'flash-loss':         'flash-red 0.6s ease-out',
        'extreme-pulse':      'extreme-pulse 0.8s ease-in-out infinite',
        'fade-in-row':        'fade-in-row 0.2s ease-out',
      },
    },
  },
  plugins: [],
}

export default config
