/*
 * TabCotizaciones — pack ventas/cotizaciones por WhatsApp (ADR 0017). Gateada por 'pack_ventas'.
 * El backend deja a staff (vendedor+) LISTAR y MARCAR (aceptada/cancelada — es quien cierra la venta);
 * la config del carrito WA es de admin. Aquí: lista con filtro por estado, detalle de ítems por
 * cotización, acciones de aceptar/cancelar (sobre pendientes) y, para el admin, la config.
 * Tiempo real: refetch ante cotizacion_creada / cotizacion_estado.
 */
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'
import { FileSpreadsheet, Check, X, ChevronDown, ChevronRight } from 'lucide-react'
import { cop } from '@/components/shared.jsx'
import {
  useCotizaciones, useCotizacionesConfig, useMarcarCotizacion, useGuardarCotizacionesConfig, keyPrefix,
} from '@/lib/queries'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])
const EVENTOS = ['cotizacion_creada', 'cotizacion_estado', 'cotizacion_aceptada']

const FILTROS = [
  { id: '', label: 'Todas' },
  { id: 'pendiente', label: 'Pendientes' },
  { id: 'aceptada', label: 'Aceptadas' },
  { id: 'cancelada', label: 'Canceladas' },
  { id: 'vencida', label: 'Vencidas' },
]

const ESTADO_BADGE = {
  pendiente: 'bg-info/10 text-info border-info/20',
  aceptada: 'bg-success/10 text-success border-success/20',
  cancelada: 'bg-muted text-muted-foreground border-border',
  vencida: 'bg-warning/10 text-warning border-warning/20',
}

function fechaCorta(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('es-CO', { day: '2-digit', month: 'short', timeZone: 'America/Bogota' })
}

