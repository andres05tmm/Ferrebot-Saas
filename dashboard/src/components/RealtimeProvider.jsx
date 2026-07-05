/*
 * RealtimeProvider — dueño de la ÚNICA conexión SSE del dashboard (useRealtime de E5).
 *
 * Abrir un stream por tab montado multiplicaría conexiones (el backend tiene límite de pool). Por eso
 * el shell monta UN solo RealtimeProvider y los tabs se suscriben con useRealtimeEvent(tipos, handler)
 * — sin abrir su propio stream. El provider despacha cada evento a los suscriptores cuyo filtro de
 * `tipos` coincida (o que escuchen todo, tipos = null), y muestra el toast en 'reconnected'.
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { useRealtime } from '../hooks/useRealtime.js'

const RealtimeContext = createContext(null)
const StatusContext = createContext({ estado: 'conectando' })

export function RealtimeProvider({ children }) {
  const subsRef = useRef(new Set())
  // Estado REAL del canal SSE (no del proceso del bot): 'conectando' | 'conectado' | 'reconectando'
  // | 'sin-conexion'. Cambia poco (solo en open/error), así que el re-render del provider es raro.
  const [estado, setEstado] = useState('conectando')

  const onEvent = useCallback((tipo, data) => {
    if (tipo === '__estado') { setEstado(data.estado); return }   // señal interna, no va a los tabs
    if (tipo === 'reconnected') toast.success('Conexión restablecida')
    for (const sub of subsRef.current) {
      if (sub.tipos === null || sub.tipos.has(tipo)) sub.handler(tipo, data)
    }
  }, [])

  useRealtime(onEvent) // ← la única suscripción al stream

  const subscribe = useCallback((sub) => {
    subsRef.current.add(sub)
    return () => { subsRef.current.delete(sub) }
  }, [])

  return (
    <StatusContext.Provider value={{ estado }}>
      <RealtimeContext.Provider value={subscribe}>{children}</RealtimeContext.Provider>
    </StatusContext.Provider>
  )
}

/**
 * useRealtimeStatus() — estado del canal SSE compartido para indicadores de conexión (el pill del
 * header). Mide el CANAL de tiempo real, no el proceso del bot de Telegram (eso sería un heartbeat
 * aparte). 'conectado' = el dashboard recibe eventos en vivo.
 */
export function useRealtimeStatus() {
  return useContext(StatusContext)
}

/**
 * useRealtimeEvent(tipos, handler) — suscribe un tab a eventos del stream compartido.
 * `tipos`: string | string[] | null (null = todos). `handler(tipo, data)` corre en cada match.
 * El handler vive en un ref: no re-suscribe en cada render.
 */
export function useRealtimeEvent(tipos, handler) {
  const subscribe = useContext(RealtimeContext)
  const handlerRef = useRef(handler)
  useEffect(() => { handlerRef.current = handler })

  const lista = tipos == null ? null : (Array.isArray(tipos) ? tipos : [tipos])
  const clave = lista == null ? '*' : [...lista].sort().join(',')

  useEffect(() => {
    if (!subscribe) return undefined
    const sub = {
      tipos: lista == null ? null : new Set(lista),
      handler: (t, d) => handlerRef.current?.(t, d),
    }
    return subscribe(sub)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscribe, clave])
}
