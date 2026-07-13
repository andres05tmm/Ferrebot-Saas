/*
 * Sidebar — navegación primaria del shell desktop (≥768px).
 * Ancho 240px, colapsable a 64px (atajo [ o botón). Persiste estado y grupos en localStorage.
 * Los ítems se filtran por las features de la empresa (gating de GET /config).
 */
import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { ChevronDown, ChevronRight, PanelLeftClose, PanelLeftOpen, Command, Sun, Moon } from 'lucide-react'
import { ROUTES, groupsFor, routesByGroup } from '@/routes.jsx'
import { useFeatures, isRouteEnabled } from '@/lib/features.jsx'
import { useBranding } from '@/lib/branding.jsx'
import { cn } from '@/lib/utils'

function loadGroupState() {
  // Solo lo persistido: un grupo sin entrada cae a su `collapsedByDefault` al renderizar (los ids
  // varían por familia — construcción usa obra/comercial/recursos/materiales/plata).
  try {
    const raw = localStorage.getItem('ferrebot_sidebar_groups')
    if (raw) return JSON.parse(raw)
  } catch {}
  return {}
}

function saveGroupState(state) {
  try { localStorage.setItem('ferrebot_sidebar_groups', JSON.stringify(state)) } catch {}
}

export default function Sidebar({ collapsed, setCollapsed, onOpenCommand, colorScheme, onToggleColorScheme }) {
  const features = useFeatures()
  const branding = useBranding()
  const nombreComercial = branding?.nombre_comercial || 'Melquiadez'
  const [groupOpen, setGroupOpen] = useState(loadGroupState)
  // Logo resiliente: si la imagen no carga (URL rota/ausente), `onError` lo marca y caemos al cuadro
  // tematizado — nunca se muestra el ícono de "imagen rota". Se reintenta si cambia la URL.
  const [logoRoto, setLogoRoto] = useState(false)
  useEffect(() => { setLogoRoto(false) }, [branding?.logo_url])

  function toggleGroup(id) {
    setGroupOpen(prev => {
      const next = { ...prev, [id]: !prev[id] }
      saveGroupState(next)
      return next
    })
  }

  // Atajo: [ colapsa/expande sidebar
  useEffect(() => {
    const fn = (e) => {
      if (e.key === '[' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const tag = (e.target?.tagName || '').toLowerCase()
        if (tag === 'input' || tag === 'textarea' || e.target?.isContentEditable) return
        setCollapsed(c => !c)
      }
    }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [setCollapsed])

  // Las portadas (top) también se gatean: solo la home resuelta del tenant (Inicio o Hoy) se muestra.
  const topItems = ROUTES.filter(r => r.group === 'top' && isRouteEnabled(r.path, features))

  return (
    <aside
      className={cn(
        'sticky top-0 h-dvh shrink-0 border-r border-border bg-surface-sidebar',
        'flex flex-col transition-[width] duration-base ease-out-quad',
        collapsed ? 'w-16' : 'w-60',
      )}
      aria-label="Navegación principal"
    >
      {/* Brand — white-label: logo + nombre comercial de la empresa (GET /config). Sin logo → cuadro
          tematizado con --color-primary; sin nombre → fallback "Melquiadez" (marca de plataforma). */}
      <div className={cn('flex items-center gap-2.5 px-3 h-[88px] border-b border-border', collapsed && 'justify-center px-0')}>
        {branding?.logo_url && !logoRoto ? (
          <img
            src={branding.logo_url}
            alt={nombreComercial}
            onError={() => setLogoRoto(true)}
            className={cn('shrink-0 rounded-md object-contain bg-surface', collapsed ? 'size-9' : 'size-10')}
          />
        ) : (
          <div
            className={cn('shrink-0 rounded-md bg-color-primary', collapsed ? 'size-9' : 'size-10')}
            aria-hidden="true"
          />
        )}
        {!collapsed && (
          <div className="flex flex-col leading-tight min-w-0">
            <span className="font-display text-[17px] font-bold tracking-tight text-foreground truncate leading-tight">{nombreComercial}</span>
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Dashboard</span>
          </div>
        )}
      </div>

      {/* Nav scrollable */}
      <nav className="flex-1 overflow-y-auto py-3 scrollbar-aurora">
        {topItems.map(item => (
          <SidebarLink key={item.path} item={item} collapsed={collapsed} />
        ))}

        {groupsFor(features).map(group => {
          const items = routesByGroup(group.id, features)
          if (!items.length) return null
          const isOpen = collapsed ? true : (groupOpen[group.id] ?? !group.collapsedByDefault)
          return (
            <div key={group.id} className="mt-4">
              {!collapsed && (
                <button
                  onClick={() => toggleGroup(group.id)}
                  className="flex w-full items-center justify-between px-4 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors"
                >
                  <span>{group.label}</span>
                  {isOpen ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
                </button>
              )}
              {isOpen && items.map(item => (
                <SidebarLink key={item.path} item={item} collapsed={collapsed} />
              ))}
            </div>
          )
        })}
      </nav>

      {/* Footer: Cmd+K + tema + colapsar */}
      <div className={cn('border-t border-border p-2 flex gap-1', collapsed ? 'flex-col items-center' : 'items-center')}>
        <button
          onClick={onOpenCommand}
          title="Buscar (Ctrl+K)"
          className={cn(
            'flex items-center gap-2 rounded-md border border-border bg-surface px-2.5 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:bg-surface-2 transition-colors',
            collapsed ? 'w-9 h-9 justify-center px-0' : 'flex-1',
          )}
        >
          <Command className="size-3.5" />
          {!collapsed && (
            <>
              <span>Buscar…</span>
              <kbd className="ml-auto text-[10px] font-mono bg-surface-2 border border-border rounded px-1.5 py-0.5">⌘K</kbd>
            </>
          )}
        </button>
        <button
          onClick={onToggleColorScheme}
          title={colorScheme === 'dark' ? 'Tema claro' : 'Tema oscuro'}
          className="size-9 grid place-items-center rounded-md border border-border bg-surface text-muted-foreground hover:text-foreground hover:bg-surface-2 transition-colors"
        >
          {colorScheme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </button>
        <button
          onClick={() => setCollapsed(c => !c)}
          title={collapsed ? 'Expandir' : 'Colapsar'}
          className="size-9 grid place-items-center rounded-md border border-border bg-surface text-muted-foreground hover:text-foreground hover:bg-surface-2 transition-colors"
        >
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
        </button>
      </div>
    </aside>
  )
}

function SidebarLink({ item, collapsed }) {
  const Icon = item.icon
  return (
    <NavLink
      to={item.path}
      title={collapsed ? item.label : undefined}
      className={({ isActive }) => cn(
        'group flex items-center gap-3 px-4 py-2 mx-2 rounded-md text-sm transition-colors duration-fast ease-out-quad',
        'relative',
        isActive
          ? 'bg-primary-soft text-primary font-medium'
          : 'text-secondary-foreground hover:bg-surface-2 hover:text-foreground',
        collapsed && 'justify-center px-0 mx-1',
      )}
    >
      {({ isActive }) => (
        <>
          {isActive && !collapsed && (
            <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] bg-primary rounded-r-sm" />
          )}
          <Icon className={cn('size-[18px] shrink-0', isActive && 'text-primary')} />
          {!collapsed && <span className="truncate">{item.label}</span>}
        </>
      )}
    </NavLink>
  )
}
