/*
 * TabPedidos — kanban en vivo del pack pedidos (ADR 0016): LA pantalla del restaurante.
 * Gateada por la feature 'pack_pedidos' (la ruta se oculta sin ella). Staff opera el ciclo
 * (confirmado → en preparación → en camino → entregado; cancelar desde no finales); las reglas
 * (horario de cocina, mínimo, domicilio default) y las zonas son de admin.
 * Tiempo real: refetch ante pedido_confirmado / pedido_estado (SSE).
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { ChefHat, Bike, CheckCircle2, ClipboardList, XCircle } from 'lucide-react'
import { api } from '@/lib/api'
import { cop, useFetch } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

// Columnas del kanban (el ciclo operable; 'recibido' son carritos sin confirmar y no se muestran).
const COLUMNAS = [
  { estado: 'confirmado',     label: 'Confirmados',    icon: ClipboardList, siguiente: 'en_preparacion', accion: 'A cocina' },
  { estado: 'en_preparacion', label: 'En preparación', icon: ChefHat,       siguiente: 'en_camino',      accion: 'Despachar' },
  { estado: 'en_camino',      label: 'En camino',      icon: Bike,          siguiente: 'entregado',      accion: 'Entregado' },
  { estado: 'entregado',      label: 'Entregados',     icon: CheckCircle2,  siguiente: null,             accion: null },
]

async function enviar(path, method, body, okMsg, after) {
  try {
    const res = await api(path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    if (res.ok) { if (okMsg) toast.success(okMsg); after?.(); return true }
    if (res.status === 403) toast.error('Necesitas permisos de administrador')
    else if (res.status === 409) toast.error('Ese cambio de estado no es válido')
    else toast.error('No se pudo guardar')
  } catch { toast.error('Error de conexión') }
  return false
}

function horaCorta(iso) {
  return new Date(iso).toLocaleTimeString('es-CO', {
    hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota',
  })
}

function TarjetaPedido({ p, col, onAvanzar, onCancelar }) {
  return (
    <Card className="p-2.5 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-[13px]">#{p.id} · {p.cliente_nombre || p.cliente_telefono}</span>
        <span className="text-[11px] text-muted-foreground tabular-nums">{horaCorta(p.creado_en)}</span>
      </div>
      <ul className="text-[12px] text-muted-foreground space-y-0.5">
        {p.items.map(i => (
          <li key={i.id}>{Number(i.cantidad)}× {i.nombre}</li>
        ))}
      </ul>
      {p.direccion && <div className="text-[12px] truncate">{p.direccion}</div>}
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold tabular-nums text-[13px]">{cop(p.total)}</span>
        <span className="text-[11px] text-muted-foreground">{p.metodo_pago || ''}</span>
      </div>
      {p.notas && <div className="text-[11px] italic text-muted-foreground">“{p.notas}”</div>}
      <div className="flex gap-1.5 pt-0.5">
        {col.siguiente && (
          <Button size="sm" className="flex-1" onClick={() => onAvanzar(p, col.siguiente)}>
            {col.accion}
          </Button>
        )}
        {col.siguiente && (
          <Button size="sm" variant="ghost" className="text-destructive"
            aria-label={`Cancelar pedido ${p.id}`} onClick={() => onCancelar(p)}>
            <XCircle className="size-3.5" />
          </Button>
        )}
      </div>
    </Card>
  )
}

function SeccionReglas({ config, refetch }) {
  const [f, setF] = useState(null)
  if (config && !f) setF(config)
  if (!f) return null
  const set = (k) => (e) => setF(prev => ({ ...prev, [k]: e.target.value }))

  async function guardar() {
    const body = {
      activo: !!f.activo,
      hora_apertura: f.hora_apertura,
      hora_cierre: f.hora_cierre,
      minimo_pedido: String(f.minimo_pedido ?? '0'),
      tiempo_estimado_min: Number(f.tiempo_estimado_min) || 45,
      costo_domicilio_default: String(f.costo_domicilio_default ?? '0'),
    }
    await enviar('/pedidos/config', 'PUT', body, 'Reglas guardadas', refetch)
  }

  const campo = (label, k, type = 'number') => (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</span>
      <Input type={type} value={f[k] ?? ''} onChange={set(k)} aria-label={label} className="h-9" />
    </label>
  )

  return (
    <Card className="p-3.5">
      <h3 className="text-sm font-semibold mb-3">Reglas de pedidos</h3>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-2.5">
        {campo('Abre (hora)', 'hora_apertura', 'time')}
        {campo('Cierra (hora)', 'hora_cierre', 'time')}
        {campo('Pedido mínimo ($)', 'minimo_pedido')}
        {campo('Tiempo estimado (min)', 'tiempo_estimado_min')}
        {campo('Domicilio default ($)', 'costo_domicilio_default')}
        <label className="inline-flex items-center gap-2 text-sm self-end pb-2">
          <input type="checkbox" checked={!!f.activo} aria-label="Pedidos activos"
            onChange={e => setF(prev => ({ ...prev, activo: e.target.checked }))} />
          Recibiendo pedidos
        </label>
      </div>
      <div className="mt-3 flex justify-end">
        <Button onClick={guardar}>Guardar reglas</Button>
      </div>
    </Card>
  )
}

function SeccionZonas({ zonas, refetch }) {
  const [f, setF] = useState({ nombre: '', tarifa: '' })
  async function crear() {
    if (!f.nombre.trim() || f.tarifa === '') { toast.error('Barrio y tarifa son obligatorios'); return }
    const ok = await enviar('/pedidos/zonas', 'POST',
      { nombre: f.nombre.trim(), tarifa: String(f.tarifa) }, 'Zona creada', refetch)
    if (ok) setF({ nombre: '', tarifa: '' })
  }
  return (
    <Card className="p-3.5">
      <h3 className="text-sm font-semibold mb-2">Zonas de domicilio</h3>
      {zonas.length === 0 ? (
        <p className="text-sm text-muted-foreground py-2">Sin zonas: se usa la tarifa default.</p>
      ) : (
        <ul className="divide-y divide-border-subtle mb-2">
          {zonas.map(z => (
            <li key={z.id} className="py-1.5 flex items-center justify-between text-[13px]">
              <span>{z.nombre}</span>
              <span className="flex items-center gap-2">
                <span className="tabular-nums">{cop(z.tarifa)}</span>
                <Button size="sm" variant="ghost" className="text-destructive"
                  aria-label={`Eliminar zona ${z.nombre}`}
                  onClick={() => enviar(`/pedidos/zonas/${z.id}`, 'DELETE', null, 'Zona eliminada', refetch)}>
                  <XCircle className="size-3.5" />
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <Input value={f.nombre} onChange={e => setF(p => ({ ...p, nombre: e.target.value }))}
          placeholder="Barrio (p. ej. Bocagrande)" aria-label="Barrio" className="h-9" />
        <Input type="number" value={f.tarifa} onChange={e => setF(p => ({ ...p, tarifa: e.target.value }))}
          placeholder="Tarifa" aria-label="Tarifa" className="h-9 w-28" />
        <Button onClick={crear}>Agregar</Button>
      </div>
    </Card>
  )
}

// La config es de admin (403 para staff): vive en su propio componente, montado solo con el rol.
function PanelAdmin() {
  const configQ = useFetch('/pedidos/config')
  const zonasQ = useFetch('/pedidos/zonas')
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <SeccionReglas config={configQ.data} refetch={configQ.refetch} />
      <SeccionZonas zonas={arr(zonasQ.data)} refetch={zonasQ.refetch} />
    </div>
  )
}

export default function TabPedidos() {
  const { isAdmin } = useAuth()
  const admin = isAdmin()
  const pedidosQ = useFetch('/pedidos')

  useRealtimeEvent(['pedido_confirmado', 'pedido_estado'], () => pedidosQ.refetch())

  const pedidos = arr(pedidosQ.data)
  const onAvanzar = (p, nuevo) =>
    enviar(`/pedidos/${p.id}/estado`, 'PUT', { estado: nuevo }, `Pedido #${p.id} → ${nuevo.replace('_', ' ')}`, pedidosQ.refetch)
  const onCancelar = (p) =>
    enviar(`/pedidos/${p.id}/estado`, 'PUT', { estado: 'cancelado' }, `Pedido #${p.id} cancelado`, pedidosQ.refetch)

  return (
    <div className="space-y-3">
      <h1 className="text-base font-semibold inline-flex items-center gap-2">
        <ChefHat className="size-4.5 text-primary" /> Pedidos
      </h1>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        {COLUMNAS.map(col => {
          const Icono = col.icon
          const enColumna = pedidos.filter(p => p.estado === col.estado)
          return (
            <div key={col.estado} className="space-y-2">
              <div className="text-[12px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
                <Icono className="size-3.5" /> {col.label} ({enColumna.length})
              </div>
              {enColumna.length === 0 ? (
                <Card className="p-3 text-center text-[12px] text-muted-foreground">—</Card>
              ) : (
                enColumna.map(p => (
                  <TarjetaPedido key={p.id} p={p} col={col} onAvanzar={onAvanzar} onCancelar={onCancelar} />
                ))
              )}
            </div>
          )
        })}
      </div>

      {admin && <PanelAdmin />}
    </div>
  )
}
