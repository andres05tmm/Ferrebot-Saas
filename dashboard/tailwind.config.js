import animate from 'tailwindcss-animate'

/** @type {import('tailwindcss').Config} */
// Tokens derivados de .planning/dashboard-redesign/DESIGN.md (Fase 2 locked).
// Capa primitive aquí; capa semantic vive como CSS vars en src/index.css.
export default {
  darkMode: ['class', '[data-theme="dark"]'],
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    container: {
      center: true,
      padding: '1.5rem',
      screens: { '2xl': '1440px' },
    },
    extend: {
      colors: {
        // ── marca white-label (runtime) ────────────────────────────────────
        // Inyectada al boot desde GET /config (branding.color_primario); reemplaza el #C8200E fijo.
        'color-primary': 'var(--color-primary)',
        // ── capa semantic vía CSS vars ─────────────────────────────────────
        border:          'hsl(var(--border) / <alpha-value>)',
        'border-subtle': 'hsl(var(--border-subtle) / <alpha-value>)',
        'border-strong': 'hsl(var(--border-strong) / <alpha-value>)',
        input:           'hsl(var(--input) / <alpha-value>)',
        ring:            'hsl(var(--ring) / <alpha-value>)',
        background:      'hsl(var(--bg-body) / <alpha-value>)',
        foreground:      'hsl(var(--text-primary) / <alpha-value>)',
        surface: {
          DEFAULT:  'hsl(var(--bg-surface) / <alpha-value>)',
          2:        'hsl(var(--bg-surface-2) / <alpha-value>)',
          sidebar:  'hsl(var(--bg-sidebar) / <alpha-value>)',
        },
        muted: {
          DEFAULT:    'hsl(var(--bg-surface-2) / <alpha-value>)',
          foreground: 'hsl(var(--text-muted) / <alpha-value>)',
        },
        primary: {
          DEFAULT:    'hsl(var(--accent) / <alpha-value>)',
          hover:      'hsl(var(--accent-hover) / <alpha-value>)',
          // soft: alpha-tint del accent en lugar de --accent-soft (que en dark
          // colapsaba a mismo H/L que --accent → "red on red" invisible).
          // 0.15 alpha funciona en light (≈ pink) y dark (≈ red sutil sobre bg).
          soft:       'hsl(var(--accent) / 0.15)',
          foreground: 'hsl(var(--accent-on) / <alpha-value>)',
        },
        secondary: {
          DEFAULT:    'hsl(var(--bg-surface-2) / <alpha-value>)',
          foreground: 'hsl(var(--text-secondary) / <alpha-value>)',
        },
        destructive: {
          DEFAULT:    'hsl(var(--danger) / <alpha-value>)',
          foreground: 'hsl(var(--accent-on) / <alpha-value>)',
        },
        success: {
          DEFAULT:    'hsl(var(--success) / <alpha-value>)',
          foreground: 'hsl(var(--accent-on) / <alpha-value>)',
        },
        warning: {
          DEFAULT:    'hsl(var(--warning) / <alpha-value>)',
          foreground: 'hsl(var(--accent-on) / <alpha-value>)',
        },
        info: {
          DEFAULT:    'hsl(var(--info) / <alpha-value>)',
          foreground: 'hsl(var(--accent-on) / <alpha-value>)',
        },
        danger: {
          DEFAULT:    'hsl(var(--danger) / <alpha-value>)',
          foreground: 'hsl(var(--accent-on) / <alpha-value>)',
        },
        accent: {
          DEFAULT:    'hsl(var(--bg-surface-2) / <alpha-value>)',
          foreground: 'hsl(var(--text-primary) / <alpha-value>)',
        },
        popover: {
          DEFAULT:    'hsl(var(--bg-surface) / <alpha-value>)',
          foreground: 'hsl(var(--text-primary) / <alpha-value>)',
        },
        card: {
          DEFAULT:    'hsl(var(--bg-surface) / <alpha-value>)',
          foreground: 'hsl(var(--text-primary) / <alpha-value>)',
        },
        // ── Accent strips (top bars en KpiCard) ──────────────────────────
        'body-strong':   'hsl(var(--bg-body-strong) / <alpha-value>)',
        'accent-red':    'hsl(var(--accent) / <alpha-value>)',
        'accent-yellow': 'hsl(var(--accent-yellow) / <alpha-value>)',
        'accent-blue':   'hsl(var(--accent-blue) / <alpha-value>)',
        'accent-green':  'hsl(var(--accent-green) / <alpha-value>)',
        'accent-orange': 'hsl(var(--accent-orange) / <alpha-value>)',
        // ── capa primitive (brand red) — uso puntual ──────────────────────
        brand: {
          50:  '#FEF1EF',
          100: '#FCDBD6',
          200: '#F8B5AB',
          300: '#F08879',
          400: '#E25A47',
          500: '#C8200E',
          600: '#A01808',
          700: '#7A1206',
          800: '#570D04',
          900: '#3A0903',
        },
      },
      // Radio temable: la capa semantic (src/index.css) define --radius-* con los valores base de
      // siempre; un tema con nombre (p. ej. aurora) los sube (cards 16px). El base queda idéntico.
      borderRadius: {
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        xl: 'var(--radius-xl)',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        // Familia de títulos temable: base = Inter (idéntico a hoy); aurora la cambia a Nunito.
        display: ['var(--font-display)'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        // calibrado vs Stitch — ver DESIGN.md §4
        xs:    ['11px', { lineHeight: '1.4' }],
        sm:    ['13px', { lineHeight: '1.45' }],
        base:  ['14px', { lineHeight: '1.5' }],
        md:    ['16px', { lineHeight: '1.5' }],
        lg:    ['18px', { lineHeight: '1.4' }],
        xl:    ['22px', { lineHeight: '1.3' }],
        '2xl': ['28px', { lineHeight: '1.25' }],
        '3xl': ['32px', { lineHeight: '1.2' }],
        '4xl': ['40px', { lineHeight: '1.15', letterSpacing: '-0.02em' }],
      },
      letterSpacing: {
        tight:  '-0.02em',
        wide:   '0.06em',
        wider:  '0.12em',
      },
      // Sombra temable: --shadow-* en la capa semantic. Base = sombras neutras de siempre; aurora usa
      // la sombra suave teñida de teal de la propuesta. El base queda idéntico.
      boxShadow: {
        xs: 'var(--shadow-xs)',
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
      },
      transitionTimingFunction: {
        'out-quad': 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
      },
      transitionDuration: {
        fast: '120ms',
        base: '180ms',
        slow: '280ms',
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to:   { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to:   { height: '0' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 180ms ease-out',
        'accordion-up':   'accordion-up 180ms ease-out',
      },
    },
  },
  plugins: [animate],
}
