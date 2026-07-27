/*
 * PanelAvance — construir el inventario producto por producto, con el trabajo del día (issue #180).
 *
 * La ferretería no llevaba stock de nada. Nadie va a cerrar el local para contar 600 productos, así
 * que el inventario se arma de a poco y el ORDEN es lo que decide si eso sirve: el backend devuelve
 * los pendientes por ROTACIÓN, no alfabéticamente — con los que más se venden ya se controla la
 * mayor parte del negocio.
 *
 * Dos puertas, y la primera es la importante:
 *
 *   · "Se acabó"  — el día que el estante queda vacío el stock ES cero. No hay que contar ni
 *                   estimar nada, y queda exacto para siempre. Es un conteo con cantidad 0.
 *   · "Conté N"   — cuando sí se cuenta. Set-to-absolute: el número que se escribe QUEDA.
 *
 * El stock negativo de un producto pendiente no es un error: son las ventas anotadas sin haber
 * contado nunca. Se muestra como lo que es (backlog), sin alarma roja.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { Check, Package } from '@/lib/icons.jsx'
import { api } from '@/lib/api'
import { useFetch, num } from '@/components/shared.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'

export default function PanelAvance({ refreshKey, onCuadrado }) {
  const { data, loading, error } = useFetch('/inventario/avance?limite=8', [refreshKey])
  const [contando, setContando] = useState(null)      // producto_id con el input abierto
  const [valor, setValor] = useState('')
  const [enviando, setEnviando] = useState(false)

  async function cuadrar(item, cantidad) {
    setEnviando(true)
    try {
      const res = await api('/inventario/conteo', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          // El mismo conteo del mismo producto no debe aplicarse dos veces si se toca dos veces.
          'Idempotency-Key': `conteo:${item.producto_id}:${cantidad}:${new Date().toDateString()}`,
        },
        body: JSON.stringify({
          producto_id: item.producto_id, cantidad_contada: cantidad,
          motivo: cantidad === 0 ? 'Se acabó' : 'Conteo físico',
        }),
      })
      if (!res.ok) { toast.error('No se pudo cuadrar el producto.'); return }
      toast.success(`${item.nombre} ya queda en control`)
      setContando(null); setValor('')
      onCuadrado?.()
    } catch {
      toast.error('Error de conexión.')
    } finally { setEnviando(false) }
  }

  if (loading || error || !data) return null
  const { activos = 0, cuadrados = 0, pendientes = [] } = data
  if (pendientes.length === 0) return null
  const pct = activos > 0 ? Math.round((cuadrados / activos) * 100) : 0

  return (
    <Card className="p-3 mb-3">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-body font-medium flex items-center gap-1.5">
          <Package className="size-4 text-muted-foreground" aria-hidden="true" />
          Armando el inventario
        </h2>
        <span className="text-caption text-muted-foreground tabular">
          {num(cuadrados)} de {num(activos)} en control
        </span>
      </div>

      <div className="mt-2 h-1.5 rounded-full bg-surface-2 overflow-hidden"
        role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}
        aria-label="Avance del inventario">
        <div className="h-full bg-primary rounded-full transition-all" style={{ width: `${pct}%` }} />
      </div>

      <p className="mt-2 text-caption text-muted-foreground">
        Empieza por los que más se venden. Cuando uno se acabe, tócalo: ahí su stock es cero exacto.
      </p>

      <ul className="mt-2 divide-y divide-border-subtle">
        {pendientes.map(it => (
          <li key={it.producto_id} className="py-2 flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <div className="text-body-sm truncate">{it.nombre}</div>
              <div className="text-caption text-muted-foreground tabular">
                {it.lineas_vendidas > 0
                  ? `${num(it.lineas_vendidas)} ventas`
                  : 'sin ventas registradas'}
                {Number(it.stock_actual) !== 0 && (
                  <> · lleva {num(it.stock_actual)} {it.unidad_medida} sin contar</>
                )}
              </div>
            </div>

            {contando === it.producto_id ? (
              <div className="flex items-center gap-1">
                <Input type="number" min="0" step="any" value={valor} autoFocus
                  onChange={(e) => setValor(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && valor !== '') cuadrar(it, Number(valor))
                    if (e.key === 'Escape') { setContando(null); setValor('') }
                  }}
                  aria-label={`Cantidad contada de ${it.nombre}`} className="w-20 h-8 text-center" />
                <button disabled={enviando || valor === ''}
                  onClick={() => cuadrar(it, Number(valor))}
                  className="size-8 grid place-items-center rounded-md text-primary disabled:opacity-40"
                  aria-label={`Guardar conteo de ${it.nombre}`}>
                  <Check className="size-4" />
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-1">
                <button disabled={enviando} onClick={() => cuadrar(it, 0)}
                  className="h-8 px-2 rounded-md text-caption border border-border-subtle hover:bg-surface-2 disabled:opacity-40">
                  Se acabó
                </button>
                <button disabled={enviando}
                  onClick={() => { setContando(it.producto_id); setValor('') }}
                  className="h-8 px-2 rounded-md text-caption border border-border-subtle hover:bg-surface-2 disabled:opacity-40">
                  Conté…
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}
