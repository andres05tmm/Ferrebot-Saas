/*
 * TabCompras — registrar compras a proveedor + historial (Fase 12, Slice 4a). SOLO admin.
 * Registrar: proveedor (nombre/nit) + items (buscar producto vía /productos?q, cantidad, costo) →
 * POST /compras (suma stock y fija el costo de compra en el backend). Lista del rango (default mes).
 * Live: re-fetch ante 'compra_registrada' / 'inventario_actualizado' / 'reconnected'. Datos por api.js.
 */
import { useEffect, useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { Plus, Search, Trash2, Truck } from 'lucide-react'
import { api, apiJson } from '@/lib/api'
import { useFetch, cop, num, mesActualCO } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'

const FECHA_CO = { day: '2-digit', month: 'short', timeZone: 'America/Bogota' }

export default function TabCompras() {
  const { isAdmin } = useAuth()
  // Compras (registro y listado) es admin-only en el backend: el tab se gatea igual para el vendedor.
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        Las compras son solo para administradores.
      </Card>
    )
  }
  return <ComprasContenido />
}

function ComprasContenido() {
  const { refreshKey } = useOutletContext() ?? {}
  const [rango, setRango] = useState(mesActualCO())
  const setCampoRango = (k) => (e) => setRango(prev => ({ ...prev, [k]: e.target.value }))

  const comprasQ = useFetch(`/compras?desde=${rango.desde}&hasta=${rango.hasta}`, [refreshKey, rango.desde, rango.hasta])
  useRealtimeEvent(['compra_registrada', 'inventario_actualizado', 'reconnected'], comprasQ.refetch)

  const compras = Array.isArray(comprasQ.data) ? comprasQ.data : []

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <RegistrarCompra onRegistrada={comprasQ.refetch} />

      <Card className="p-0 overflow-hidden">
        <div className="px-3.5 py-2.5 border-b border-border-subtle flex items-center gap-2">
          <Truck className="size-4 text-muted-foreground" />
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mr-auto">Compras</h2>
          <Input type="date" value={rango.desde} onChange={setCampoRango('desde')} aria-label="Desde" className="h-7 w-[8.5rem] text-[11px]" />
          <Input type="date" value={rango.hasta} onChange={setCampoRango('hasta')} aria-label="Hasta" className="h-7 w-[8.5rem] text-[11px]" />
        </div>
        {comprasQ.loading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : compras.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Sin compras en el periodo.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {compras.map(c => (
              <li key={c.id} className="px-3.5 py-2.5 flex items-center gap-3 text-[13px]">
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{c.proveedor_nombre || 'Proveedor'}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {c.fecha ? new Date(c.fecha).toLocaleDateString('es-CO', FECHA_CO) : '—'}
                  </div>
                </div>
                <span className="tabular font-semibold shrink-0">{cop(Number(c.total))}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

function RegistrarCompra({ onRegistrada }) {
  const [proveedor, setProveedor] = useState({ nombre: '', nit: '' })
  const [items, setItems] = useState([])
  const [enviando, setEnviando] = useState(false)
  const setProv = (k) => (e) => setProveedor(prev => ({ ...prev, [k]: e.target.value }))

  const total = useMemo(
    () => items.reduce((a, it) => a + Number(it.cantidad) * Number(it.costo), 0),
    [items],
  )

  function agregarItem(item) {
    setItems(prev => [...prev, item])
  }
  function quitarItem(i) {
    setItems(prev => prev.filter((_, j) => j !== i))
  }

  async function registrar() {
    if (!proveedor.nombre.trim()) { toast.error('Indica el proveedor'); return }
    if (items.length === 0) { toast.error('Agrega al menos un item'); return }
    const payload = {
      proveedor: { nombre: proveedor.nombre.trim(), nit: proveedor.nit.trim() || null },
      items: items.map(it => ({ producto_id: it.producto_id, cantidad: Number(it.cantidad), costo: Number(it.costo) })),
    }
    setEnviando(true)
    try {
      const res = await api('/compras', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      if (res.ok) {
        toast.success('Compra registrada')
        setProveedor({ nombre: '', nit: '' })
        setItems([])
        onRegistrada()
      } else {
        toast.error('No se pudo registrar la compra')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <h2 className="text-sm font-semibold mb-3 inline-flex items-center gap-1.5">
        <Truck className="size-4" /> Nueva compra
      </h2>
      <div className="space-y-2">
        <Input value={proveedor.nombre} onChange={setProv('nombre')} placeholder="Proveedor *" aria-label="Proveedor" className="h-9" />
        <Input value={proveedor.nit} onChange={setProv('nit')} placeholder="NIT (opcional)" aria-label="NIT del proveedor" className="h-9" />
      </div>

      <ItemEditor onAgregar={agregarItem} />

      {items.length > 0 && (
        <ul className="mt-3 divide-y divide-border-subtle border-t border-border-subtle">
          {items.map((it, i) => (
            <li key={i} className="py-2 flex items-center gap-2 text-[12px]">
              <span className="min-w-0 flex-1 truncate">{it.nombre}</span>
              <span className="tabular text-muted-foreground">{num(Number(it.cantidad))} × {cop(Number(it.costo))}</span>
              <button onClick={() => quitarItem(i)} title="Quitar item"
                className="size-7 grid place-items-center rounded-md text-muted-foreground hover:text-destructive">
                <Trash2 className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 flex items-center justify-between">
        <span className="text-[12px] text-muted-foreground">Total</span>
        <span className="tabular font-semibold">{cop(total)}</span>
      </div>

      <button onClick={registrar} disabled={enviando}
        className="w-full mt-3 h-10 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover disabled:opacity-60">
        {enviando ? 'Registrando…' : 'Registrar compra'}
      </button>
    </Card>
  )
}

function ItemEditor({ onAgregar }) {
  const [producto, setProducto] = useState(null)
  const [cantidad, setCantidad] = useState('')
  const [costo, setCosto] = useState('')

  function agregar() {
    if (!producto) { toast.error('Busca y elige un producto'); return }
    if (!(Number(cantidad) > 0) || !(Number(costo) >= 0)) { toast.error('Cantidad y costo válidos'); return }
    onAgregar({ producto_id: producto.id, nombre: producto.nombre, cantidad, costo })
    setProducto(null); setCantidad(''); setCosto('')
  }

  return (
    <div className="mt-3 pt-3 border-t border-border-subtle space-y-2">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Item de compra</p>
      {producto ? (
        <div className="flex items-center gap-2 text-[12px] bg-surface-2/50 rounded-md px-2.5 py-1.5">
          <span className="flex-1 truncate font-medium">{producto.nombre}</span>
          <button onClick={() => setProducto(null)} className="text-[11px] text-muted-foreground hover:text-foreground">cambiar</button>
        </div>
      ) : (
        <BuscadorProducto onElegir={setProducto} />
      )}
      <div className="flex gap-2">
        <Input type="number" value={cantidad} onChange={(e) => setCantidad(e.target.value)} placeholder="Cantidad" aria-label="Cantidad" className="h-9 flex-1" />
        <Input type="number" value={costo} onChange={(e) => setCosto(e.target.value)} placeholder="Costo unit." aria-label="Costo unitario" className="h-9 flex-1" />
        <button onClick={agregar}
          className="inline-flex items-center gap-1 text-xs px-3 h-9 rounded-md border border-border bg-surface hover:bg-surface-2 shrink-0">
          <Plus className="size-4" /> Agregar item
        </button>
      </div>
    </div>
  )
}

function BuscadorProducto({ onElegir }) {
  const [q, setQ] = useState('')
  const [resultados, setResultados] = useState([])

  useEffect(() => {
    if (!q.trim()) { setResultados([]); return undefined }
    let cancelado = false
    apiJson(`/productos?q=${encodeURIComponent(q.trim())}&limite=8`)
      .then(d => { if (!cancelado) setResultados(Array.isArray(d) ? d : []) })
      .catch(() => { if (!cancelado) setResultados([]) })
    return () => { cancelado = true }
  }, [q])

  return (
    <div className="space-y-1.5">
      <div className="relative">
        <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar producto…" aria-label="Buscar producto" className="pl-9 h-9" />
      </div>
      {resultados.length > 0 && (
        <ul className="divide-y divide-border-subtle max-h-40 overflow-y-auto rounded-md border border-border-subtle">
          {resultados.map(p => (
            <li key={p.id}>
              <button onClick={() => { onElegir(p); setQ(''); setResultados([]) }}
                className="w-full text-left px-2.5 py-1.5 text-[12px] hover:bg-surface-2 truncate">
                {p.nombre}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
