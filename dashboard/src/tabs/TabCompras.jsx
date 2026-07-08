/*
 * TabCompras — registrar compras a proveedor + historial (Fase 12, Slice 4a). SOLO admin.
 *
 * Dos naturalezas de compra conviven, gateadas por familia (esConstruccion):
 *   - CATÁLOGO (retail y default): proveedor + items con `producto_id` → POST /compras suma stock y fija
 *     el costo. Para el POS no cambia NADA: el tab se ve y opera igual que siempre.
 *   - OBRA / VIAJE (solo familia construcción): la constructora compra "viajes de material"
 *     (asfalto/arena a una planta) imputados a una obra y, cuando se revenden al cliente, marcados como
 *     `es_viaje_material` con `precio_venta_cliente` → el backend calcula el RESBALO (margen = venta −
 *     costo). Esas compras NO mueven inventario y no llevan producto de catálogo. El form muestra un
 *     preview del resbalo en vivo; la lista pinta obra, categoría y el resbalo con su semáforo.
 *
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
import { esConstruccion, useFeatures } from '@/lib/features.jsx'
import { Semaforo, Campo, SELECT_CLS } from './construccion/comunes.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'

const FECHA_CO = { day: '2-digit', month: 'short', timeZone: 'America/Bogota' }

// Categorías de compra del vertical construcción → etiqueta legible. Los VALORES calcan los literales
// EXACTOS de modules/compras/schemas.py::CategoriaCompra (no traducir el valor, solo el label).
const CATEGORIAS = [
  ['MEZCLA_ASFALTICA', 'Mezcla asfáltica'],
  ['EMULSION_ASFALTICA', 'Emulsión asfáltica'],
  ['ARENA_AGREGADO', 'Arena / agregado'],
  ['REPUESTO', 'Repuesto'],
  ['COMBUSTIBLE_GENERAL', 'Combustible'],
  ['TRANSPORTE', 'Transporte'],
  ['SERVICIO_MANTENIMIENTO', 'Servicio / mantenimiento'],
  ['OTRO', 'Otro'],
]
const CAT_LABEL = Object.fromEntries(CATEGORIAS)

// Umbral de margen "sano" del viaje de material (espeja `resbalo_alerta` del backend: <5% o negativo).
const RESBALO_MIN_PCT = 5

// Chip neutro para metadatos de la fila (obra, categoría). Pill de token, sin color de estado.
const CHIP = 'inline-flex items-center rounded-full border border-border bg-surface-2 px-2 py-0.5 text-[10px] font-medium text-muted-foreground'

// Idempotencia (regla 8): la compra es una operación crítica. Cada envío lleva una key nueva; un
// reintento de red reusa la misma (misma key + mismo payload → la compra original, no un duplicado).
function nuevaIdemKey() {
  try { return crypto.randomUUID() } catch { return `c-${Date.now()}-${Math.random().toString(16).slice(2)}` }
}

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
  const construccion = esConstruccion(useFeatures())
  const [rango, setRango] = useState(mesActualCO())
  const setCampoRango = (k) => (e) => setRango(prev => ({ ...prev, [k]: e.target.value }))

  const comprasQ = useFetch(`/compras?desde=${rango.desde}&hasta=${rango.hasta}`, [refreshKey, rango.desde, rango.hasta])
  useRealtimeEvent(['compra_registrada', 'inventario_actualizado', 'reconnected'], comprasQ.refetch)

  // Obras solo en la familia construcción: nombra la imputación de cada compra y alimenta el selector del
  // form. Path null cuando no aplica ⇒ useFetch queda en reposo (sin llamada que daría 404 en retail).
  const obrasQ = useFetch(construccion ? '/obras' : null, [construccion, refreshKey])
  const obras = Array.isArray(obrasQ.data) ? obrasQ.data : []
  // Nombre por id desde TODAS las obras (incluidas archivadas) para resolver el histórico; el selector del
  // form filtra aparte a las vigentes.
  const obraNombre = useMemo(() => Object.fromEntries(obras.map(o => [o.id, o.nombre])), [obras])
  const obrasVigentes = useMemo(() => obras.filter(o => o.estado !== 'archivada'), [obras])

  const compras = Array.isArray(comprasQ.data) ? comprasQ.data : []

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <RegistrarCompra construccion={construccion} obras={obrasVigentes} onRegistrada={comprasQ.refetch} />

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
              <CompraFila key={c.id} compra={c} construccion={construccion} obraNombre={obraNombre} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

/** Fila del histórico. En retail queda idéntica (proveedor · fecha · total). En construcción apila abajo
 *  los metadatos: obra imputada, categoría y —si es viaje— el resbalo con su semáforo por `resbalo_alerta`
 *  (nunca color solo: el texto lleva el monto y el %). Los chips envuelven para no romper el ancho móvil. */
