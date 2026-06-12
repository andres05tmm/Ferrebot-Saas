import animate from 'tailwindcss-animate'

/** @type {import('tailwindcss').Config} */
// Capa de utilidades sobre los tokens de marca (marca/tokens.css). Los nombres shadcn
// (background/foreground/primary/...) se mapean a los mismos vars para que los componentes
// del registry (21st.dev) funcionen sin retocar clases.
export default {
  darkMode: ['selector', '[data-tema="oscuro"]', 'class'],
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // ── marca ──────────────────────────────────────────────
        papel: 'var(--papel)',
        tinta: 'var(--tinta)',
        oro: {
          DEFAULT: 'var(--oro)',
          claro: 'var(--oro-claro)',
          oscuro: 'var(--oro-oscuro)',
          vivo: 'var(--oro-vivo)', // oro legible según tema
        },
        // ── tema (claro/oscuro via data-tema) ──────────────────
        fondo: { DEFAULT: 'var(--fondo)', 2: 'var(--fondo-2)' },
        panel: 'var(--panel)',
        linea: 'var(--linea)',
        texto: { DEFAULT: 'var(--texto)', 2: 'var(--texto-2)', 3: 'var(--texto-3)' },
        acento: {
          DEFAULT: 'var(--acento)',
          suave: 'var(--acento-suave)',
          sobre: 'var(--sobre-acento)',
        },
        wa: 'var(--wa)',
        // ── alias shadcn para componentes del registry ─────────
        background: 'var(--fondo)',
        foreground: 'var(--texto)',
        border: 'var(--linea)',
        input: 'var(--linea)',
        ring: 'var(--acento)',
        primary: { DEFAULT: 'var(--acento)', foreground: 'var(--sobre-acento)' },
        secondary: { DEFAULT: 'var(--fondo-2)', foreground: 'var(--texto-2)' },
        muted: { DEFAULT: 'var(--fondo-2)', foreground: 'var(--texto-2)' },
        accent: { DEFAULT: 'var(--fondo-2)', foreground: 'var(--texto)' },
        card: { DEFAULT: 'var(--panel)', foreground: 'var(--texto)' },
        popover: { DEFAULT: 'var(--panel)', foreground: 'var(--texto)' },
        destructive: { DEFAULT: 'oklch(55% .19 29)', foreground: 'var(--papel)' },
      },
      fontFamily: {
        display: 'var(--fuente-display)',
        sans: 'var(--fuente-cuerpo)',
      },
      transitionTimingFunction: {
        marca: 'cubic-bezier(.16, 1, .3, 1)',
      },
      boxShadow: {
        marca: 'var(--sombra)',
      },
      borderRadius: {
        lg: '0.75rem',
        xl: '1rem',
        '2xl': '1.5rem',
      },
    },
  },
  plugins: [animate],
}
