/*
 * Piezas compartidas por los modales del tab Compras: buscador de producto, editor de líneas
 * (producto + cantidad + costo unitario) y los formatos del cronómetro.
 */
import { useState } from 'react'
import { Search, Trash2 } from 'lucide-react'
import { apiJson } from '@/lib/api'
import { cop } from '@/components/shared.jsx'
import { Input } from '@/components/ui/input.jsx'

export const arr = (d) => (Array.isArray(d) ? d : [])

/** "3 h" / "2.5 días": el cronómetro en el idioma del mostrador. */
export function horasATexto(h) {
  if (h == null) return '—'
  if (h < 24) return `${Math.round(h)} h`
  const dias = h / 24
  return `${dias < 10 ? dias.toFixed(1) : Math.round(dias)} días`
}

export function fechaCorta(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('es-CO', {
    day: '2-digit', month: 'short', timeZone: 'America/Bogota',
  })
}

export function nuevaIdemKey() {
  try { return crypto.randomUUID() } catch { return `k-${Date.now()}-${Math.random().toString(16).slice(2)}` }
}

/** Total de las líneas capturadas (cantidad × costo unitario). */
export const totalLineas = (lineas, campoCosto = 'costo') =>
  lineas.reduce((acc, l) => acc + Number(l.cantidad || 0) * Number(l[campoCosto] || 0), 0)

