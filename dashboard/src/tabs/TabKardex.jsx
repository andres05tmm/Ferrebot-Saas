/*
 * TabKardex — movimientos de inventario por producto (reportes/retail). Gateada por 'inventario'.
 * Se busca un producto (GET /productos?q) y se muestra su kárdex (GET /inventario/kardex/{id}): cada
 * movimiento con su tipo, cantidad (con signo por naturaleza del tipo), costo y referencia. Lectura de
 * staff (vendedor+). Tiempo real: refetch del kárdex ante venta/ajuste/compra que muevan stock.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { BookOpen, Search, PackageSearch } from '@/lib/icons.jsx'
import { cop } from '@/components/shared.jsx'
import { useProductos, useKardex, keyPrefix, LIMITE_KARDEX } from '@/lib/queries'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])
// Los CUATRO tipos que existen (enum `mov_inventario_tipo`: ENTRADA, SALIDA, AJUSTE, DEVOLUCION).
// Esto estuvo escrito contra VENTA/COMPRA/MERMA/CONTEO, que la base rechaza: una venta llega como
// SALIDA y una compra como ENTRADA, así que los dos movimientos más frecuentes se pintaban en el
// gris del fallback. El AJUSTE guarda el delta CON signo (−3 por merma, +5 por sobrante), por eso
// el signo se decide por tipo O por cantidad negativa.
const SALIDAS = new Set(['SALIDA'])

function fecha(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('es-CO', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota',
  })
}

const TIPO_TONO = {
  SALIDA: 'text-destructive', ENTRADA: 'text-info',
  DEVOLUCION: 'text-success', AJUSTE: 'text-warning',
}

function Movimiento({ m }) {
  const cant = Number(m.cantidad || 0)
  const esSalida = SALIDAS.has(m.tipo) || cant < 0
  const signo = esSalida ? '−' : '+'
  return (
    <li className="px-3.5 py-2.5 flex items-center gap-3 text-body-sm">
      <Badge variant="outline" className={`h-5 text-micro shrink-0 ${TIPO_TONO[m.tipo] || 'text-muted-foreground'}`}>
        {m.tipo}
      </Badge>
      <div className="min-w-0 flex-1">
        <div className="text-meta text-muted-foreground truncate">
          {m.referencia || 'sin referencia'} · {fecha(m.creado_en)}
        </div>
      </div>
      {m.costo_unitario != null && (
        <span className="text-caption text-muted-foreground tabular-nums shrink-0">c/u {cop(m.costo_unitario)}</span>
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
  // `devolucion_registrada` va acá porque una devolución SÍ mueve stock (movimiento DEVOLUCION):
  // sin ella, el renglón no aparecía hasta recargar la página.
  useRealtimeEvent(
    ['venta_registrada', 'compra_registrada', 'stock_ajustado', 'devolucion_registrada'],
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
                      className="w-full text-left px-1 py-2 hover:bg-surface-2 rounded-md flex items-center gap-2 text-body-sm">
                      <PackageSearch className="size-4 text-muted-foreground shrink-0" />
                      <span className="flex-1 truncate">{p.nombre}</span>
                      {p.codigo && <span className="text-caption text-muted-foreground">{p.codigo}</span>}
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
              className="ml-auto text-caption text-primary hover:underline shrink-0">cambiar</button>
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
            <>
              <ul className="divide-y divide-border-subtle">
                {movimientos.map(m => <Movimiento key={m.id} m={m} />)}
              </ul>
              {movimientos.length >= LIMITE_KARDEX && (
                <p className="px-3.5 py-2.5 border-t border-border-subtle text-caption text-muted-foreground">
                  Mostrando los {LIMITE_KARDEX} movimientos más recientes; este producto puede tener
                  más atrás.
                </p>
              )}
            </>
          )}
        </Card>
      )}
    </div>
  )
}
