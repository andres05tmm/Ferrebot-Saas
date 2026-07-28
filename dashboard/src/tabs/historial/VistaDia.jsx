/*
 * VistaDia — el libro de ventas: una fila por RENGLÓN vendido, no por venta.
 *
 * Antes esta vista listaba cabeceras y escondía los productos detrás de un desplegable que pedía
 * `GET /ventas/{id}` por cada venta. El dueño necesita lo contrario: ver qué se vendió, a quién y a
 * cómo, de un vistazo. Ahora `GET /ventas/historial` trae todo resuelto en una sola llamada.
 *
 * Columnas en el orden que pidió el dueño: hora · producto · cliente · cantidad · v. unit. ·
 * método · vendedor · total.
 *
 * TENSIÓN QUE RESUELVE EL AGRUPADO: la tabla es plana, pero editar y anular actúan sobre la VENTA
 * completa. Si las filas de una misma venta no se leyeran como un bloque, el botón de borrar
 * parecería borrar el renglón. Por eso las celdas que se repiten (hora, cliente, método, vendedor)
 * se pintan solo en la primera fila del grupo, la venta arranca con una línea divisoria marcada, y
 * las acciones viven en esa primera fila.
 *
 * Editar/anular siguen siendo solo para ventas de HOY (regla del backend: 403 fuera de eso, 409 si
 * tiene factura electrónica viva). Las filas de días anteriores no muestran los botones, en vez de
 * mostrarlos y fallar al tocarlos.
 */
import { useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'
import { Download, Pencil, Trash2 } from '@/lib/icons.jsx'
import { api } from '@/lib/api'
import { hoyStrCO } from '@/lib/fechas'
import { cop } from '@/components/shared.jsx'
import { useHistorialLineas, useHistorialResumen, keyPrefix } from '@/lib/queries'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Badge } from '@/components/ui/badge.jsx'
import EditarVenta from './EditarVenta.jsx'

