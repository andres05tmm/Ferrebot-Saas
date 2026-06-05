/*
 * HeaderBar — header sticky con el título de la ruta, refresh y (en móvil) tema + búsqueda.
 * Andamiaje E3: se quitaron el selector de vendedor y el pill de caja (dependían de auth/datos);
 * vuelven en E4/E6. El pill "Bot activo" queda estático por ahora.
 */
import { useLocation } from 'react-router-dom'
import { Command, RefreshCw, Sun, Moon } from 'lucide-react'
import { findRoute } from '@/routes.jsx'

export default function HeaderBar({ isMobile, onOpenCommand, onRefresh, lastRefresh, colorScheme, onToggleColorScheme }) {
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

        <div className="hidden md:flex items-center gap-2 px-3 h-9 rounded-md border border-border bg-surface text-xs">
          <span className="size-2 rounded-full bg-success animate-pulse" />
          <span className="text-muted-foreground">Bot activo</span>
        </div>
      </div>
    </header>
  )
}
