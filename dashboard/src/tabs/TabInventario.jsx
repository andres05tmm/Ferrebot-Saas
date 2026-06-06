/*
 * TabInventario — catálogo con CRUD para admin (Fase 12, Slice 1; sobre el solo-lectura de E6).
 * Lista/búsqueda: GET /productos (?q, ?activo=true por defecto, limite/offset → "cargar más").
 * Stock: GET /inventario/stock. Admin: nuevo/editar (POST/PUT /productos), eliminar (DELETE = soft,
 * con confirmación) y ajuste de stock (POST /inventario/ajuste). El vendedor sigue en solo-lectura.
 * Live: re-fetch ante inventario_actualizado / reconnected.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { Pencil, Plus, Search, SlidersHorizontal, Trash2 } from 'lucide-react'
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
  const [editando, setEditando] = useState(null) // null | 'nuevo' | producto

  const cargar = useCallback(async (busqueda, off, append) => {
    setLoading(true)
    // activo=true: los productos inactivos (soft-deleted) no salen en el listado por defecto.
    const params = new URLSearchParams({ limite: String(LIMITE), offset: String(off), activo: 'true' })
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

  const recargar = useCallback(() => { setOffset(0); cargar(q, 0, false) }, [q, cargar])

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

  async function eliminar(producto) {
    if (!window.confirm(`¿Eliminar "${producto.nombre}"? Dejará de aparecer en el catálogo.`)) return
    try {
      const res = await api(`/productos/${producto.id}`, { method: 'DELETE' })
      if (res.ok) { toast.success('Producto eliminado'); recargar(); stockQ.refetch() }
      else toast.error('No se pudo eliminar el producto')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar producto por nombre o código…"
              aria-label="Buscar producto"
              className="pl-9"
            />
          </div>
          {admin && (
            <button onClick={() => setEditando('nuevo')}
              className="inline-flex items-center gap-1.5 text-xs px-3 h-9 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover shrink-0">
              <Plus className="size-4" /> Nuevo producto
            </button>
          )}
        </div>
      </Card>

      {admin && editando && (
        <ProductoForm
          producto={editando === 'nuevo' ? null : editando}
          onClose={() => setEditando(null)}
          onSaved={() => { setEditando(null); recargar(); stockQ.refetch() }}
        />
      )}

      <Card className="p-0 overflow-hidden">
        {loading && productos.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : productos.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Sin productos.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {productos.map(p => (
              <ProductoRow key={p.id} producto={p} stock={stockMap.get(p.id)} admin={admin}
                onAjustado={() => { recargar(); stockQ.refetch() }}
                onEditar={() => setEditando(p)}
                onEliminar={() => eliminar(p)} />
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

// Stock negativo: aviso SUAVE (ámbar atenuado, sin rojo ni ⚠️). El número honesto + "por cuadrar".
const TOOLTIP_POR_CUADRAR =
  'Vendiste más de lo registrado; registra tus compras o haz un conteo físico para cuadrar.'

function ProductoRow({ producto, stock, admin, onAjustado, onEditar, onEliminar }) {
  const [abierto, setAbierto] = useState(false)
  const stockActual = stock ? Number(stock.stock_actual) : null
  const bajo = stock?.bajo
  const negativo = stockActual !== null && stockActual < 0

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
            negativo ? (
              <div className="inline-flex items-center justify-end gap-1 text-[11px] tabular text-warning/80"
                title={TOOLTIP_POR_CUADRAR}>
                <span>{num(stockActual)} {producto.unidad_medida}</span>
                <span className="text-[9px] font-normal px-1 py-px rounded bg-warning/10 border border-warning/20 text-warning/80">
                  por cuadrar
                </span>
              </div>
            ) : (
              <div className={`text-[11px] tabular ${bajo ? 'text-warning font-semibold' : 'text-muted-foreground'}`}>
                {num(stockActual)} {producto.unidad_medida}
              </div>
            )
          )}
        </div>
        {admin && (
          <div className="flex items-center gap-1 shrink-0">
            <button onClick={() => setAbierto(a => !a)} title="Ajustar stock"
              className="size-8 grid place-items-center rounded-md border border-border bg-surface text-muted-foreground hover:text-foreground hover:bg-surface-2">
              <SlidersHorizontal className="size-4" />
            </button>
            <button onClick={onEditar} title="Editar producto"
              className="size-8 grid place-items-center rounded-md border border-border bg-surface text-muted-foreground hover:text-foreground hover:bg-surface-2">
              <Pencil className="size-4" />
            </button>
            <button onClick={onEliminar} title="Eliminar producto"
              className="size-8 grid place-items-center rounded-md border border-border bg-surface text-muted-foreground hover:text-destructive hover:bg-surface-2">
              <Trash2 className="size-4" />
            </button>
          </div>
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
  const [contada, setContada] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState('')

  // Conteo físico (set-to-absolute): el admin escribe el número REAL contado; el backend calcula el
  // delta y deja el stock en ese valor (así se cuadran los negativos). POST /inventario/conteo.
  async function ajustarAReal() {
    if (contada === '' || !(Number(contada) >= 0)) { setError('Indica la cantidad real contada (0 o más).'); return }
    setEnviando(true); setError('')
    try {
      const res = await api('/inventario/conteo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ producto_id: productoId, cantidad_contada: Number(contada), motivo: 'conteo físico' }),
      })
      if (res.ok) {
        const r = await res.json().catch(() => null)
        if (r) toast.success(`Stock ajustado a ${num(Number(r.stock_actual))}`)   // muestra el stock resultante
        onDone()
      } else setError('No se pudo ajustar a la cantidad contada.')
    } catch { setError('Error de conexión.') } finally { setEnviando(false) }
  }

  // Ajuste por delta (complementario): +sobrante / −merma con su motivo. POST /inventario/ajuste.
  async function guardarDelta() {
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
    } catch { setError('Error de conexión.') } finally { setEnviando(false) }
  }

  return (
    <div className="mt-2.5 space-y-2 bg-surface-2/50 rounded-md p-2">
      <div>
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Conteo físico · ajustar a cantidad real</p>
        <div className="flex flex-wrap items-center gap-2">
          <Input type="number" value={contada} onChange={(e) => setContada(e.target.value)}
            placeholder="Cantidad real contada" aria-label="Cantidad real contada" className="w-40 h-8" />
          <button onClick={ajustarAReal} disabled={enviando}
            className="text-xs px-3 h-8 rounded-md bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-60">
            {enviando ? 'Guardando…' : 'Ajustar a real'}
          </button>
        </div>
      </div>

      <div className="pt-2 border-t border-border-subtle">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Ajuste por delta (+/−)</p>
        <div className="flex flex-wrap items-center gap-2">
          <Input type="number" value={delta} onChange={(e) => setDelta(e.target.value)}
            placeholder="Delta (+/-)" aria-label="Delta de ajuste" className="w-28 h-8" />
          <Input value={motivo} onChange={(e) => setMotivo(e.target.value)}
            placeholder="Motivo" aria-label="Motivo del ajuste" className="flex-1 min-w-[120px] h-8" />
          <button onClick={guardarDelta} disabled={enviando}
            className="text-xs px-3 h-8 rounded-md border border-border bg-surface hover:bg-surface-2 disabled:opacity-60">
            {enviando ? 'Guardando…' : 'Guardar'}
          </button>
        </div>
      </div>

      {error && <span className="block text-[11px] text-destructive">{error}</span>}
    </div>
  )
}

// ── ProductoForm — alta (POST) / edición (PUT) ────────────────────────────────
const NUEVA_CATEGORIA = '__nueva__'
const SELECT_CLS = 'h-9 w-full px-2 rounded-md border border-border bg-surface text-sm'

const FORM_VACIO = {
  nombre: '', codigo: '', categoria: '', categoriaNueva: '', proveedor_id: '',
  unidad_medida: 'unidad',
  precio_venta: '', precio_compra: '', precio_especial: '',
  precio_umbral: '', precio_bajo_umbral: '', precio_sobre_umbral: '',
  iva: '19', permite_fraccion: false, activo: true, fracciones: [],
}

function desdeProducto(p) {
  const s = (v) => (v === null || v === undefined ? '' : String(v))
  return {
    ...FORM_VACIO,
    nombre: s(p.nombre), codigo: s(p.codigo), categoria: s(p.categoria),
    proveedor_id: s(p.proveedor_id), unidad_medida: s(p.unidad_medida) || 'unidad',
    precio_venta: s(p.precio_venta), precio_compra: s(p.precio_compra),
    precio_especial: s(p.precio_especial),
    precio_umbral: s(p.precio_umbral), precio_bajo_umbral: s(p.precio_bajo_umbral),
    precio_sobre_umbral: s(p.precio_sobre_umbral), iva: s(p.iva) || '19',
    permite_fraccion: !!p.permite_fraccion, activo: p.activo !== false, fracciones: [],
  }
}

// `incluirEscalonado`: el bloque de umbral solo se envía si está desplegado (si no, el backend lo
// deja en NULL). El proveedor viaja como id (de la lista registrada); nunca como texto libre.
function construirPayload(f, { incluirEscalonado }) {
  const dec = (v) => (v === '' || v === null || v === undefined ? null : Number(v))
  const categoria = (f.categoria === NUEVA_CATEGORIA ? f.categoriaNueva : f.categoria).trim() || null
  const payload = {
    nombre: f.nombre.trim(),
    codigo: f.codigo.trim() || null,
    categoria,
    proveedor_id: f.proveedor_id ? Number(f.proveedor_id) : null,
    unidad_medida: f.unidad_medida.trim() || 'unidad',
    precio_venta: Number(f.precio_venta || 0),
    precio_compra: dec(f.precio_compra),
    precio_especial: dec(f.precio_especial),
    iva: Number(f.iva || 0),
    permite_fraccion: !!f.permite_fraccion,
    activo: !!f.activo,
    fracciones: f.fracciones
      .filter(fr => fr.fraccion.trim())
      .map(fr => ({
        fraccion: fr.fraccion.trim(),
        decimal: dec(fr.decimal),
        precio_total: Number(fr.precio_total || 0),
        precio_unitario: dec(fr.precio_unitario),
      })),
  }
  if (incluirEscalonado) {
    payload.precio_umbral = dec(f.precio_umbral)
    payload.precio_bajo_umbral = dec(f.precio_bajo_umbral)
    payload.precio_sobre_umbral = dec(f.precio_sobre_umbral)
  }
  return payload
}

function tieneEscalonado(p) {
  return !!p && (p.precio_umbral != null || p.precio_bajo_umbral != null || p.precio_sobre_umbral != null)
}

function ProductoForm({ producto, onClose, onSaved }) {
  const esEdicion = !!producto
  const [f, setF] = useState(() => (producto ? desdeProducto(producto) : FORM_VACIO))
  const [enviando, setEnviando] = useState(false)
  // Escalonado colapsado por defecto; al editar, abierto si el producto ya trae umbral.
  const [escalonado, setEscalonado] = useState(() => tieneEscalonado(producto))

  // Desplegables: categorías existentes (+ "nueva") y proveedores registrados (no texto libre).
  const categoriasQ = useFetch('/productos/categorias')
  const proveedoresQ = useFetch('/proveedores')
  const categorias = Array.isArray(categoriasQ.data) ? categoriasQ.data : []
  const proveedores = Array.isArray(proveedoresQ.data) ? proveedoresQ.data : []
  // El valor actual siempre debe existir como opción (p. ej. al editar antes de que cargue el endpoint).
  const opcionesCategoria = [...new Set(
    [...(f.categoria && f.categoria !== NUEVA_CATEGORIA ? [f.categoria] : []), ...categorias],
  )]

  const set = (k) => (e) => setF(prev => ({ ...prev, [k]: e.target.value }))
  const setBool = (k) => (e) => setF(prev => ({ ...prev, [k]: e.target.checked }))

  const setFraccion = (i, k, v) => setF(prev => ({
    ...prev, fracciones: prev.fracciones.map((fr, j) => (j === i ? { ...fr, [k]: v } : fr)),
  }))
  const agregarFraccion = () => setF(prev => ({
    ...prev, fracciones: [...prev.fracciones, { fraccion: '', decimal: '', precio_total: '', precio_unitario: '' }],
  }))
  const quitarFraccion = (i) => setF(prev => ({
    ...prev, fracciones: prev.fracciones.filter((_, j) => j !== i),
  }))

  async function guardar() {
    if (!f.nombre.trim()) { toast.error('El nombre es obligatorio'); return }
    if (!(Number(f.precio_venta) >= 0) || f.precio_venta === '') { toast.error('Indica un precio de venta válido'); return }
    const payload = construirPayload(f, { incluirEscalonado: escalonado })
    setEnviando(true)
    try {
      const res = esEdicion
        ? await api(`/productos/${producto.id}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
          })
        : await api('/productos', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
          })
      if (res.status === 409) { toast.error('Ya existe un producto con ese código'); return }
      if (res.status === 422) { toast.error('Revisa los datos del producto (proveedor o precios)'); return }
      if (!res.ok) { toast.error(esEdicion ? 'No se pudo guardar' : 'No se pudo crear el producto'); return }
      toast.success(esEdicion ? 'Producto actualizado' : 'Producto creado')
      onSaved()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold">{esEdicion ? 'Editar producto' : 'Nuevo producto'}</h2>
        <button onClick={onClose} className="text-xs text-muted-foreground hover:text-foreground">Cancelar</button>
      </div>

      <div className="space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <Input value={f.nombre} onChange={set('nombre')} placeholder="Nombre *" aria-label="Nombre" className="h-9" />
          <Input value={f.codigo} onChange={set('codigo')} placeholder="Código" aria-label="Código" className="h-9" />

          <div className="space-y-1">
            <select value={f.categoria} onChange={set('categoria')} aria-label="Categoría" className={SELECT_CLS}>
              <option value="">Sin categoría</option>
              {opcionesCategoria.map(c => <option key={c} value={c}>{c}</option>)}
              <option value={NUEVA_CATEGORIA}>+ Nueva categoría…</option>
            </select>
            {f.categoria === NUEVA_CATEGORIA && (
              <Input value={f.categoriaNueva} onChange={set('categoriaNueva')}
                placeholder="Nueva categoría" aria-label="Nueva categoría" className="h-9" />
            )}
          </div>

          <div className="space-y-1">
            <select value={f.proveedor_id} onChange={set('proveedor_id')} aria-label="Proveedor" className={SELECT_CLS}>
              <option value="">Sin proveedor</option>
              {proveedores.map(p => <option key={p.id} value={p.id}>{p.nombre}</option>)}
            </select>
            {proveedores.length === 0 && !proveedoresQ.loading && (
              <p className="text-[10px] text-muted-foreground">Registra proveedores en el tab Proveedores.</p>
            )}
          </div>

          <Input value={f.unidad_medida} onChange={set('unidad_medida')} placeholder="Unidad" aria-label="Unidad de medida" className="h-9" />
          <Input type="number" value={f.iva} onChange={set('iva')} placeholder="IVA %" aria-label="IVA" className="h-9" />
        </div>

        <div className="pt-2 border-t border-border-subtle">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2">Precios</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <Input type="number" value={f.precio_venta} onChange={set('precio_venta')} placeholder="Venta *" aria-label="Precio de venta" className="h-9" />
            <Input type="number" value={f.precio_compra} onChange={set('precio_compra')} placeholder="Compra" aria-label="Precio de compra" className="h-9" />
            <Input type="number" value={f.precio_especial} onChange={set('precio_especial')} placeholder="Especial" aria-label="Precio especial" className="h-9" />
          </div>

          {!escalonado ? (
            <button type="button" onClick={() => setEscalonado(true)}
              className="mt-3 text-[11px] text-primary hover:underline">+ Añadir precio escalonado</button>
          ) : (
            <div className="mt-3">
              <div className="flex items-center justify-between mb-2">
                <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Precio escalonado (por cantidad)</p>
                <button type="button" onClick={() => setEscalonado(false)}
                  className="text-[11px] text-destructive hover:underline">Quitar</button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                <Input type="number" value={f.precio_umbral} onChange={set('precio_umbral')} placeholder="Umbral (cantidad)" aria-label="Umbral de cantidad" className="h-9" />
                <Input type="number" value={f.precio_bajo_umbral} onChange={set('precio_bajo_umbral')} placeholder="Precio bajo umbral" aria-label="Precio bajo umbral" className="h-9" />
                <Input type="number" value={f.precio_sobre_umbral} onChange={set('precio_sobre_umbral')} placeholder="Precio sobre umbral" aria-label="Precio sobre umbral" className="h-9" />
              </div>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-4 pt-1">
          <label className="flex items-center gap-2 text-[13px]">
            <input type="checkbox" checked={f.permite_fraccion} onChange={setBool('permite_fraccion')} aria-label="Permite fracción" />
            Permite fracción
          </label>
          {esEdicion && (
            <label className="flex items-center gap-2 text-[13px]">
              <input type="checkbox" checked={f.activo} onChange={setBool('activo')} aria-label="Activo" />
              Activo
            </label>
          )}
        </div>

        {f.permite_fraccion && (
          <div className="pt-2 border-t border-border-subtle">
            <div className="flex items-center justify-between mb-2">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Fracciones</p>
              <button onClick={agregarFraccion} className="text-[11px] text-primary hover:underline">+ Añadir fracción</button>
            </div>
            <div className="space-y-2">
              {f.fracciones.map((fr, i) => (
                <div key={i} className="flex flex-wrap items-center gap-2">
                  <Input value={fr.fraccion} onChange={(e) => setFraccion(i, 'fraccion', e.target.value)}
                    placeholder="Fracción (1/2)" aria-label={`Fracción ${i + 1}`} className="w-28 h-8" />
                  <Input type="number" value={fr.decimal} onChange={(e) => setFraccion(i, 'decimal', e.target.value)}
                    placeholder="Decimal (0.5)" aria-label={`Decimal fracción ${i + 1}`} className="w-28 h-8" />
                  <Input type="number" value={fr.precio_total} onChange={(e) => setFraccion(i, 'precio_total', e.target.value)}
                    placeholder="Precio total" aria-label={`Precio total fracción ${i + 1}`} className="w-32 h-8" />
                  <button onClick={() => quitarFraccion(i)} className="text-[11px] text-destructive hover:underline">Quitar</button>
                </div>
              ))}
            </div>
          </div>
        )}

        <button onClick={guardar} disabled={enviando}
          className="w-full h-10 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover disabled:opacity-60">
          {enviando ? 'Guardando…' : esEdicion ? 'Guardar cambios' : 'Crear producto'}
        </button>
      </div>
    </Card>
  )
}