export function BuscadorProducto({ onPick, placeholder = 'Buscar producto para agregar…' }) {
  const [q, setQ] = useState('')
  const [resultados, setResultados] = useState([])

  async function buscar(term) {
    setQ(term)
    if (!term.trim()) { setResultados([]); return }
    try {
      setResultados(arr(await apiJson(`/productos?q=${encodeURIComponent(term)}&limite=8`)))
    } catch { setResultados([]) }
  }

  return (
    <div className="relative">
      <Search className="size-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
      <Input value={q} onChange={(e) => buscar(e.target.value)} placeholder={placeholder}
        aria-label="Buscar producto" className="pl-8" />
      {resultados.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full bg-surface border border-border rounded-md shadow-md max-h-48 overflow-y-auto">
          {resultados.map(p => (
            <li key={p.id}>
              <button type="button"
                onClick={() => { onPick(p); setQ(''); setResultados([]) }}
                className="w-full text-left px-3 py-1.5 text-body-sm hover:bg-surface-2 flex justify-between gap-2">
                <span className="truncate">{p.nombre}</span>
                <span className="text-muted-foreground shrink-0">{cop(Number(p.precio_compra || 0))}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// Vocabulario del granel (mismo que el POS): la sub-unidad en que se VENDE y el envase con que se
// COMPRA. `unidades_por_paquete` (del backend) dice cuántas sub-unidades trae el envase.
const ENVASE = {
  grm: { sub: 'g', paquete: 'caja' },
  gramos: { sub: 'g', paquete: 'caja' },
  cms: { sub: 'cm', paquete: 'rollo' },
  mlt: { sub: 'ml', paquete: 'tarro' },
  ml: { sub: 'ml', paquete: 'tarro' },
  mililitros: { sub: 'ml', paquete: 'tarro' },
}

/** Cómo se llama lo que se está capturando: granel (caja/gramo) o la unidad del catálogo. */
export function unidadesDe(producto) {
  const paquete = Number(producto?.unidades_por_paquete || 0)
  const envase = ENVASE[(producto?.unidad_medida || '').trim().toLowerCase()]
  if (!paquete || !envase) return null
  return { ...envase, factor: paquete }
}

/** Línea nueva a partir del producto elegido, con lo necesario para mostrar/convertir la unidad. */
export function lineaDe(producto, { campoCosto = 'costo' } = {}) {
  const granel = unidadesDe(producto)
  return {
    producto_id: producto.id,
    nombre: producto.nombre,
    unidad_medida: producto.unidad_medida,
    unidades_por_paquete: producto.unidades_por_paquete ?? null,
    cantidad: '1',
    // Los granel se capturan por defecto en el envase con que se le compra al proveedor (la caja),
    // que es como el dueño piensa la compra; el backend lo pasa a la sub-unidad del stock.
    unidad: granel ? 'paquete' : 'sub',
    [campoCosto]: producto.precio_compra != null ? String(producto.precio_compra) : '',
    cuadrar: false,
    cantidad_fisica: '',
  }
}

/**
 * Líneas de la compra: producto, cantidad y COSTO UNITARIO (los tres obligatorios — decisión del
 * dueño). `campoCosto` distingue el costo acordado al pedir (`costo_estimado`) del real al recibir.
 *
 * Cada línea muestra SU unidad. En los productos que se venden menudeados (puntilla por gramo, lija
 * por cm, tintilla por ml) se puede capturar en la caja/rollo/tarro con que se le compra al
 * proveedor: el backend convierte a la sub-unidad en la que vive el stock.
 */
export function LineasEditor({ lineas, setLineas, campoCosto = 'costo', etiquetaCosto = 'costo unitario', extraFila }) {
  if (lineas.length === 0) {
    return <p className="text-body-sm text-muted-foreground">Agrega los productos de la compra.</p>
  }
  const set = (i, cambios) => setLineas(prev => prev.map((x, j) => (j === i ? { ...x, ...cambios } : x)))
  return (
    <div className="space-y-2">
      {lineas.map((l, i) => {
        const granel = unidadesDe(l)
        const enPaquetes = granel && l.unidad === 'paquete'
        const nombreUnidad = granel
          ? (enPaquetes ? granel.paquete : granel.sub)
          : (l.unidad_medida || 'unidad')
        return (
          <div key={`${l.producto_id}-${i}`} className="border border-border rounded-md p-2 space-y-2">
            <div className="flex items-center gap-2 text-body-sm">
              <span className="flex-1 truncate font-medium">{l.nombre}</span>
              <Input type="number" inputMode="decimal" min="0" value={l.cantidad}
                aria-label={`Cantidad ${l.nombre}`} className="w-20 h-8"
                onChange={(e) => set(i, { cantidad: e.target.value })} />
              <span className="w-16 text-caption text-muted-foreground truncate" title={nombreUnidad}>
                {nombreUnidad}
              </span>
              <Input type="number" inputMode="numeric" min="0" value={l[campoCosto]}
                aria-label={`Costo unitario ${l.nombre}`} className="w-28 h-8" placeholder={etiquetaCosto}
                onChange={(e) => set(i, { [campoCosto]: e.target.value })} />
              <span className="w-24 text-right tabular-nums text-muted-foreground">
                {cop(Number(l.cantidad || 0) * Number(l[campoCosto] || 0))}
              </span>
              <button type="button" onClick={() => setLineas(prev => prev.filter((_, j) => j !== i))}
                aria-label={`Quitar ${l.nombre}`} className="text-muted-foreground hover:text-danger">
                <Trash2 className="size-4" />
              </button>
            </div>
            {granel && (
              <label className="flex items-center gap-2 text-caption text-muted-foreground">
                <input type="checkbox" checked={enPaquetes}
                  aria-label={`Comprar por ${granel.paquete} ${l.nombre}`}
                  onChange={(e) => set(i, { unidad: e.target.checked ? 'paquete' : 'sub' })} />
                Se compra por {granel.paquete} ({granel.factor} {granel.sub} cada una) — se vende por {granel.sub}
                {enPaquetes && Number(l.cantidad) > 0 && (
                  <strong className="text-foreground">
                    · entran {Number(l.cantidad) * granel.factor} {granel.sub}
                  </strong>
                )}
              </label>
            )}
            {extraFila?.(l, i, set)}
          </div>
        )
      })}
    </div>
  )
}

export const MEDIOS = [
  { id: 'caja', label: 'Efectivo de la caja' },
  { id: 'efectivo_externo', label: 'Efectivo guardado' },
  { id: 'banco', label: 'Transferencia / banco' },
]

/**
 * De dónde sale la plata. Caso normal: un solo medio (botones). Caso mixto: se reparte el monto
 * entre medios y las partes tienen que sumar exactamente lo que se paga — solo la parte de la caja
 * mueve el arqueo del día.
 *
 * `onCambio(origen)` para el medio único y `onPagos(partes)` para la repartición ([] = sin mixto).
 */
export function OrigenFondos({ valor, onCambio, pagos = [], onPagos, monto = 0, id = 'origen-fondos' }) {
  const mixto = pagos.length > 0
  const suma = pagos.reduce((acc, p) => acc + Number(p.monto || 0), 0)
  const cuadra = Math.abs(suma - Number(monto || 0)) < 0.005

  function alternarMixto() {
    if (!onPagos) return
    // Al abrir, la primera parte arranca con todo el monto en el medio ya elegido: solo hay que
    // mover lo que se pagó por el otro lado.
    onPagos(mixto ? [] : [{ origen: valor, monto: String(monto || '') }])
  }

  return (
    <div className="space-y-2">
      {!mixto && (
        <div className="flex gap-2 flex-wrap" role="group" aria-label="De dónde sale la plata">
          {MEDIOS.map(o => (
            <button key={o.id} type="button" id={`${id}-${o.id}`} aria-pressed={valor === o.id}
              onClick={() => onCambio(o.id)}
              className={`px-2.5 py-1 rounded-md border text-body-sm ${
                valor === o.id ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>
              {o.label}
            </button>
          ))}
        </div>
      )}

      {mixto && (
        <div className="space-y-1.5">
          {MEDIOS.map(o => {
            const parte = pagos.find(p => p.origen === o.id)
            return (
              <div key={o.id} className="flex items-center gap-2 text-body-sm">
                <span className="flex-1">{o.label}</span>
                <Input type="number" inputMode="numeric" min="0" className="w-32 h-8"
                  aria-label={`Monto ${o.label}`} value={parte?.monto ?? ''}
                  onChange={(e) => {
                    const v = e.target.value
                    const resto = pagos.filter(p => p.origen !== o.id)
                    onPagos(v === '' || Number(v) <= 0 ? resto : [...resto, { origen: o.id, monto: v }])
                  }} />
              </div>
            )
          })}
          <p className={`text-caption ${cuadra ? 'text-muted-foreground' : 'text-warning'}`}>
            Repartido {cop(suma)} de {cop(Number(monto || 0))}
            {!cuadra && ' — las partes tienen que sumar el total'}
          </p>
        </div>
      )}

      {onPagos && (
        <button type="button" onClick={alternarMixto}
          className="text-caption text-primary hover:underline">
          {mixto ? 'Pagar con un solo medio' : 'Pago mixto (parte en efectivo, parte por transferencia)'}
        </button>
      )}
    </div>
  )
}

/** Las partes listas para el backend (números), o [] si no hay pago mixto. */
export const partesPago = (pagos) =>
  pagos.filter(p => Number(p.monto) > 0).map(p => ({ origen: p.origen, monto: Number(p.monto) }))

/** ¿La repartición cuadra con lo que se paga? (sin mixto siempre cuadra). */
export const pagoCuadra = (pagos, monto) =>
  pagos.length === 0
  || Math.abs(pagos.reduce((a, p) => a + Number(p.monto || 0), 0) - Number(monto || 0)) < 0.005
