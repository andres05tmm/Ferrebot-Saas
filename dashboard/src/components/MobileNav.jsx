/*
 * MobileNav — bottom nav móvil (<768px).
 * 5 botones: Hoy + 4 grupos (Operación / Gestión / Reportes / Fiscal).
 * Tap en grupo abre drawer con los items (ya filtrados por las features de la empresa).
 */
import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { LayoutDashboard, ChevronUp, X } from 'lucide-react'
import { GROUPS, routesByGroup, ROUTES } from '@/routes.jsx'
import { useFeatures, resolveHomePath } from '@/lib/features.jsx'
import { cn } from '@/lib/utils'

const GROUP_ICONS = {
  operacion:    ROUTES.find(r => r.path === '/ventas')?.icon,
  construccion: ROUTES.find(r => r.path === '/obras')?.icon,   // HardHat — sin esto el grupo entero desaparecía del bottom nav
  gestion:      ROUTES.find(r => r.path === '/clientes')?.icon,
  reportes:     ROUTES.find(r => r.path === '/historial')?.icon,
  fiscal:       ROUTES.find(r => r.path === '/facturacion')?.icon,
}

export default function MobileNav() {
  const features = useFeatures()
  const [openGroup, setOpenGroup] = useState(null)
  const navigate = useNavigate()
  const location = useLocation()

  // Portada del tenant (Inicio de servicios o Hoy POS): un solo botón "home" que resuelve por features.
  const homePath = resolveHomePath(features)
  const homeRoute = ROUTES.find(r => r.path === homePath)
  const HomeIcon = homeRoute?.icon || LayoutDashboard
  const isHome = location.pathname === homePath || location.pathname === '/'
  const activeGroup = ROUTES.find(r => r.path === location.pathname)?.group
  const drawer = openGroup ? GROUPS.find(g => g.id === openGroup) : null

  function go(path) {
    navigate(path)
    setOpenGroup(null)
  }

  return (
    <>
      {drawer && (
        <div
          onClick={() => setOpenGroup(null)}
          className="fixed inset-0 z-[99] bg-black/55 backdrop-blur-sm flex flex-col justify-end animate-in fade-in duration-200"
        >
          <div
            onClick={e => e.stopPropagation()}
            className="bg-surface border-t border-border rounded-t-xl pb-[calc(72px+env(safe-area-inset-bottom))] pt-4 px-4 animate-in slide-in-from-bottom duration-200"
          >
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-semibold">{drawer.label}</span>
              <button onClick={() => setOpenGroup(null)} className="size-8 grid place-items-center rounded-md hover:bg-surface-2">
                <X className="size-4 text-muted-foreground" />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2 pb-2">
              {routesByGroup(drawer.id, features).map(item => {
                const Icon = item.icon
                const isActive = location.pathname === item.path
                return (
                  <button
                    key={item.path}
                    onClick={() => go(item.path)}
                    className={cn(
                      'flex flex-col items-center gap-2 p-4 rounded-lg border transition-colors',
                      isActive
                        ? 'border-primary/40 bg-primary-soft text-primary'
                        : 'border-border bg-surface-2/40 text-foreground hover:bg-surface-2',
                    )}
                  >
                    <Icon className="size-5" />
                    <span className="text-xs font-medium text-center leading-tight">{item.label}</span>
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      )}

      <nav
        className="fixed bottom-0 inset-x-0 z-[100] bg-surface-sidebar/95 backdrop-blur-md border-t border-border flex items-stretch pb-[env(safe-area-inset-bottom)]"
        aria-label="Navegación móvil"
      >
        <BottomItem
          icon={HomeIcon}
          label={homeRoute?.label || 'Inicio'}
          active={isHome}
          onClick={() => go(homePath)}
        />
        {GROUPS.map(group => {
          const Icon = GROUP_ICONS[group.id]
          // Igual que el Sidebar: ocultar el grupo si el tenant no tiene rutas habilitadas en él
          // (evita un botón muerto —p. ej. Construcción en un tenant retail— con drawer vacío).
          if (!Icon || !routesByGroup(group.id, features).length) return null
          const active = activeGroup === group.id
          return (
            <BottomItem
              key={group.id}
              icon={Icon}
              label={group.label}
              active={active}
              hasMore
              onClick={() => setOpenGroup(g => g === group.id ? null : group.id)}
            />
          )
        })}
      </nav>
    </>
  )
}

function BottomItem({ icon: Icon, label, active, hasMore, onClick }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex-1 flex flex-col items-center justify-center gap-1 py-2 relative',
        active ? 'text-primary' : 'text-muted-foreground',
      )}
    >
      <Icon className={cn('size-5', active && 'text-primary')} />
      <span className={cn('text-[10px] font-medium', active && 'font-semibold')}>{label}</span>
      {hasMore && <ChevronUp className="size-2.5 absolute top-1 right-1/3 opacity-50" />}
    </button>
  )
}
