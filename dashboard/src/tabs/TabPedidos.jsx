/*
 * TabPedidos — kanban en vivo del pack pedidos (ADR 0016): LA pantalla del restaurante.
 * Gateada por la feature 'pack_pedidos' (la ruta se oculta sin ella). Staff opera el ciclo
 * (pendiente → en preparación → en camino → entregado; cancelar desde no finales). El kanban ES
 * el tab entero: 4 columnas a altura completa. Las reglas de pedidos viven ahora en Conocimiento
 * (TabConocimiento) y las zonas quedaron fuera de la UI (el endpoint sigue vivo).
 * Tiempo real: refetch ante pedido_confirmado / pedido_estado / pedido_pagado (SSE).
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { ChefHat, Bike, CheckCircle2, ClipboardList, Receipt, XCircle } from 'lucide-react'
import { api } from '@/lib/api'
import { useFeatures } from '@/lib/features.jsx'
import { cop, useFetch } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Button } from '@/components/ui/button.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

// Columnas del kanban (el ciclo operable; 'recibido' son carritos sin confirmar y no se muestran).
// 'confirmado' → "Pendientes": ahí caen los pedidos confirmados y recién pagados esperando cocina.
// 'entregado' es archivo del día: se pinta más compacto/apagado (columna `apagada`).
const COLUMNAS = [
  { estado: 'confirmado',     label: 'Pendientes',     icon: ClipboardList, siguiente: 'en_preparacion', accion: 'A cocina' },
  { estado: 'en_preparacion', label: 'En preparación', icon: ChefHat,       siguiente: 'en_camino',      accion: 'Despachar' },
  { estado: 'en_camino',      label: 'En camino',      icon: Bike,          siguiente: 'entregado',      accion: 'Entregado' },
  { estado: 'entregado',      label: 'Entregados',     icon: CheckCircle2,  siguiente: null,             accion: null, apagada: true },
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

function InsigniaPagado() {
  // Chip verde consistente con los chips del tab; se muestra cuando el cobro del pedido está pagado.
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
      <CheckCircle2 className="size-3" /> Pagado
    </span>
  )
}

function TarjetaPedido({ p, col, pagado, onAvanzar, onCancelar, onConvertir }) {
  return (
    <Card className="p-2.5 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-[13px]">#{p.id} · {p.cliente_nombre || p.cliente_telefono}</span>
        <span className="inline-flex items-center gap-1.5">
          {pagado && <InsigniaPagado />}
          <span className="text-[11px] text-muted-foreground tabular-nums">{horaCorta(p.creado_en)}</span>
        </span>
      </div>
      <ul className="text-[12px] text-muted-foreground space-y-0.5">
        {p.items.map(i => (
          <li key={i.id}>{Number(i.cantidad)}× {i.nombre}</li>
        ))}
      </ul>
      {p.direccion && <div className="text-[12px] truncate">{p.direccion}</div>}
      {p.telefono_contacto && (
        <div className="text-[12px] text-muted-foreground">📞 {p.telefono_contacto}</div>
      )}
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
        {onConvertir && !p.venta_id && (
          <Button size="sm" variant="outline" className="flex-1"
            aria-label={`Registrar venta del pedido ${p.id}`} onClick={() => onConvertir(p)}>
            <Receipt className="size-3.5" /> Registrar venta
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

export default function TabPedidos() {
  const pedidosQ = useFetch('/pedidos')
  // Pedidos marcados pagados en vivo por el SSE `pedido_pagado`: la insignia aparece al instante,
  // sin esperar el round-trip del refetch (el efecto "wow" del pago detectado en la demo).
  const [pagadosLive, setPagadosLive] = useState(() => new Set())

  useRealtimeEvent(['pedido_confirmado', 'pedido_estado', 'pedido_pagado'], (tipo, data) => {
    if (tipo === 'pedido_pagado' && data?.pedido_id != null) {
      setPagadosLive(prev => new Set(prev).add(Number(data.pedido_id)))
    }
    pedidosQ.refetch()
  })

  const features = useFeatures()
  const pedidos = arr(pedidosQ.data)
  const onAvanzar = (p, nuevo) =>
    enviar(`/pedidos/${p.id}/estado`, 'PUT', { estado: nuevo }, `Pedido #${p.id} → ${nuevo.replace('_', ' ')}`, pedidosQ.refetch)
  const onCancelar = (p) =>
    enviar(`/pedidos/${p.id}/estado`, 'PUT', { estado: 'cancelado' }, `Pedido #${p.id} cancelado`, pedidosQ.refetch)
  // Conversión pedido → venta (F1 / ADR 0032): registra la venta POS (stock + caja) desde el kanban.
  // Solo con la feature `ventas` (el endpoint también la exige) y en pedidos aún sin venta vinculada.
  const puedeConvertir = features.includes('ventas') || features.includes('pos')
  const onConvertir = puedeConvertir
    ? (p) => enviar(`/pedidos/${p.id}/convertir`, 'POST', {}, `Pedido #${p.id} registrado como venta`, pedidosQ.refetch)
    : null

  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      <h1 className="text-base font-semibold inline-flex items-center gap-2 shrink-0">
        <ChefHat className="size-4.5 text-primary" /> Pedidos
      </h1>

      {/* El kanban ocupa todo el alto disponible. Móvil: apilado (scroll vertical). Desktop: 4 columnas
          iguales, cada una con su propio scroll cuando se llena; Entregados va más compacta/apagada. */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 flex-1 min-h-0">
        {COLUMNAS.map(col => {
          const Icono = col.icon
          const enColumna = pedidos.filter(p => p.estado === col.estado)
          return (
            <div
              key={col.estado}
              className={`flex flex-col min-h-0 rounded-lg border border-border-subtle p-2.5 ${
                col.apagada ? 'bg-surface-2/20 opacity-80' : 'bg-surface-2/40'
              }`}
            >
              <div className={`shrink-0 pb-2 text-[12px] font-semibold uppercase tracking-wider inline-flex items-center gap-1.5 ${
                col.apagada ? 'text-muted-foreground/70' : 'text-muted-foreground'
              }`}>
                <Icono className="size-3.5" /> {col.label} ({enColumna.length})
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto space-y-2">
                {enColumna.length === 0 ? (
                  <Card className="p-3 text-center text-[12px] text-muted-foreground">—</Card>
                ) : (
                  enColumna.map(p => (
                    <TarjetaPedido key={p.id} p={p} col={col}
                      pagado={p.pagado || pagadosLive.has(p.id)}
                      onAvanzar={onAvanzar} onCancelar={onCancelar} onConvertir={onConvertir} />
                  ))
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
