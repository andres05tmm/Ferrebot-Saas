/*
 * PanelPedidos — el ciclo de la compra: en camino → llegó → (corregir si hubo error).
 *
 * Cada compra se registra al hacer el pedido y su cronómetro corre hasta que llega la mercancía; el
 * semáforo compara contra la fecha prometida o contra lo que ese proveedor suele tardar. Al final,
 * la tabla de tiempos por proveedor: el historial de quién cumple.
 */
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Pencil, Plus, Timer, Truck, XCircle } from '@/lib/icons.jsx'
import { api, apiJson } from '@/lib/api'
import { cop } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Badge } from '@/components/ui/badge.jsx'
import ModalCorregir from './ModalCorregir.jsx'
import ModalPedido from './ModalPedido.jsx'
import ModalRecibir from './ModalRecibir.jsx'
import { arr, fechaCorta, horasATexto } from './comunes.jsx'

const EVENTOS = [
  'pedido_proveedor_creado', 'pedido_proveedor_recibido', 'pedido_proveedor_cancelado',
  'compra_corregida',
  'pedido_demorado',   // el cron acaba de avisar: refresca la lista (horas/semáforo al día)
]
const KEY = ['pedidos-proveedor']

const FILTROS = [
  { id: 'pedido', label: 'En camino' },
  { id: 'recibido', label: 'Recibidas' },
  { id: '', label: 'Todas' },
]

// Semáforo del cronómetro: rojo si superó el promedio del proveedor (o la fecha estimada),
// ámbar desde el 75% del promedio, verde antes. Sin referencia → neutro.
function tonoCronometro(p) {
  if (p.estado !== 'pedido') return 'neutro'
  if (p.fecha_estimada) {
    const vence = new Date(`${p.fecha_estimada}T23:59:59-05:00`)
    return Date.now() > vence.getTime() ? 'rojo' : 'verde'
  }
  if (p.promedio_proveedor_horas == null || p.horas_transcurridas == null) return 'neutro'
  if (p.horas_transcurridas > p.promedio_proveedor_horas) return 'rojo'
  if (p.horas_transcurridas > p.promedio_proveedor_horas * 0.75) return 'ambar'
  return 'verde'
}

const TONO_CLS = {
  rojo: 'bg-danger/10 text-danger border-danger/20',
  ambar: 'bg-warning/10 text-warning border-warning/20',
  verde: 'bg-success/10 text-success border-success/20',
  neutro: 'bg-muted text-muted-foreground border-border',
}

const PAGO_LABEL = {
  contado: 'pagada de contado',
  credito: 'a crédito',
  anticipado: 'con anticipo',
}

