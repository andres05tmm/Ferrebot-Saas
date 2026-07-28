/*
 * EditarVenta — el formulario de corrección de una venta, extraído tal cual de VistaDia al
 * reescribirla como tabla renglón por renglón. La lógica no cambió: carga la venta
 * (GET /ventas/{id}), deja tocar líneas, método y cliente, y manda PUT /ventas/{id}.
 *
 * Solo aplica a ventas de HOY: el backend responde 403 fuera de eso, y 409 si la venta tiene
 * factura electrónica viva. Quien lo monta ya filtró por esa regla.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { useFetch, cop } from '@/components/shared.jsx'
import { Input } from '@/components/ui/input.jsx'

// EditarVenta — carga la venta (GET /ventas/{id}) y monta el form prellenado.
export default function EditarVenta({ ventaId, onClose, onSaved }) {
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
        <label className="text-micro uppercase tracking-wider text-muted-foreground">Método</label>
        <select value={metodo} onChange={(e) => setMetodo(e.target.value)} aria-label="Método de pago"
          className="h-8 px-2 rounded-md border border-border bg-surface text-sm capitalize">
          {METODOS.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        {clienteId != null && (
          <button onClick={() => setClienteId(null)} className="text-caption text-muted-foreground hover:text-foreground">
            Quitar cliente
          </button>
        )}
      </div>

      <ul className="space-y-1.5">
        {lineas.map((l, i) => (
          <li key={i} className="flex flex-wrap items-center gap-2">
            <span className="flex-1 min-w-[120px] truncate text-meta">{l.descripcion || `Producto ${l.producto_id}`}</span>
            <Input type="number" min="0" step="any" value={l.cantidad} onChange={(e) => setLinea(i, 'cantidad', e.target.value)}
              aria-label={`Cantidad línea ${i + 1}`} className="w-20 h-8 text-center" />
            <Input type="number" min="0" step="any" value={l.precio_unitario} onChange={(e) => setLinea(i, 'precio_unitario', e.target.value)}
              aria-label={`Precio línea ${i + 1}`} className="w-28 h-8" />
            <button onClick={() => quitarLinea(i)} aria-label={`Quitar línea ${i + 1}`}
              className="text-caption text-destructive hover:underline">Quitar</button>
          </li>
        ))}
      </ul>

      <div className="flex items-center gap-3 pt-1">
        <span className="text-meta text-muted-foreground">Total <span className="tabular font-semibold text-foreground">{cop(total)}</span></span>
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
