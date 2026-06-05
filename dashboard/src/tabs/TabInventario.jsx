/*
 * TabInventario — catálogo SOLO LECTURA (E6, recableado a endpoints SaaS).
 * Lista/búsqueda: GET /productos (?q, limite/offset → "cargar más"). Stock: GET /inventario/stock.
 * Admin: ajuste de stock vía POST /inventario/ajuste (Idempotency-Key); el vendedor no ve ese control.
 * Live: re-fetch ante inventario_actualizado / reconnected.
 * Diferido a Fase 12 (gap de backend): crear/editar/eliminar producto, fracciones, mayorista, kárdex.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Search, SlidersHorizontal } from 'lucide-react'
import { api, apiJson } from '@/lib/api.js'
import { useFetch, cop, num } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const LIMITE = 50

export default function TabInventario() {
  const { refreshKey } = useOutletContext() ?? {}
  const { isAdmin } = useAuth()
  const admin = isAdmin()

  const [q, setQ] = useState('')
  const [productos, setProductos] = useState([])
  const [offset, setOffset] = useState(0)
  const [hayMas, setHayMas] = useState(false)
  const [loading, setLoading] = useState(true)

  const cargar = useCallback(async (busqueda, off, append) => {
    setLoading(true)
    const params = new URLSearchParams({ limite: String(LIMITE), offset: String(off) })
    if (busqueda) params.set('q', busqueda)
    try {
      const data = await apiJson(`/productos?${params.toString()}`)
      const lista = Array.isArray(data) ? data : []
      setProductos(prev => (append ? [...prev, ...lista] : lista))
      setHayMas(lista.length === LIMITE)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    setOffset(0)
    cargar(q, 0, false)
  }, [q, refreshKey, cargar])

  const stockQ = useFetch('/inventario/stock', [refreshKey])
  const stockMap = useMemo(() => {
    const m = new Map()
    for (const s of (Array.isArray(stockQ.data) ? stockQ.data : [])) m.set(s.producto_id, s)
    return m
  }, [stockQ.data])

  useRealtimeEvent(['inventario_actualizado', 'reconnected'], () => {
    setOffset(0); cargar(q, 0, false); stockQ.refetch()
  })

  function cargarMas() {
    const next = offset + LIMITE
    setOffset(next)
    cargar(q, next, true)
  }

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="relative">
          <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar producto por nombre o código…"
            aria-label="Buscar producto"
            className="pl-9"
          />
        </div>
      </Card>

      <Card className="p-0 overflow-hidden">
        {loading && productos.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : productos.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Sin productos.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {productos.map(p => (
              <ProductoRow key={p.id} producto={p} stock={stockMap.get(p.id)} admin={admin}
                onAjustado={() => { cargar(q, 0, false); stockQ.refetch() }} />
            ))}
          </ul>
        )}
      </Card>

      {hayMas && (
        <div className="flex justify-center">
          <button onClick={cargarMas}
            className="text-xs px-4 py-2 rounded-md border border-border bg-surface hover:bg-surface-2 transition-colors">
            Cargar más
          </button>
        </div>
      )}
    </div>
  )
}

function ProductoRow({ producto, stock, admin, onAjustado }) {
  const [abierto, setAbierto] = useState(false)
  const stockActual = stock ? Number(stock.stock_actual) : null
  const bajo = stock?.bajo

  return (
    <li className="px-3.5 py-2.5">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-medium truncate">{producto.nombre}</span>
            {!producto.activo && <Badge variant="outline" className="h-4 text-[9px] text-muted-foreground">inactivo</Badge>}
          </div>
          <div className="text-[11px] text-muted-foreground truncate">
            {[producto.codigo, producto.categoria].filter(Boolean).join(' · ') || '—'}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-[13px] font-semibold tabular">{cop(Number(producto.precio_venta))}</div>
          {stockActual !== null && (
            <div className={`text-[11px] tabular ${bajo ? 'text-warning font-semibold' : 'text-muted-foreground'}`}>
              {num(stockActual)} {producto.unidad_medida}
            </div>
          )}
        </div>
        {admin && (
          <button onClick={() => setAbierto(a => !a)} title="Ajustar stock"
            className="size-8 grid place-items-center rounded-md border border-border bg-surface text-muted-foreground hover:text-foreground hover:bg-surface-2 shrink-0">
            <SlidersHorizontal className="size-4" />
          </button>
        )}
      </div>
      {admin && abierto && (
        <AjusteForm productoId={producto.id} onDone={() => { setAbierto(false); onAjustado() }} />
      )}
    </li>
  )
}

function AjusteForm({ productoId, onDone }) {
  const [delta, setDelta] = useState('')
  const [motivo, setMotivo] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState('')

  async function guardar() {
    const n = Number(delta)
    if (!n || !motivo.trim()) { setError('Indica un delta distinto de 0 y un motivo.'); return }
    setEnviando(true); setError('')
    try {
      const res = await api('/inventario/ajuste', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ producto_id: productoId, cantidad: n, motivo: motivo.trim() }),
      })
      if (res.ok) onDone()
      else setError('No se pudo ajustar el stock.')
    } catch {
      setError('Error de conexión.')
    } finally {
      setEnviando(false)
    }
  }

  return (
    <div className="mt-2.5 flex flex-wrap items-center gap-2 bg-surface-2/50 rounded-md p-2">
      <Input type="number" value={delta} onChange={(e) => setDelta(e.target.value)}
        placeholder="Delta (+/-)" aria-label="Delta de ajuste" className="w-28 h-8" />
      <Input value={motivo} onChange={(e) => setMotivo(e.target.value)}
        placeholder="Motivo" aria-label="Motivo del ajuste" className="flex-1 min-w-[120px] h-8" />
      <button onClick={guardar} disabled={enviando}
        className="text-xs px-3 h-8 rounded-md bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-60">
        {enviando ? 'Guardando…' : 'Guardar'}
      </button>
      {error && <span className="w-full text-[11px] text-destructive">{error}</span>}
    </div>
  )
}