export default function PanelPedidos({ esAdmin = false }) {
  const [filtro, setFiltro] = useState('pedido')
  const [crearAbierto, setCrearAbierto] = useState(false)
  const [recibiendo, setRecibiendo] = useState(null)
  const [corrigiendo, setCorrigiendo] = useState(null)
  const qc = useQueryClient()

  const pedidosQ = useQuery({
    queryKey: [...KEY, filtro],
    queryFn: () => apiJson(`/pedidos-proveedor${filtro ? `?estado=${filtro}` : ''}`),
  })
  const metricasQ = useQuery({
    queryKey: [...KEY, 'metricas'],
    queryFn: () => apiJson('/pedidos-proveedor/metricas'),
  })
  useRealtimeEvent(EVENTOS, () => qc.invalidateQueries({ queryKey: KEY }))

  const pedidos = arr(pedidosQ.data)
  const metricas = arr(metricasQ.data)
  const enCamino = useMemo(
    () => metricas.reduce((acc, m) => acc + m.pedidos_en_camino, 0), [metricas],
  )
  const masViejo = useMemo(
    () => metricas.reduce((acc, m) => Math.max(acc, m.mas_viejo_en_camino_horas || 0), 0), [metricas],
  )

  function refrescar() {
    setCrearAbierto(false); setRecibiendo(null); setCorrigiendo(null)
    qc.invalidateQueries({ queryKey: KEY })
  }

  async function cancelar(p) {
    if (!window.confirm(`¿Cancelar la compra #${p.id} a ${p.proveedor_nombre}?`)) return
    try {
      const res = await api(`/pedidos-proveedor/${p.id}/cancelar`, { method: 'POST' })
      if (res.ok) { toast.success('Compra cancelada'); refrescar() }
      else toast.error('No se pudo cancelar')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-base font-semibold inline-flex items-center gap-2">
          <Truck className="size-4.5 text-primary" aria-hidden="true" /> Compras
        </h1>
        <Button onClick={() => setCrearAbierto(true)}>
          <Plus className="size-4 mr-1" aria-hidden="true" /> Nueva compra
        </Button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <Card className="p-3">
          <div className="text-caption uppercase tracking-wider text-muted-foreground">En camino</div>
          <div className="text-lg font-semibold tabular-nums">{metricasQ.isLoading ? '…' : enCamino}</div>
        </Card>
        <Card className="p-3">
          <div className="text-caption uppercase tracking-wider text-muted-foreground">Más vieja esperando</div>
          <div className="text-lg font-semibold tabular-nums">
            {metricasQ.isLoading ? '…' : (enCamino ? horasATexto(masViejo) : '—')}
          </div>
        </Card>
        <Card className="p-3 hidden lg:block">
          <div className="text-caption uppercase tracking-wider text-muted-foreground">Proveedores con historial</div>
          <div className="text-lg font-semibold tabular-nums">
            {metricasQ.isLoading ? '…' : metricas.filter(m => m.pedidos_recibidos > 0).length}
          </div>
        </Card>
      </div>

      <div className="flex gap-2">
        {FILTROS.map(f => (
          <button key={f.id} onClick={() => setFiltro(f.id)} aria-pressed={filtro === f.id}
            className={`px-2.5 py-1 rounded-md border text-body-sm ${
              filtro === f.id ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>
            {f.label}
          </button>
        ))}
      </div>

      <Card className="p-0 divide-y divide-border-subtle">
        {pedidosQ.isLoading && <div className="p-4 text-body-sm text-muted-foreground">Cargando…</div>}
        {!pedidosQ.isLoading && pedidos.length === 0 && (
          <div className="p-6 text-center text-body-sm text-muted-foreground">
            {filtro === 'pedido'
              ? 'No hay compras en camino. Cuando le pidas al proveedor, regístrala aquí para medir cuánto tarda.'
              : 'Nada por aquí todavía.'}
          </div>
        )}
        {pedidos.map(p => {
          const tono = tonoCronometro(p)
          return (
            <div key={p.id} className="p-3 flex items-center gap-3 flex-wrap">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium text-body-sm truncate">
                    {p.proveedor_nombre || `Proveedor #${p.proveedor_id}`}
                  </span>
                  {p.condicion_pago && (
                    <Badge variant="outline" className="text-micro">
                      {PAGO_LABEL[p.condicion_pago] || p.condicion_pago}
                      {p.condicion_pago === 'anticipado' && p.anticipo
                        ? ` ${cop(Number(p.anticipo))}` : ''}
                    </Badge>
                  )}
                </div>
                <div className="text-caption text-muted-foreground truncate">
                  {p.detalles?.length ? `${p.detalles.length} producto(s)` : p.descripcion || ''}
                  {p.monto_estimado ? ` · ${cop(Number(p.monto_estimado))}` : ''}
                  {` · pedida ${fechaCorta(p.fecha_pedido)}`}
                </div>
              </div>

              {p.estado === 'pedido' && (
                <Badge variant="outline" className={`inline-flex items-center gap-1 ${TONO_CLS[tono]}`}>
                  <Timer className="size-3" aria-hidden="true" />
                  {horasATexto(p.horas_transcurridas)}
                  {p.promedio_proveedor_horas != null && (
                    <span className="opacity-70">/ suele tardar {horasATexto(p.promedio_proveedor_horas)}</span>
                  )}
                </Badge>
              )}
              {p.estado === 'recibido' && (
                <Badge variant="outline" className={TONO_CLS.verde}>
                  <CheckCircle2 className="size-3 mr-1" aria-hidden="true" />
                  llegó en {horasATexto(p.lead_time_horas)}
                </Badge>
              )}
              {p.estado === 'cancelado' && (
                <Badge variant="outline" className={TONO_CLS.neutro}>
                  <XCircle className="size-3 mr-1" aria-hidden="true" /> cancelada
                </Badge>
              )}

              {p.estado === 'pedido' && (
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => setRecibiendo(p)}>
                    <Truck className="size-4 mr-1" aria-hidden="true" /> Llegó
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => cancelar(p)}>Cancelar</Button>
                </div>
              )}
              {p.estado === 'recibido' && esAdmin && p.compra_id && (
                <Button size="sm" variant="outline" onClick={() => setCorrigiendo(p)}>
                  <Pencil className="size-4 mr-1" aria-hidden="true" /> Corregir
                </Button>
              )}
            </div>
          )
        })}
      </Card>

      {metricas.length > 0 && (
        <Card className="p-3">
          <h2 className="text-body-sm font-semibold mb-2">¿Cuánto tarda cada proveedor?</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-body-sm">
              <thead>
                <tr className="text-left text-caption text-muted-foreground">
                  <th className="py-1 pr-2 font-normal">Proveedor</th>
                  <th className="py-1 pr-2 font-normal">Tarda en promedio</th>
                  <th className="py-1 pr-2 font-normal">Compras recibidas</th>
                  <th className="py-1 pr-2 font-normal">Última entrega</th>
                  <th className="py-1 font-normal">En camino</th>
                </tr>
              </thead>
              <tbody>
                {metricas.map(m => (
                  <tr key={m.proveedor_id} className="border-t border-border-subtle">
                    <td className="py-1.5 pr-2">{m.proveedor_nombre}</td>
                    <td className="py-1.5 pr-2 tabular-nums">{horasATexto(m.lead_time_promedio_horas)}</td>
                    <td className="py-1.5 pr-2 tabular-nums">{m.pedidos_recibidos || '—'}</td>
                    <td className="py-1.5 pr-2">{fechaCorta(m.ultima_entrega)}</td>
                    <td className="py-1.5 tabular-nums">{m.pedidos_en_camino || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <ModalPedido abierto={crearAbierto} onCerrar={() => setCrearAbierto(false)} onCreado={refrescar} />
      {recibiendo && (
        <ModalRecibir pedido={recibiendo} onCerrar={() => setRecibiendo(null)} onRecibido={refrescar} />
      )}
      {corrigiendo && (
        <ModalCorregir pedido={corrigiendo} onCerrar={() => setCorrigiendo(null)} onCorregido={refrescar} />
      )}
    </div>
  )
}
