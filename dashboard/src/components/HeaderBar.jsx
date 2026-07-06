/*
 * HeaderBar — header sticky con el título de la ruta, refresh y (en móvil) tema + búsqueda.
 * El pill de conexión refleja el estado REAL del canal SSE (useRealtimeStatus): verde = recibe
 * eventos en vivo, ámbar = reconectando, gris = sin conexión. Mide el canal de tiempo real, no el
 * proceso del bot de Telegram (eso sería un heartbeat aparte, diferido).
 */
import { useLocation } from 'react-router-dom'
import { Command, RefreshCw, Sun, Moon } from 'lucide-react'
import { findRoute } from '@/routes.jsx'
import { useRealtimeStatus } from '@/components/RealtimeProvider.jsx'

const PILL = {
  conectado: { dot: 'bg-success animate-pulse', texto: 'En vivo' },
  conectando: { dot: 'bg-muted-foreground/50', texto: 'Conectando…' },
  reconectando: { dot: 'bg-warning animate-pulse', texto: 'Reconectando…' },
  'sin-conexion': { dot: 'bg-muted-foreground/40', texto: 'Sin conexión' },
}

export default function HeaderBar({ isMobile, onOpenCommand, onRefresh, lastRefresh, colorScheme, onToggleColorScheme }) {
  const { estado } = useRealtimeStatus()
  const pill = PILL[estado] || PILL.conectando
  const location = useLocation()
  const route = findRoute(location.pathname)
  const title = route?.label || 'Hoy'
  const isHoy = location.pathname === '/hoy' || location.pathname === '/'
  const fechaHoy = isHoy
    ? new Date().toLocaleDateString('es-CO', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'America/Bogota' })
    : null

  return (
    <header className="sticky top-0 z-30 bg-surface border-b border-border">
      <div className="flex items-center gap-3 h-14 px-4 md:px-6">
        <h1 className="text-base md:text-lg font-semibold tracking-tight truncate">{title}</h1>
        {fechaHoy && (
          <span className="hidden md:inline text-xs text-muted-foreground capitalize truncate">
            {fechaHoy}
          </span>
        )}

        <div className="flex-1" />

        {!isMobile && lastRefresh && (
          <span className="text-xs text-muted-foreground tabular hidden lg:inline">
            Actualizado {lastRefresh}
          </span>
        )}

        <button
          onClick={onRefresh}
          title="Refrescar"
          className="size-9 grid place-items-center rounded-md border border-border bg-surface text-muted-foreground hover:text-foreground hover:bg-surface-2 transition-colors"
        >
          <RefreshCw className="size-4" />
        </button>

        {isMobile && (
          <>
            <button
              onClick={onToggleColorScheme}
              title="Tema"
              className="size-9 grid place-items-center rounded-md border border-border bg-surface text-muted-foreground hover:text-foreground hover:bg-surface-2"
            >
              {colorScheme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
            <button
              onClick={onOpenCommand}
              title="Buscar"
              className="size-9 grid place-items-center rounded-md border border-border bg-surface text-muted-foreground hover:text-foreground hover:bg-surface-2"
            >
              <Command className="size-4" />
            </button>
          </>
        )}

        <div className="hidden md:flex items-center gap-2 px-3 h-9 rounded-md border border-border bg-surface text-caption"
          title="Estado del canal de tiempo real" aria-live="polite">
          <span className={`size-2 rounded-full ${pill.dot}`} />
          <span className="text-muted-foreground">{pill.texto}</span>
        </div>
      </div>
    </header>
  )
}
