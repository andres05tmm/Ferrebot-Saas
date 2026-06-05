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
import { ROUTES, GROUPS } from '@/routes.jsx'
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
        e.preventDefault()
        navigate('/ventas')
      }
    }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [setOpen, navigate])

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
          <CommandItem onSelect={() => run(() => navigate('/ventas'))}>
            <Plus className="size-4" />
            <span>Nueva venta rápida</span>
            <CommandShortcut>⌘N</CommandShortcut>
          </CommandItem>
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

        <CommandGroup heading="Hoy">
          {ROUTES.filter(r => r.group === 'top').map(r => {
            const Icon = r.icon
            return (
              <CommandItem key={r.path} onSelect={() => run(() => navigate(r.path))}>
                <Icon className="size-4" />
                <span>{r.label}</span>
              </CommandItem>
            )
          })}
        </CommandGroup>

        {GROUPS.map(group => {
          const items = ROUTES.filter(r => r.group === group.id && isRouteEnabled(r.path, features))
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