const HORA_CO = { hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota' }
const EVENTOS = [
  'venta_registrada', 'venta_anulada', 'venta_editada',
  'factura_pendiente', 'factura_aceptada', 'factura_rechazada', 'factura_error', 'factura_anulada',
  'reconnected',
]
const METODOS = ['', 'efectivo', 'transferencia', 'datafono', 'fiado']
const fechaCO = (iso) => new Date(iso).toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
const horaCO = (iso) => new Date(iso).toLocaleTimeString('es-CO', HORA_CO)
const num = (v) => Number(v).toLocaleString('es-CO', { maximumFractionDigits: 3 })

// Atajos de rango. El dueño entra a mirar "hoy" casi siempre y "este mes" cuando cuadra: teclear
// dos fechas cada vez es fricción pura. Los inputs quedan igual para cualquier otro rango.
function atajos() {
  const hoy = hoyStrCO()
  const menos = (d) => {
    const f = new Date(`${hoy}T12:00:00`)
    f.setDate(f.getDate() - d)
    return f.toLocaleDateString('en-CA')
  }
  return [
    ['Hoy', hoy, hoy],
    ['Ayer', menos(1), menos(1)],
    ['7 días', menos(6), hoy],
    ['Este mes', `${hoy.slice(0, 7)}-01`, hoy],
  ]
}

// 'mixto' no es un método de pago, es un marcador: sin las partes, la columna mentiría sobre cómo
// pagaron. Misma regla que aplica el Excel.
function metodoTexto(fila) {
  if (fila.metodo_pago !== 'mixto' || !fila.pagos?.length) return fila.metodo_pago
  return fila.pagos.map(p => p.metodo).join(' + ')
}

export default function VistaDia() {
  useOutletContext()
  const { isAdmin, getUser } = useAuth()
  const admin = isAdmin()
  const miId = getUser()?.id
  const qc = useQueryClient()
  const [desde, setDesde] = useState(hoyStrCO)
  const [hasta, setHasta] = useState(hoyStrCO)
  const [metodo, setMetodo] = useState('')
  const [texto, setTexto] = useState('')
  const [editando, setEditando] = useState(null)
  const [descargando, setDescargando] = useState(false)

  const feedQ = useHistorialLineas(desde, hasta)
  const resumenQ = useHistorialResumen(desde, hasta)
  const refrescar = () => qc.invalidateQueries({ queryKey: keyPrefix.historial })
  useRealtimeEvent(EVENTOS, refrescar)

  const filas = Array.isArray(feedQ.data?.filas) ? feedQ.data.filas : []
  const resumen = resumenQ.data ?? {}
  const porMetodo = resumen.por_metodo_pago ?? {}

  // Filtros en cliente: el feed ya viene acotado a 100 ventas, así que filtrar acá no esconde nada
  // que el servidor sí tenga. Si el rango crece, el filtro sube al backend.
  const visibles = useMemo(() => {
    const q = texto.trim().toLowerCase()
    return filas.filter(f => {
      if (metodo && f.metodo_pago !== metodo && !metodoTexto(f).includes(metodo)) return false
      if (!q) return true
      return [f.producto, f.cliente, f.vendedor].some(v => (v || '').toLowerCase().includes(q))
    })
  }, [filas, metodo, texto])

  // Una venta se puede tocar solo si es de HOY y es mía (o soy admin). Igual que antes.
  const puedoModificar = (f) =>
    fechaCO(f.fecha) === hoyStrCO() && (admin || Number(f.vendedor_id) === Number(miId))

  async function borrar(f) {
    if (!window.confirm(`¿Borrar la venta N.º ${f.consecutivo} completa? Se revertirá el stock.`)) return
    try {
      const res = await api(`/ventas/${f.venta_id}`, { method: 'DELETE' })
      if (res.ok) { toast.success('Venta borrada'); refrescar() }
      else if (res.status === 409) toast.error('Tiene factura electrónica, no se puede borrar')
      else if (res.status === 403) toast.error('No puedes borrar esta venta')
      else toast.error('No se pudo borrar la venta')
    } catch { toast.error('Error de conexión') }
  }

  async function descargarExcel() {
    setDescargando(true)
    try {
      const res = await api(`/ventas/historial/exportar?desde=${desde}&hasta=${hasta}`)
      if (!res.ok) {
        // 422 = el rango no cabe en un archivo. Se muestra el motivo del backend tal cual, en vez
        // de un "error al exportar" que no le dice a nadie qué hacer al respecto.
        const detalle = await res.json().catch(() => null)
        toast.error(detalle?.detail || 'No se pudo generar el informe')
        return
      }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `ventas_${desde}_${hasta}.xlsx`
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } catch { toast.error('Error de conexión') } finally { setDescargando(false) }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div>
          <p className="text-caption text-muted-foreground">Vendido en el período</p>
          <p className="text-3xl font-semibold tabular text-success leading-tight">
            {cop(Number(resumen.total_vendido ?? 0))}
          </p>
          <p className="text-caption text-muted-foreground">
            {resumen.num_ventas ?? 0} venta{resumen.num_ventas === 1 ? '' : 's'}
            {Object.entries(porMetodo).map(([m, v]) => ` · ${m} ${cop(Number(v))}`).join('')}
          </p>
        </div>
        <button type="button" onClick={descargarExcel} disabled={descargando}
          className="inline-flex items-center gap-1.5 text-meta h-9 px-3 rounded-md border border-border bg-surface hover:bg-surface-2 disabled:opacity-60">
          <Download className="size-4" /> {descargando ? 'Generando…' : 'Exportar Excel'}
        </button>
      </div>

      <Card className="p-3 flex flex-wrap items-end gap-3">
        <div className="flex flex-wrap gap-1.5">
          {atajos().map(([label, d, h]) => (
            <button key={label} type="button" onClick={() => { setDesde(d); setHasta(h) }}
              aria-pressed={desde === d && hasta === h}
              className={`text-meta px-2.5 h-9 rounded-md border transition-colors ${
                desde === d && hasta === h
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'bg-surface border-border hover:bg-surface-2'}`}>
              {label}
            </button>
          ))}
        </div>
        <label className="flex flex-col gap-1 text-caption text-muted-foreground">
          Desde
          <Input type="date" value={desde} onChange={(e) => setDesde(e.target.value)} aria-label="Desde" className="h-9 w-40" />
        </label>
        <label className="flex flex-col gap-1 text-caption text-muted-foreground">
          Hasta
          <Input type="date" value={hasta} onChange={(e) => setHasta(e.target.value)} aria-label="Hasta" className="h-9 w-40" />
        </label>
        <Input value={texto} onChange={(e) => setTexto(e.target.value)}
          placeholder="Buscar producto, cliente o vendedor…" aria-label="Buscar"
          className="h-9 w-full sm:w-64 ml-auto" />
      </Card>

      <div className="flex flex-wrap gap-1.5">
        {METODOS.map(m => (
          <button key={m || 'todos'} type="button" onClick={() => setMetodo(m)}
            className={`text-meta px-2.5 h-8 rounded-md border capitalize transition-colors ${
              metodo === m ? 'bg-primary text-primary-foreground border-primary'
                : 'bg-surface border-border hover:bg-surface-2'}`}>
            {m || 'Todos'}
          </button>
        ))}
      </div>

      <Card className="p-0 overflow-hidden">
        {feedQ.isLoading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : feedQ.isError ? (
          <p className="py-10 text-center text-sm text-destructive">No se pudieron cargar las ventas.</p>
        ) : visibles.length === 0 ? (
          <p className="py-10 px-4 text-center text-sm text-muted-foreground">
            {filas.length ? 'Ningún renglón coincide con el filtro.' : 'Sin ventas en el rango.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-body-sm">
              <thead className="bg-surface-2/60">
                <tr className="text-micro uppercase tracking-wider font-semibold text-muted-foreground">
                  <th className="text-left px-3 py-2">Hora</th>
                  <th className="text-left px-3 py-2">Producto</th>
                  <th className="text-left px-3 py-2">Cliente</th>
                  <th className="text-right px-3 py-2">Cant.</th>
                  <th className="text-right px-3 py-2">V. unit.</th>
                  <th className="text-left px-3 py-2">Método</th>
                  <th className="text-left px-3 py-2">Vendedor</th>
                  <th className="text-right px-3 py-2">Total</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {visibles.map((f, i) => {
                  // Primera fila de su venta: ahí van las celdas que no se repiten y las acciones.
                  const abre = i === 0 || visibles[i - 1].venta_id !== f.venta_id
                  const anulada = f.estado === 'anulada'
                  return (
                    <tr key={f.linea_id}
                      className={`border-t ${abre ? 'border-border' : 'border-transparent'} ${
                        anulada ? 'opacity-50' : ''} hover:bg-surface-2/60 transition-colors`}>
                      <td className="px-3 py-2 tabular text-muted-foreground whitespace-nowrap">
                        {abre ? horaCO(f.fecha) : ''}
                      </td>
                      <td className="px-3 py-2">
                        {f.producto}
                        {anulada && (
                          <Badge variant="outline" className="ml-1.5 text-micro h-4 px-1 bg-destructive/10 text-destructive border-destructive/20">
                            anulada
                          </Badge>
                        )}
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">{abre ? f.cliente : ''}</td>
                      <td className="px-3 py-2 text-right tabular">{num(f.cantidad)}</td>
                      <td className="px-3 py-2 text-right tabular">{cop(Number(f.precio_unitario))}</td>
                      <td className="px-3 py-2 capitalize text-muted-foreground whitespace-nowrap">
                        {abre ? metodoTexto(f) : ''}
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">{abre ? f.vendedor : ''}</td>
                      <td className="px-3 py-2 text-right tabular font-medium">
                        {cop(Number(f.total_linea))}
                      </td>
                      <td className="px-2 py-2 whitespace-nowrap">
                        {abre && puedoModificar(f) && !anulada && (
                          <span className="flex items-center">
                            <button onClick={() => setEditando(e => (e === f.venta_id ? null : f.venta_id))}
                              aria-label={`Editar venta N.º ${f.consecutivo}`} title="Editar la venta completa"
                              className="size-8 grid place-items-center rounded-md text-muted-foreground hover:text-primary">
                              <Pencil className="size-4" />
                            </button>
                            <button onClick={() => borrar(f)}
                              aria-label={`Borrar venta N.º ${f.consecutivo}`} title="Borrar la venta completa"
                              className="size-8 grid place-items-center rounded-md text-muted-foreground hover:text-destructive">
                              <Trash2 className="size-4" />
                            </button>
                          </span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
              <tfoot>
                <tr className="border-t border-border bg-surface-2/30">
                  <td colSpan={7} className="px-3 py-2 text-right text-caption text-muted-foreground">
                    {visibles.length} renglón{visibles.length === 1 ? '' : 'es'} en pantalla
                  </td>
                  <td className="px-3 py-2 text-right tabular font-semibold">
                    {cop(Number(resumen.total_vendido ?? 0))}
                  </td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </Card>

      {editando && (
        <Card className="p-0 overflow-hidden">
          <EditarVenta ventaId={editando} onClose={() => setEditando(null)}
            onSaved={() => { setEditando(null); refrescar() }} />
        </Card>
      )}

      {feedQ.data?.hay_mas && (
        <p className="text-caption text-muted-foreground px-1">
          Se muestran las ventas más recientes del rango. Acota las fechas o exporta el Excel para
          verlas todas.
        </p>
      )}
      <p className="text-caption text-muted-foreground px-1">
        El total de arriba cubre TODO el período y descuenta las anuladas; la tabla muestra las
        ventas más recientes. Corregir y borrar solo aplica a las ventas de hoy.
      </p>
    </div>
  )
}