function Fila({ cot, admin, onMarcar }) {
  const [abierto, setAbierto] = useState(false)
  const items = arr(cot.items)
  return (
    <li className="px-3.5 py-2.5 text-[13px]">
      <div className="flex items-center gap-3">
        <button onClick={() => setAbierto(v => !v)} className="shrink-0 text-muted-foreground"
          aria-label={abierto ? 'Ocultar ítems' : 'Ver ítems'}>
          {abierto ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="font-medium truncate">{cot.cliente_nombre || cot.cliente_telefono}</div>
          <div className="text-[11px] text-muted-foreground tabular-nums">
            {items.length} ítem{items.length === 1 ? '' : 's'} · {fechaCorta(cot.creado_en)}
            {cot.vigencia_hasta && ` · vence ${cot.vigencia_hasta}`}
          </div>
        </div>
        <span className="tabular-nums font-semibold shrink-0">{cop(cot.total)}</span>
        <Badge variant="outline" className={`h-5 text-[10px] capitalize shrink-0 ${ESTADO_BADGE[cot.estado] || ''}`}>
          {cot.estado}
        </Badge>
        {cot.estado === 'pendiente' && (
          <div className="flex gap-1 shrink-0">
            <Button size="sm" variant="ghost" className="h-7 px-2 text-success"
              aria-label={`Aceptar cotización ${cot.id}`} title="Aceptar"
              onClick={() => onMarcar(cot, 'aceptada')}>
              <Check className="size-4" />
            </Button>
            <Button size="sm" variant="ghost" className="h-7 px-2 text-destructive"
              aria-label={`Cancelar cotización ${cot.id}`} title="Cancelar"
              onClick={() => onMarcar(cot, 'cancelada')}>
              <X className="size-4" />
            </Button>
          </div>
        )}
      </div>
      {abierto && (
        <ul className="mt-2 ml-7 space-y-1 border-l border-border-subtle pl-3">
          {items.length === 0 ? (
            <li className="text-[12px] text-muted-foreground">Sin ítems.</li>
          ) : items.map(it => (
            <li key={it.id} className="flex items-center gap-2 text-[12px] text-muted-foreground">
              <span className="tabular-nums">{Number(it.cantidad)}×</span>
              <span className="flex-1 truncate">{it.nombre}</span>
              <span className="tabular-nums">{cop(it.subtotal)}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

function SeccionConfig({ config }) {
  const [f, setF] = useState(null)
  const guardarM = useGuardarCotizacionesConfig()
  useEffect(() => { if (config && !f) setF(config) }, [config]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!f) return null

  async function guardar() {
    const body = { mostrar_stock: !!f.mostrar_stock, vigencia_dias: Number(f.vigencia_dias) || 3 }
    try {
      const res = await guardarM.mutateAsync(body)
      if (res.ok) toast.success('Configuración guardada')
      else if (res.status === 403) toast.error('Necesitas permisos de administrador')
      else toast.error('No se pudo guardar')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <Card className="p-3.5">
      <h3 className="text-sm font-semibold mb-3">Cotizador por WhatsApp</h3>
      <div className="space-y-2.5">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Vigencia (días)</span>
          <Input type="number" value={f.vigencia_dias ?? ''} onChange={e => setF(p => ({ ...p, vigencia_dias: e.target.value }))}
            aria-label="Vigencia (días)" className="h-9" />
        </label>
        <label className="inline-flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!f.mostrar_stock} aria-label="Mostrar stock disponible"
            onChange={e => setF(p => ({ ...p, mostrar_stock: e.target.checked }))} />
          Mostrar stock disponible en la cotización
        </label>
      </div>
      <div className="mt-3 flex justify-end">
        <Button onClick={guardar}>Guardar</Button>
      </div>
    </Card>
  )
}

export default function TabCotizaciones() {
  const { isAdmin } = useAuth()
  const admin = isAdmin()
  const [filtro, setFiltro] = useState('')
  const qc = useQueryClient()
  const cotsQ = useCotizaciones(filtro)
  // La config solo la puede leer el admin (403 para staff): `enabled` corta la llamada sin rol.
  const configQ = useCotizacionesConfig(admin)
  const marcarM = useMarcarCotizacion()
  useRealtimeEvent(EVENTOS, () => qc.invalidateQueries({ queryKey: keyPrefix.cotizacionesLista }))

  const cots = arr(cotsQ.data)

  async function onMarcar(cot, estado) {
    try {
      const res = await marcarM.mutateAsync({ id: cot.id, estado })
      if (res.ok) toast.success(estado === 'aceptada' ? 'Cotización aceptada' : 'Cotización cancelada')
      else if (res.status === 409) toast.error('La cotización ya no admite ese cambio')
      else toast.error('No se pudo actualizar')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
      <div className="lg:col-span-2 space-y-3">
        <h1 className="text-base font-semibold inline-flex items-center gap-2">
          <FileSpreadsheet className="size-4.5 text-primary" /> Cotizaciones
        </h1>
        <div className="flex flex-wrap gap-1.5">
          {FILTROS.map(fo => (
            <button key={fo.id || 'todas'} onClick={() => setFiltro(fo.id)}
              className={`text-[12px] px-2.5 h-8 rounded-md border transition-colors ${
                filtro === fo.id ? 'bg-primary text-primary-foreground border-primary' : 'bg-surface border-border hover:bg-surface-2'
              }`}>
              {fo.label}
            </button>
          ))}
        </div>
        <Card className="p-0 overflow-hidden">
          {cotsQ.isLoading ? (
            <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
          ) : cotsQ.isError ? (
            <p className="py-10 text-center text-sm text-destructive">No se pudieron cargar las cotizaciones.</p>
          ) : cots.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              Sin cotizaciones {filtro ? `en estado "${filtro}"` : 'todavía'}.
            </p>
          ) : (
            <ul className="divide-y divide-border-subtle">
              {cots.map(c => <Fila key={c.id} cot={c} admin={admin} onMarcar={onMarcar} />)}
            </ul>
          )}
        </Card>
      </div>
      {admin && <SeccionConfig config={configQ.data} />}
    </div>
  )
}
