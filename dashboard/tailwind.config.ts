import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './hooks/**/*.{js,ts,jsx,tsx}',
    './lib/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: '#08080F',
          panel:   '#0D0D1A',
          border:  '#1A1A3E',
        },
        neon: {
          green:   '#00FF88',
          magenta: '#FF00AA',
          blue:    '#0088FF',
        },
        alert: {
          warn:   '#FFAA00',
          danger: '#FF2244',
        },
        text: {
          primary: '#E0E0F0',
          muted:   '#666688',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      keyframes: {
        pulse: {
          '0%, 100%': { opacity: '1' },
          '50%':       { opacity: '0.4' },
        },
      },
      animation: {
        pulse: 'pulse 2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}

export default config
