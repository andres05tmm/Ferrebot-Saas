/*
 * VistaDia — ventas de un rango (default hoy Colombia), con detalle expandible por venta.
 * Lista: GET /ventas (?desde&hasta) — scopeada por get_filtro_efectivo en el backend. Detalle:
 * GET /ventas/{id} (cabecera + líneas). Borrar/editar solo ventas de HOY propias (o admin):
 * DELETE/PUT /ventas/{id}. Live: venta_registrada / venta_anulada / venta_editada / reconnected.
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { ChevronDown, ChevronRight, Pencil, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { hoyStrCO } from '@/lib/fechas'
import { useFetch, cop } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Badge } from '@/components/ui/badge.jsx'
import BadgeFiscal, { etiquetaIdentificador } from '@/components/BadgeFiscal.jsx'

const HORA_CO = { hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota' }
const METODOS = ['efectivo', 'transferencia', 'datafono', 'fiado']
// Eventos que refrescan la lista: ventas + ciclo de vida fiscal (el badge se actualiza al aceptar/rechazar).
const EVENTOS = [
  'venta_registrada', 'venta_anulada', 'venta_editada',
  'factura_pendiente', 'factura_aceptada', 'factura_rechazada', 'factura_error', 'factura_anulada',
  'reconnected',
]
const fechaCO = (iso) => new Date(iso).toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
const hoyCO = hoyStrCO

export default function VistaDia() {
  const { refreshKey } = useOutletContext() ?? {}
  const { isAdmin, getUser } = useAuth()
  const admin = isAdmin()
  const miId = getUser()?.id
  const [desde, setDesde] = useState(hoyCO)
  const [hasta, setHasta] = useState(hoyCO)
  const [expandido, setExpandido] = useState(null)
  const [editando, setEditando] = useState(null)   // id de la venta en edición, o null

  const ventasQ = useFetch(`/ventas?desde=${desde}&hasta=${hasta}`, [refreshKey])
  useRealtimeEvent(EVENTOS, ventasQ.refetch)

  const ventas = Array.isArray(ventasQ.data) ? ventasQ.data : []
  const total = ventas.reduce((a, v) => a + Number(v.total), 0)

  // Solo se puede borrar/editar una venta de HOY (Colombia) y mía (o si soy admin, cualquiera).
  const puedoModificar = (v) =>
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
                    <BadgeFiscal fiscal={v.fiscal} className="text-[10px] h-5 px-1.5 shrink-0" />
                    {v.estado === 'anulada' && (
                      <Badge variant="outline" className="text-[10px] h-5 px-1.5 bg-destructive/10 text-destructive border-destructive/20 shrink-0">anulada</Badge>
                    )}
                    <span className="ml-auto text-[13px] font-semibold tabular shrink-0">{cop(Number(v.total))}</span>
                  </button>
                  {/* Editar/borrar: solo ventas de HOY propias (o admin). Días anteriores/ajenas: sin botón. */}
                  {puedoModificar(v) && (
                    <div className="flex items-center shrink-0 mr-2">
                      <button
                        onClick={() => { setEditando(e => (e === v.id ? null : v.id)); setExpandido(null) }}
                        aria-label={`Editar venta N.º ${v.consecutivo}`}
                        title="Editar venta"
                        className="size-8 grid place-items-center rounded-md text-muted-foreground hover:text-primary"
                      >
                        <Pencil className="size-4" />
                      </button>
                      <button
                        onClick={() => borrar(v)}
                        aria-label={`Borrar venta N.º ${v.consecutivo}`}
                        title="Borrar venta"
                        className="size-8 grid place-items-center rounded-md text-muted-foreground hover:text-destructive"
                      >
                        <Trash2 className="size-4" />
                      </button>
                    </div>
                  )}
                </div>
                {editando === v.id ? (
                  <EditarVenta
                    ventaId={v.id}
                    onClose={() => setEditando(null)}
                    onSaved={() => { setEditando(null); ventasQ.refetch() }}
                  />
                ) : (
                  expandido === v.id && <DetalleVenta ventaId={v.id} />
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

// EditarVenta — carga la venta (GET /ventas/{id}) y monta el form prellenado.
function EditarVenta({ ventaId, onClose, onSaved }) {
  const { data, loading, error } = useFetch(`/ventas/${ventaId}`, [])
  if (loading) return <div className="px-9 py-3 text-xs text-muted-foreground">Cargando venta…</div>
  if (error || !data) return <div className="px-9 py-3 text-xs text-destructive">No se pudo cargar la venta.</div>
  return <EditarVentaForm venta={data} onClose={onClose} onSaved={onSaved} />
}

// EditarVentaForm — edita líneas (cantidad/precio/quitar), método y cliente; PUT /ventas/{id}.
function EditarVentaForm({ venta, onClose, onSaved }) {
  const [metodo, setMetodo] = useState(venta.metodo_pago)
  const [clienteId, setClienteId] = useState(venta.cliente_id ?? null)
  const [lineas, setLineas] = useState(() => venta.lineas.map(l => ({
    producto_id: l.producto_id, descripcion: l.descripcion,
    cantidad: String(Number(l.cantidad)), precio_unitario: String(Number(l.precio_unitario)),
  })))
  const [enviando, setEnviando] = useState(false)

  const setLinea = (i, k, val) => setLineas(prev => prev.map((l, j) => (j === i ? { ...l, [k]: val } : l)))
  const quitarLinea = (i) => setLineas(prev => prev.filter((_, j) => j !== i))
  const total = lineas.reduce((a, l) => a + (Number(l.precio_unitario) || 0) * (Number(l.cantidad) || 0), 0)

  async function guardar() {
    if (lineas.length === 0) { toast.error('La venta debe tener al menos una línea'); return }
    const payload = {
      metodo_pago: metodo,
      lineas: lineas.map(l => (l.producto_id != null
        ? { producto_id: l.producto_id, cantidad: Number(l.cantidad), precio_unitario: Number(l.precio_unitario) }
        : { descripcion: l.descripcion, cantidad: Number(l.cantidad), precio_unitario: Number(l.precio_unitario) })),
    }
    if (clienteId != null) payload.cliente_id = clienteId
    setEnviando(true)
    try {
      const res = await api(`/ventas/${venta.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      if (res.ok) { toast.success('Venta actualizada'); onSaved() }
      else if (res.status === 409) toast.error('Tiene factura electrónica, no se puede editar')
      else if (res.status === 403) toast.error('No puedes editar esta venta')
      else if (res.status === 404) toast.error('Producto o venta no encontrado')
      else toast.error('No se pudo editar la venta')
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <div className="px-9 py-3 bg-surface-2/40 border-t border-border-subtle space-y-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-[10px] uppercase tracking-wider text-muted-foreground">Método</label>
        <select value={metodo} onChange={(e) => setMetodo(e.target.value)} aria-label="Método de pago"
          className="h-8 px-2 rounded-md border border-border bg-surface text-sm capitalize">
          {METODOS.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        {clienteId != null && (
          <button onClick={() => setClienteId(null)} className="text-[11px] text-muted-foreground hover:text-foreground">
            Quitar cliente
          </button>
        )}
      </div>

      <ul className="space-y-1.5">
        {lineas.map((l, i) => (
          <li key={i} className="flex flex-wrap items-center gap-2">
            <span className="flex-1 min-w-[120px] truncate text-[12px]">{l.descripcion || `Producto ${l.producto_id}`}</span>
            <Input type="number" min="0" step="any" value={l.cantidad} onChange={(e) => setLinea(i, 'cantidad', e.target.value)}
              aria-label={`Cantidad línea ${i + 1}`} className="w-20 h-8 text-center" />
            <Input type="number" min="0" step="any" value={l.precio_unitario} onChange={(e) => setLinea(i, 'precio_unitario', e.target.value)}
              aria-label={`Precio línea ${i + 1}`} className="w-28 h-8" />
            <button onClick={() => quitarLinea(i)} aria-label={`Quitar línea ${i + 1}`}
              className="text-[11px] text-destructive hover:underline">Quitar</button>
          </li>
        ))}
      </ul>

      <div className="flex items-center gap-3 pt-1">
        <span className="text-[12px] text-muted-foreground">Total <span className="tabular font-semibold text-foreground">{cop(total)}</span></span>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={onClose} className="text-xs text-muted-foreground hover:text-foreground px-2 h-8">Cancelar</button>
          <button onClick={guardar} disabled={enviando}
            className="text-xs px-3 h-8 rounded-md bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-60">
            {enviando ? 'Guardando…' : 'Guardar cambios'}
          </button>
        </div>
      </div>
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
      {data.fiscal && <DetalleFiscal fiscal={data.fiscal} />}
    </div>
  )
}

// Bloque fiscal del detalle: badge + número (prefijo-consecutivo) + CUDE/CUFE.
function DetalleFiscal({ fiscal }) {
  const tieneNumero = fiscal.numero != null || fiscal.prefijo
  return (
    <div className="mt-2 pt-2 border-t border-border-subtle space-y-1 text-[11px] text-muted-foreground">
      <div className="flex items-center gap-2 flex-wrap">
        <BadgeFiscal fiscal={fiscal} className="text-[10px] h-5 px-1.5" />
        {tieneNumero && (
          <span className="tabular">N.º {fiscal.prefijo ? `${fiscal.prefijo}-` : ''}{fiscal.numero ?? '—'}</span>
        )}
      </div>
      {fiscal.cufe && (
        <div className="break-all">
          <span className="uppercase tracking-wider">{etiquetaIdentificador(fiscal.tipo)}:</span> {fiscal.cufe}
        </div>
      )}
    </div>
  )
}
