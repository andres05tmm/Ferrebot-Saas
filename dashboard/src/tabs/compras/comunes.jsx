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

/**
 * Líneas de la compra: producto, cantidad y COSTO UNITARIO (los tres obligatorios — decisión del
 * dueño). `campoCosto` distingue el costo acordado al pedir (`costo_estimado`) del real al recibir.
 */
export function LineasEditor({ lineas, setLineas, campoCosto = 'costo', etiquetaCosto = 'costo unitario', extraFila }) {
  if (lineas.length === 0) {
    return <p className="text-body-sm text-muted-foreground">Agrega los productos de la compra.</p>
  }
  const set = (i, cambios) => setLineas(prev => prev.map((x, j) => (j === i ? { ...x, ...cambios } : x)))
  return (
    <div className="space-y-2">
      {lineas.map((l, i) => (
        <div key={`${l.producto_id}-${i}`} className="border border-border rounded-md p-2 space-y-2">
          <div className="flex items-center gap-2 text-body-sm">
            <span className="flex-1 truncate font-medium">{l.nombre}</span>
            <Input type="number" inputMode="decimal" min="0" value={l.cantidad}
              aria-label={`Cantidad ${l.nombre}`} className="w-20 h-8"
              onChange={(e) => set(i, { cantidad: e.target.value })} />
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
          {extraFila?.(l, i, set)}
        </div>
      ))}
    </div>
  )
}

/** Selector de dónde sale la plata: solo la caja mueve el arqueo del día. */
export function OrigenFondos({ valor, onCambio, id = 'origen-fondos' }) {
  const opciones = [
    { id: 'caja', label: 'Efectivo de la caja' },
    { id: 'efectivo_externo', label: 'Efectivo guardado' },
    { id: 'banco', label: 'Transferencia / banco' },
  ]
  return (
    <div className="flex gap-2 flex-wrap" role="group" aria-label="De dónde sale la plata">
      {opciones.map(o => (
        <button key={o.id} type="button" id={`${id}-${o.id}`} aria-pressed={valor === o.id}
          onClick={() => onCambio(o.id)}
          className={`px-2.5 py-1 rounded-md border text-body-sm ${
            valor === o.id ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>
          {o.label}
        </button>
      ))}
    </div>
  )
}
