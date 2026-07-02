import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: '#6366f1',  // indigo-500
          dark:    '#4f46e5',
          light:   '#a5b4fc',
        },
        success:  '#22c55e',
        warning:  '#f59e0b',
        danger:   '#ef4444',
        surface:  '#0f172a',
        card:     '#1e293b',
        border:   '#334155',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Mono', 'monospace'],
      },
    },
  },
  plugins: [],
};

export default config;
