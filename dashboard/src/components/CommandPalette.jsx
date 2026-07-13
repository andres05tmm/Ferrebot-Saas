/*
 * CommandPalette — Cmd+K / Ctrl+K. Indexa navegación + acciones rápidas.
 * Los destinos se filtran por las features de la empresa (gating de GET /config).
 */
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CommandDialog, CommandInput, CommandList, CommandEmpty,
  CommandGroup, CommandItem, CommandShortcut, CommandSeparator,
} from '@/components/ui/command.jsx'
import { Plus, RefreshCw } from 'lucide-react'
import { ROUTES, groupsFor, routesByGroup } from '@/routes.jsx'
import { useFeatures, isRouteEnabled } from '@/lib/features.jsx'

export default function CommandPalette({ open, setOpen, onRefresh }) {
  const navigate = useNavigate()
  const features = useFeatures()

  // Atajos globales: Cmd+K abre, Cmd+N nueva venta
  useEffect(() => {
    const fn = (e) => {
      const mod = e.metaKey || e.ctrlKey
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen(o => !o)
      } else if (mod && e.key.toLowerCase() === 'n') {
        // Solo si el tenant tiene la feature de ventas: en verticales de servicios no hay POS.
        if (!isRouteEnabled('/ventas', features)) return
        e.preventDefault()
        navigate('/ventas')
      }
    }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [setOpen, navigate, features])

  function run(fn) {
    setOpen(false)
    setTimeout(fn, 0)
  }

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Buscar destino o acción..." />
      <CommandList>
        <CommandEmpty>Sin resultados.</CommandEmpty>

        <CommandGroup heading="Acciones">
          {/* "Nueva venta rápida" navega a /ventas, que la familia construcción tiene suprimida (no
              vende por mostrador). Se gatea por la MISMA condición que la ruta: desaparece para obra,
              sigue para retail. */}
          {isRouteEnabled('/ventas', features) && (
            <CommandItem onSelect={() => run(() => navigate('/ventas'))}>
              <Plus className="size-4" />
              <span>Nueva venta rápida</span>
              <CommandShortcut>⌘N</CommandShortcut>
            </CommandItem>
          )}
          <CommandItem onSelect={() => run(() => navigate('/gastos'))}>
            <Plus className="size-4" />
            <span>Registrar gasto</span>
          </CommandItem>
          <CommandItem onSelect={() => run(() => navigate('/caja'))}>
            <Plus className="size-4" />
            <span>Abrir / cerrar caja</span>
          </CommandItem>
          {onRefresh && (
            <CommandItem onSelect={() => run(onRefresh)}>
              <RefreshCw className="size-4" />
              <span>Refrescar datos</span>
            </CommandItem>
          )}
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading="Inicio">
          {ROUTES.filter(r => r.group === 'top' && isRouteEnabled(r.path, features)).map(r => {
            const Icon = r.icon
            return (
              <CommandItem key={r.path} onSelect={() => run(() => navigate(r.path))}>
                <Icon className="size-4" />
                <span>{r.label}</span>
              </CommandItem>
            )
          })}
        </CommandGroup>

        {groupsFor(features).map(group => {
          const items = routesByGroup(group.id, features)
          if (!items.length) return null
          return (
            <CommandGroup key={group.id} heading={group.label}>
              {items.map(r => {
                const Icon = r.icon
                return (
                  <CommandItem key={r.path} onSelect={() => run(() => navigate(r.path))}>
                    <Icon className="size-4" />
                    <span>{r.label}</span>
                  </CommandItem>
                )
              })}
            </CommandGroup>
          )
        })}
      </CommandList>
    </CommandDialog>
  )
}
