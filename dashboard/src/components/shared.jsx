// ── shared.jsx — helpers transversales del dashboard ─────────────────────────
// Andamiaje E3: formatters, detector móvil y componentes de estado tokenizados.
// El `useFetch` original (acoplado a useAuth) se reintroduce en E6 sobre lib/api.js.
import { useState, useEffect } from 'react'

// ── FORMATTERS ───────────────────────────────────────────────────────────────
export function cop(val) {
  if (val === null || val === undefined || isNaN(val)) return '$0'
  return '$' + Math.round(val).toLocaleString('es-CO')
}

export function num(n) {
  if (n === null || n === undefined) return '0'
  return Number(n).toLocaleString('es-CO', { maximumFractionDigits: 2 })
}

// ── SPINNER / ERRORMSG — tokenizados (consumers transversales) ───────────────
export function Spinner() {
  return (
    <div className="flex flex-col items-center justify-center py-12 gap-3 text-muted-foreground">
      <div
        className="size-7 rounded-full border-2 border-border animate-spin"
        style={{ borderTopColor: 'hsl(var(--accent))' }}
      />
      <span className="text-xs tracking-wide">Cargando…</span>
    </div>
  )
}

export function ErrorMsg({ msg }) {
  return (
    <div className="bg-destructive/10 border border-destructive/40 rounded-md px-4 py-3 text-sm text-destructive flex items-center gap-2">
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="size-4 flex-shrink-0"
      >
        <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
        <line x1="12" x2="12" y1="9" y2="13" />
        <line x1="12" x2="12.01" y1="17" y2="17" />
      </svg>
      <span>{msg}</span>
    </div>
  )
}

// ── ProductThumb — miniatura cuadrada con fallback a iniciales ───────────────
export function ProductThumb({ src, nombre, size = 32, className = '' }) {
  const iniciales = String(nombre || '?')
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map(w => w[0]?.toUpperCase() || '')
    .join('') || '?'

  if (src) {
    return (
      <img
        src={src}
        alt={nombre || ''}
        className={`rounded-sm object-cover shrink-0 ${className}`}
        style={{ width: size, height: size }}
      />
    )
  }

  return (
    <span
      aria-hidden="true"
      className={`grid place-items-center rounded-sm bg-surface-2 text-muted-foreground font-semibold shrink-0 ${className}`}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.36) }}
    >
      {iniciales}
    </span>
  )
}

// ── useIsMobile — media query (max-width: 767px) con listener ────────────────
export function useIsMobile() {
  const mq = typeof window !== 'undefined'
    ? window.matchMedia('(max-width: 767px)')
    : null

  const [v, setV] = useState(() => mq ? mq.matches : false)

  useEffect(() => {
    if (!mq) return
    const fn = (e) => setV(e.matches)

    if (mq.addEventListener) {
      mq.addEventListener('change', fn)
    } else {
      mq.addListener(fn)
    }

    const onResize = () => setV(window.matchMedia('(max-width: 767px)').matches)
    window.addEventListener('orientationchange', onResize)
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', onResize)
    }

    setV(window.matchMedia('(max-width: 767px)').matches)

    return () => {
      if (mq.removeEventListener) {
        mq.removeEventListener('change', fn)
      } else {
        mq.removeListener(fn)
      }
      window.removeEventListener('orientationchange', onResize)
      if (window.visualViewport) {
        window.visualViewport.removeEventListener('resize', onResize)
      }
    }
  }, [])

  return v
}
