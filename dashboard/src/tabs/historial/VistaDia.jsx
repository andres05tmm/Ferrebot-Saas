/*
 * VistaDia — ventas de un rango (default hoy Colombia), con detalle expandible por venta.
 * Lista: GET /ventas (?desde&hasta) — scopeada por get_filtro_efectivo en el backend. Detalle:
 * GET /ventas/{id} (cabecera + líneas). Live: venta_registrada / venta_anulada / reconnected.
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { ChevronDown, ChevronRight, Trash2 } from 'lucide-react'
import { api } from '@/lib/api.js'
import { useFetch, cop } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const HORA_CO = { hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota' }
const fechaCO = (iso) => new Date(iso).toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
const hoyCO = () => new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })

export default function VistaDia() {
  const { refreshKey } = useOutletContext() ?? {}
  const { isAdmin, getUser } = useAuth()
  const admin = isAdmin()
  const miId = getUser()?.id
  const [desde, setDesde] = useState(hoyCO)
  const [hasta, setHasta] = useState(hoyCO)
  const [expandido, setExpandido] = useState(null)

  const ventasQ = useFetch(`/ventas?desde=${desde}&hasta=${hasta}`, [refreshKey])
  useRealtimeEvent(['venta_registrada', 'venta_anulada', 'reconnected'], ventasQ.refetch)

  const ventas = Array.isArray(ventasQ.data) ? ventasQ.data : []
  const total = ventas.reduce((a, v) => a + Number(v.total), 0)

  // Solo se puede borrar una venta de HOY (Colombia) y que sea mía (o si soy admin, cualquiera).
  const puedoBorrar = (v) =>
    fechaCO(v.fecha) === hoyCO() && (admin || Number(v.vendedor_id) === Number(miId))

  async function borrar(v) {
    if (!window.confirm(`¿Borrar la venta N.º ${v.consecutivo}? Se revertirá el stock.`)) return
    try {
      const res = await api(`/ventas/${v.id}`, { method: 'DELETE' })
      if (res.ok) { toast.success('Venta borrada'); ventasQ.refetch() }
      else if (res.status === 409) toast.error('Tiene factura electrónica, no se puede borrar')
      else if (res.status === 403) toast.error('No puedes borrar esta venta')
      else toast.error('No se pudo borrar la venta')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <div className="space-y-3">
      <Card className="p-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
          Desde
          <Input type="date" value={desde} onChange={(e) => setDesde(e.target.value)} aria-label="Desde" className="h-9 w-40" />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
          Hasta
          <Input type="date" value={hasta} onChange={(e) => setHasta(e.target.value)} aria-label="Hasta" className="h-9 w-40" />
        </label>
        <span className="ml-auto text-[12px] text-muted-foreground tabular">
          {ventas.length} {ventas.length === 1 ? 'venta' : 'ventas'} · {cop(total)}
        </span>
      </Card>

      <Card className="p-0 overflow-hidden">
        {ventasQ.loading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : ventas.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Sin ventas en el rango.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {ventas.map(v => (
              <li key={v.id}>
                <div className="flex items-center hover:bg-surface-2 transition-colors">
                  <button
                    onClick={() => setExpandido(e => (e === v.id ? null : v.id))}
                    aria-label={`Venta ${v.consecutivo}`}
                    className="flex-1 min-w-0 flex items-center gap-2 px-3.5 py-2 text-left"
                  >
                    {expandido === v.id ? <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
                      : <ChevronRight className="size-4 shrink-0 text-muted-foreground" />}
                    <span className="text-[11px] text-muted-foreground tabular w-12 shrink-0">
                      {new Date(v.fecha).toLocaleTimeString('es-CO', HORA_CO)}
                    </span>
                    <span className="text-[13px] shrink-0">N.º {v.consecutivo}</span>
                    <Badge variant="outline" className="text-[10px] h-5 px-1.5 capitalize shrink-0">{v.metodo_pago}</Badge>
                    {v.estado === 'anulada' && (
                      <Badge variant="outline" className="text-[10px] h-5 px-1.5 bg-destructive/10 text-destructive border-destructive/20 shrink-0">anulada</Badge>
                    )}
                    <span className="ml-auto text-[13px] font-semibold tabular shrink-0">{cop(Number(v.total))}</span>
                  </button>
                  {/* Borrar: solo ventas de HOY propias (o admin). Para días anteriores/ajenas: sin botón. */}
                  {puedoBorrar(v) && (
                    <button
                      onClick={() => borrar(v)}
                      aria-label={`Borrar venta N.º ${v.consecutivo}`}
                      title="Borrar venta"
                      className="size-8 grid place-items-center rounded-md text-muted-foreground hover:text-destructive shrink-0 mr-2"
                    >
                      <Trash2 className="size-4" />
                    </button>
                  )}
                </div>
                {expandido === v.id && <DetalleVenta ventaId={v.id} />}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

function DetalleVenta({ ventaId }) {
  const { data, loading, error } = useFetch(`/ventas/${ventaId}`, [])
  if (loading) return <div className="px-9 py-2 text-xs text-muted-foreground">Cargando detalle…</div>
  if (error || !data) return <div className="px-9 py-2 text-xs text-destructive">No se pudo cargar el detalle.</div>
  return (
    <div className="px-9 py-2.5 bg-surface-2/40 border-t border-border-subtle">
      <ul className="space-y-1">
        {data.lineas.map((l, i) => (
          <li key={i} className="flex items-center gap-2 text-[12px]">
            <span className="flex-1 truncate">{l.descripcion || `Producto ${l.producto_id}`}</span>
            <span className="tabular text-muted-foreground shrink-0">
              {Number(l.cantidad)} × {cop(Number(l.precio_unitario))}
            </span>
            <span className="tabular text-muted-foreground w-16 text-right shrink-0">IVA {l.iva}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