function CompraFila({ compra: c, construccion, obraNombre }) {
  return (
    <li className={`px-3.5 py-2.5 flex gap-3 text-[13px] ${construccion ? 'items-start' : 'items-center'}`}>
      <div className="min-w-0 flex-1">
        <div className="font-medium truncate">{c.proveedor_nombre || 'Proveedor'}</div>
        <div className="text-[11px] text-muted-foreground">
          {c.fecha ? new Date(c.fecha).toLocaleDateString('es-CO', FECHA_CO) : '—'}
        </div>
        {construccion && (c.obra_id != null || c.categoria || c.es_viaje_material) && (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <span className={CHIP}>{c.obra_id != null ? (obraNombre[c.obra_id] || `Obra #${c.obra_id}`) : 'Sin obra'}</span>
            {c.categoria && <span className={CHIP}>{CAT_LABEL[c.categoria] || c.categoria}</span>}
            {c.es_viaje_material && (
              <Semaforo tono={c.resbalo_alerta ? 'ambar' : 'verde'}>
                Resbalo {cop(Number(c.resbalo))}{c.resbalo_pct != null ? ` · ${Math.round(Number(c.resbalo_pct))}%` : ''}
              </Semaforo>
            )}
          </div>
        )}
      </div>
      <span className="tabular font-semibold shrink-0">{cop(Number(c.total))}</span>
    </li>
  )
}

const MODOS = [
  { valor: 'catalogo', label: 'Catálogo' },
  { valor: 'obra', label: 'Obra / viaje' },
]

