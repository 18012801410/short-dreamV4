/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0f1115',
        surface: '#171a21',
        'surface-2': '#1f2430',
        line: '#2a3040',
        ink: '#e6e9ef',
        'ink-dim': '#9aa3b2',
        primary: { DEFAULT: '#5b8cff', hover: '#7aa2ff' },
        ok: '#3fb96f',
        warn: '#e6a23c',
        danger: '#e5534b',
        stale: '#8b7355',
      },
      fontFamily: {
        sans: ['system-ui', '"Segoe UI"', '"Microsoft YaHei"', 'sans-serif'],
        mono: ['"Cascadia Code"', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
