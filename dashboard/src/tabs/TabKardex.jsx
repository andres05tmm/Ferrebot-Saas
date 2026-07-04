/*
 * TabKardex — movimientos de inventario por producto (reportes/retail). Gateada por 'inventario'.
 * Se busca un producto (GET /productos?q) y se muestra su kárdex (GET /inventario/kardex/{id}): cada
 * movimiento con su tipo, cantidad (con signo por naturaleza del tipo), costo y referencia. Lectura de
 * staff (vendedor+). Tiempo real: refetch del kárdex ante venta/ajuste/compra que muevan stock.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { BookOpen, Search, PackageSearch } from 'lucide-react'
import { cop } from '@/components/shared.jsx'
import { useProductos, useKardex, keyPrefix } from '@/lib/queries'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])
// Tipos que RESTAN stock (salidas): el resto suma. Solo para el signo visual; el dato es la magnitud.
const SALIDAS = new Set(['VENTA', 'venta', 'MERMA', 'SALIDA', 'salida'])

function fecha(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('es-CO', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota',
  })
}

const TIPO_TONO = {
  VENTA: 'text-destructive', DEVOLUCION: 'text-success', COMPRA: 'text-info',
  AJUSTE: 'text-warning', CONTEO: 'text-warning',
}

function Movimiento({ m }) {
  const cant = Number(m.cantidad || 0)
  const esSalida = SALIDAS.has(m.tipo) || cant < 0
  const signo = esSalida ? '−' : '+'
  return (
    <li className="px-3.5 py-2.5 flex items-center gap-3 text-[13px]">
      <Badge variant="outline" className={`h-5 text-[10px] shrink-0 ${TIPO_TONO[m.tipo] || 'text-muted-foreground'}`}>
        {m.tipo}
      </Badge>
      <div className="min-w-0 flex-1">
        <div className="text-[12px] text-muted-foreground truncate">
          {m.referencia || 'sin referencia'} · {fecha(m.creado_en)}
        </div>
      </div>
      {m.costo_unitario != null && (
        <span className="text-[11px] text-muted-foreground tabular-nums shrink-0">c/u {cop(m.costo_unitario)}</span>
      )}
      <span className={`tabular-nums font-semibold shrink-0 ${esSalida ? 'text-destructive' : 'text-success'}`}>
        {signo}{Math.abs(cant).toLocaleString('es-CO')}
      </span>
    </li>
  )
}

export default function TabKardex() {
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(null)   // producto elegido { id, nombre }

  const qc = useQueryClient()
  const busca = q.trim()
  // Con un producto elegido se corta la búsqueda (q vacío → useProductos deshabilitada).
  const productosQ = useProductos(sel ? '' : busca)
  const kardexQ = useKardex(sel?.id ?? null)
  useRealtimeEvent(['venta_registrada', 'compra_registrada', 'stock_ajustado'],
    () => qc.invalidateQueries({ queryKey: keyPrefix.kardex }))

  const productos = arr(productosQ.data)
  const movimientos = arr(kardexQ.data)

  return (
    <div className="space-y-3">
      <h1 className="text-base font-semibold inline-flex items-center gap-2">
        <BookOpen className="size-4.5 text-primary" /> Kárdex
      </h1>

      <Card className="p-3">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <Input value={q} onChange={e => { setQ(e.target.value); setSel(null) }}
            placeholder="Busca un producto por nombre o código" aria-label="Buscar producto"
            className="h-10 pl-8" />
        </div>
        {busca && !sel && (
          <div className="mt-2">
            {productosQ.isLoading ? (
              <p className="py-4 text-center text-sm text-muted-foreground">Buscando…</p>
            ) : productos.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted-foreground">Sin coincidencias.</p>
            ) : (
              <ul className="divide-y divide-border-subtle">
                {productos.map(p => (
                  <li key={p.id}>
                    <button onClick={() => setSel({ id: p.id, nombre: p.nombre })}
                      className="w-full text-left px-1 py-2 hover:bg-surface-2 rounded-md flex items-center gap-2 text-[13px]">
                      <PackageSearch className="size-4 text-muted-foreground shrink-0" />
                      <span className="flex-1 truncate">{p.nombre}</span>
                      {p.codigo && <span className="text-[11px] text-muted-foreground">{p.codigo}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>

      {!sel ? (
        <Card className="p-10 text-center text-sm text-muted-foreground">
          Busca un producto para ver su historial de movimientos de inventario.
        </Card>
      ) : (
        <Card className="p-0 overflow-hidden">
          <div className="px-3.5 py-2.5 border-b border-border-subtle flex items-center gap-2">
            <h2 className="text-sm font-semibold truncate">{sel.nombre}</h2>
            <button onClick={() => { setSel(null); setQ('') }}
              className="ml-auto text-[11px] text-primary hover:underline shrink-0">cambiar</button>
          </div>
          {kardexQ.isLoading ? (
            <p className="py-10 text-center text-sm text-muted-foreground">Cargando kárdex…</p>
          ) : kardexQ.isError ? (
            <p className="py-10 text-center text-sm text-destructive">No se pudo cargar el kárdex.</p>
          ) : movimientos.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              Este producto aún no tiene movimientos de inventario.
            </p>
          ) : (
            <ul className="divide-y divide-border-subtle">
              {movimientos.map(m => <Movimiento key={m.id} m={m} />)}
            </ul>
          )}
        </Card>
      )}
    </div>
  )
}