function RegistrarCompra({ construccion, obras, onRegistrada }) {
  const [modo, setModo] = useState('catalogo')
  const [proveedor, setProveedor] = useState({ nombre: '', nit: '' })
  const [items, setItems] = useState([])
  const [obraId, setObraId] = useState('')
  const [categoria, setCategoria] = useState('')
  const [esViaje, setEsViaje] = useState(false)
  const [precioVenta, setPrecioVenta] = useState('')
  const [enviando, setEnviando] = useState(false)
  const setProv = (k) => (e) => setProveedor(prev => ({ ...prev, [k]: e.target.value }))

  const esObra = construccion && modo === 'obra'

  const costoTotal = useMemo(
    () => items.reduce((a, it) => a + Number(it.cantidad) * Number(it.costo), 0),
    [items],
  )

  // Los items de catálogo (con producto) y los de obra (sin producto) no se mezclan: al cambiar de modo
  // se limpia la lista para que el payload sea coherente.
  function cambiarModo(nuevo) {
    if (nuevo === modo) return
    setModo(nuevo)
    setItems([])
  }
  function agregarItem(item) { setItems(prev => [...prev, item]) }
  function quitarItem(i) { setItems(prev => prev.filter((_, j) => j !== i)) }

  async function registrar() {
    if (!proveedor.nombre.trim()) { toast.error('Indica el proveedor'); return }
    if (items.length === 0) { toast.error('Agrega al menos un item'); return }
    if (esObra) {
      // Sin obra y sin viaje el backend exigiría producto_id (compra de catálogo): en modo obra no hay,
      // así que se pide al menos una imputación real (obra o viaje).
      if (!obraId && !esViaje) { toast.error('Elige una obra o marca el viaje de material'); return }
      if (esViaje && !(Number(precioVenta) > 0)) { toast.error('Indica el precio de venta al cliente'); return }
    }

    const payload = esObra
      ? {
        proveedor: { nombre: proveedor.nombre.trim(), nit: proveedor.nit.trim() || null },
        items: items.map(it => ({ cantidad: Number(it.cantidad), costo: Number(it.costo) })),
        obra_id: obraId ? Number(obraId) : null,
        categoria: categoria || null,
        es_viaje_material: esViaje,
        precio_venta_cliente: esViaje ? Number(precioVenta) : null,
      }
      : {
        proveedor: { nombre: proveedor.nombre.trim(), nit: proveedor.nit.trim() || null },
        items: items.map(it => ({ producto_id: it.producto_id, cantidad: Number(it.cantidad), costo: Number(it.costo) })),
      }

    setEnviando(true)
    try {
      const res = await api('/compras', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': nuevaIdemKey() },
        body: JSON.stringify(payload),
      })
      if (res.ok) {
        toast.success('Compra registrada')
        setProveedor({ nombre: '', nit: '' })
        setItems([])
        setObraId(''); setCategoria(''); setEsViaje(false); setPrecioVenta('')
        onRegistrada()
      } else {
        toast.error('No se pudo registrar la compra')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold inline-flex items-center gap-1.5">
          <Truck className="size-4" /> Nueva compra
        </h2>
        {construccion && (
          <div role="group" aria-label="Tipo de compra" className="inline-flex rounded-md border border-border p-0.5">
            {MODOS.map(m => (
              <button key={m.valor} type="button" onClick={() => cambiarModo(m.valor)}
                aria-pressed={modo === m.valor}
                className={`h-8 rounded px-3 text-[12px] font-medium transition-colors duration-fast ${
                  modo === m.valor ? 'bg-primary-soft text-primary' : 'text-muted-foreground hover:text-foreground'
                }`}>
                {m.label}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-2">
        <Input value={proveedor.nombre} onChange={setProv('nombre')} placeholder="Proveedor *" aria-label="Proveedor" className="h-9" />
        <Input value={proveedor.nit} onChange={setProv('nit')} placeholder="NIT (opcional)" aria-label="NIT del proveedor" className="h-9" />
      </div>

      {esObra ? (
        <ComprarObra
          obras={obras}
          obraId={obraId} setObraId={setObraId}
          categoria={categoria} setCategoria={setCategoria}
          esViaje={esViaje} setEsViaje={setEsViaje}
          precioVenta={precioVenta} setPrecioVenta={setPrecioVenta}
          costoTotal={costoTotal}
          onAgregarItem={agregarItem}
        />
      ) : (
        <ItemEditor conProducto onAgregar={agregarItem} />
      )}

      {items.length > 0 && (
        <ul className="mt-3 divide-y divide-border-subtle border-t border-border-subtle">
          {items.map((it, i) => (
            <li key={i} className="py-2 flex items-center gap-2 text-[12px]">
              <span className="min-w-0 flex-1 truncate">{it.nombre || `Material · línea ${i + 1}`}</span>
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
        <span className="text-[12px] text-muted-foreground">{esViaje ? 'Costo del viaje' : 'Total'}</span>
        <span className="tabular font-semibold">{cop(costoTotal)}</span>
      </div>

      <button onClick={registrar} disabled={enviando}
        className="w-full mt-3 h-10 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover disabled:opacity-60">
        {enviando ? 'Registrando…' : 'Registrar compra'}
      </button>
    </Card>
  )
}

/** Bloque del modo Obra / viaje: imputación (obra + categoría), el switch de viaje de material con su
 *  precio de venta y el preview del resbalo en vivo, y el editor de items sin producto (no mueve stock). */
function ComprarObra({
  obras, obraId, setObraId, categoria, setCategoria,
  esViaje, setEsViaje, precioVenta, setPrecioVenta, costoTotal, onAgregarItem,
}) {
  return (
    <div className="mt-3 pt-3 border-t border-border-subtle space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <Campo label="Obra">
          <select value={obraId} onChange={(e) => setObraId(e.target.value)} className={SELECT_CLS}>
            <option value="">Sin obra</option>
            {obras.map(o => <option key={o.id} value={o.id}>{o.nombre}</option>)}
          </select>
        </Campo>
        <Campo label="Categoría">
          <select value={categoria} onChange={(e) => setCategoria(e.target.value)} className={SELECT_CLS}>
            <option value="">Sin categoría</option>
            {CATEGORIAS.map(([valor, label]) => <option key={valor} value={valor}>{label}</option>)}
          </select>
        </Campo>
      </div>

      <label className="flex items-center gap-2 text-[12px] text-secondary-foreground cursor-pointer">
        <input type="checkbox" checked={esViaje} onChange={(e) => setEsViaje(e.target.checked)}
          className="size-3.5 rounded border-border text-primary focus-visible:ring-2 focus-visible:ring-ring" />
        Es viaje de material (se revende al cliente)
      </label>

      {esViaje && (
        <div className="space-y-2 rounded-md bg-surface-2/50 p-2.5">
          <Campo label="Precio de venta al cliente" requerido hint="El resbalo es lo que ganas: venta − costo del viaje.">
            <Input type="number" inputMode="numeric" value={precioVenta} onChange={(e) => setPrecioVenta(e.target.value)}
              placeholder="0" className="h-9" />
          </Campo>
          <PreviewResbalo venta={Number(precioVenta) || 0} costo={costoTotal} />
        </div>
      )}

      <ItemEditor conProducto={false} onAgregar={onAgregarItem} />
    </div>
  )
}

/** Preview del resbalo (margen) en vivo: venta − costo, con % y tono. Verde ≥5%, ámbar si <5%, rojo si es
 *  negativo. El signo NO va solo por color: la etiqueta lo nombra ("Margen sano/ajustado/en pérdida"). */
function PreviewResbalo({ venta, costo }) {
  const resbalo = venta - costo
  const pct = venta > 0 ? (resbalo / venta) * 100 : 0
  if (!(venta > 0)) {
    return <p className="text-[11px] text-muted-foreground">Ingresa el precio de venta para ver el resbalo.</p>
  }
  const tono = resbalo < 0
    ? { cls: 'text-destructive', etiqueta: 'Margen en pérdida' }
    : pct < RESBALO_MIN_PCT
      ? { cls: 'text-warning', etiqueta: 'Margen ajustado' }
      : { cls: 'text-success', etiqueta: 'Margen sano' }
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Resbalo</span>
      <span className={`inline-flex items-baseline gap-1.5 text-sm font-semibold tabular-nums ${tono.cls}`}>
        {cop(resbalo)}
        <span className="text-[11px] font-medium">· {Math.round(pct)}% · {tono.etiqueta}</span>
      </span>
    </div>
  )
}

function ItemEditor({ conProducto = true, onAgregar }) {
  const [producto, setProducto] = useState(null)
  const [cantidad, setCantidad] = useState('')
  const [costo, setCosto] = useState('')

  function agregar() {
    if (conProducto && !producto) { toast.error('Busca y elige un producto'); return }
    if (!(Number(cantidad) > 0) || !(Number(costo) >= 0)) { toast.error('Cantidad y costo válidos'); return }
    onAgregar({
      producto_id: conProducto ? producto.id : null,
      nombre: conProducto ? producto.nombre : null,
      cantidad,
      costo,
    })
    setProducto(null); setCantidad(''); setCosto('')
  }

  return (
    <div className="mt-3 pt-3 border-t border-border-subtle space-y-2">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {conProducto ? 'Item de compra' : 'Línea del viaje · no mueve inventario'}
      </p>
      {conProducto && (
        producto ? (
          <div className="flex items-center gap-2 text-[12px] bg-surface-2/50 rounded-md px-2.5 py-1.5">
            <span className="flex-1 truncate font-medium">{producto.nombre}</span>
            <button onClick={() => setProducto(null)} className="text-[11px] text-muted-foreground hover:text-foreground">cambiar</button>
          </div>
        ) : (
          <BuscadorProducto onElegir={setProducto} />
        )
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
