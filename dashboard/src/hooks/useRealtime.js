/*
 * useRealtime — stream SSE de la empresa (GET /api/v1/events) por fetch, no EventSource nativo.
 *
 * EventSource no admite headers custom; usamos @microsoft/fetch-event-source para que el stream
 * viaje con los MISMOS headers que api.js (Authorization: Bearer + X-Tenant-Slug en dev) vía
 * buildAuthHeaders(). El backend emite frames `data: {"event": <tipo>, "data": {...}}` (ver
 * core/events/publisher.py) y keepalives `event: ping`; aquí se parsea a onEvent(tipo, data).
 *
 * - Sin token → no conecta (evita loop). Token claramente expirado → limpiarSesion()+/login.
 * - 401 al abrir el stream → limpiarSesion()+/login y NO reintentar (error fatal).
 * - Reconexión con backoff exponencial (2s→…→30s) y evento sintético 'reconnected' en cada
 *   reapertura que NO sea la primera, para que el consumer haga re-fetch del gap.
 */
import { useEffect, useRef } from 'react'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { buildAuthHeaders, getToken, limpiarSesion, redirector } from '@/lib/api'

const EVENTS_URL = '/api/v1/events'
const BACKOFF_MAX = 30_000

// Error que detiene el retry de fetchEventSource (lanzarlo en onopen/onerror corta el reintento).
class FatalSSEError extends Error {}

function tokenExpirado(token) {
  try {
    const { exp } = JSON.parse(atob(token.split('.')[1]))
    return typeof exp === 'number' && exp < Date.now() / 1000
  } catch {
    return false // no es un JWT decodificable (p. ej. token de prueba) → que el servidor decida
  }
}

function cerrarSesion() {
  limpiarSesion()
  redirector.toLogin()
}

export function useRealtime(onEvent) {
  // El callback vive en un ref: el efecto de conexión no se re-suscribe en cada render del padre.
  const onEventRef = useRef(onEvent)
  useEffect(() => { onEventRef.current = onEvent })

  useEffect(() => {
    const token = getToken()
    if (!token) { onEventRef.current?.('__estado', { estado: 'sin-conexion' }); return }
    if (tokenExpirado(token)) { cerrarSesion(); return }

    const ctrl = new AbortController()
    let primeraApertura = true
    let reintentos = 0

    fetchEventSource(EVENTS_URL, {
      signal: ctrl.signal,
      headers: buildAuthHeaders(),
      openWhenHidden: true, // no cortar el stream si la pestaña pasa a segundo plano
      async onopen(res) {
        if (res.status === 401) {
          cerrarSesion()
          throw new FatalSSEError('sesión expirada') // no reintentar
        }
        if (!res.ok) throw new Error(`SSE ${res.status}`) // reintentable
        if (!primeraApertura) onEventRef.current?.('reconnected', {})
        primeraApertura = false
        reintentos = 0
        onEventRef.current?.('__estado', { estado: 'conectado' })
      },
      onmessage(ev) {
        if (ev.event === 'ping' || !ev.data) return // keepalive
        try {
          const { event, data } = JSON.parse(ev.data)
          onEventRef.current?.(event, data)
        } catch {
          // frame malformado → ignorar
        }
      },
      onerror(err) {
        if (err instanceof FatalSSEError) throw err // propaga → detiene el retry
        onEventRef.current?.('__estado', { estado: 'reconectando' })
        const delay = Math.min(2000 * 2 ** reintentos, BACKOFF_MAX)
        reintentos++
        return delay // backoff exponencial
      },
    }).catch(() => { /* abortado (cleanup) o fatal: silencioso */ })

    return () => ctrl.abort()
  }, [])
}
