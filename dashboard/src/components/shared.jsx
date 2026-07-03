// ── shared.jsx — helpers transversales del dashboard ─────────────────────────
// Formatters, detector móvil, componentes de estado tokenizados y el hook de datos
// `useFetch` (reintroducido en E6 sobre lib/api.js: Bearer + X-Tenant-Slug centralizados).
import { useCallback, useEffect, useState } from 'react'
import { apiJson } from '@/lib/api'

// ── useFetch — GET por api.js con estado loading/error y refetch() ───────────
// `deps` controla cuándo re-pedir (p. ej. [refreshKey] del shell). Los tabs llaman a refetch()
// ante eventos SSE (useRealtimeEvent). El 401 lo maneja api.js (redirige a /login).
// Un `path` falsy (null/'') desactiva el fetch: queda en reposo (data null, sin loading) — útil para
// pedir un endpoint solo cuando la feature del tenant lo habilita, sin llamadas que darían 403/404.
export function useFetch(path, deps = []) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(!!path)
  const [error, setError] = useState(null)
  const [tick, setTick] = useState(0)
  const refetch = useCallback(() => setTick(t => t + 1), [])

  useEffect(() => {
    if (!path) { setLoading(false); setError(null); return }
    let cancelado = false
    setLoading(true)
    setError(null)
    apiJson(path)
      .then(d => { if (!cancelado) { setData(d); setLoading(false) } })
      .catch(e => { if (!cancelado) { setError(e.message); setLoading(false) } })
    return () => { cancelado = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, ...deps])

  return { data, loading, error, refetch }
}

// ── FORMATTERS ───────────────────────────────────────────────────────────────
export function cop(val) {
  if (val === null || val === undefined || isNaN(val)) return '$0'
  return '$' + Math.round(val).toLocaleString('es-CO')
}

export function num(n) {
  if (n === null || n === undefined) return '0'
  return Number(n).toLocaleString('es-CO', { maximumFractionDigits: 2 })
}

// Ventana [00:00, 23:59:59] de HOY en hora Colombia (UTC-5 fijo, sin DST) como ISO con offset.
// Para filtrar endpoints por fecha sin ambigüedad de zona (regla #4).
export function rangoHoyCO() {
  const ymd = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' }) // YYYY-MM-DD
  return { desde: `${ymd}T00:00:00-05:00`, hasta: `${ymd}T23:59:59-05:00` }
}

// Rango del MES en curso en hora Colombia como fechas YYYY-MM-DD (primer día → hoy). Para los
// endpoints de reportes (?desde&hasta tipo date). Default de las pestañas Resultados / Top productos.
export function mesActualCO() {
  const hoy = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' }) // YYYY-MM-DD
  return { desde: `${hoy.slice(0, 8)}01`, hasta: hoy }
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
