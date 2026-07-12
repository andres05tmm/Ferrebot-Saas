/*
 * VistaMes — calendario mensual tipo heatmap (reforma F3): agregado por día del backend
 * (GET /reportes/calendario?anio&mes — ya no se agrega en el cliente) con navegación de meses.
 * Cada celda pinta la intensidad de la venta del día (relativa al mejor día del mes) y el detalle
 * (total, # ventas, gastos) va en el title y en la lista de abajo. Live: venta_registrada / reconnected.
 */
import { useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { anioMesCO as hoyCO } from '@/lib/fechas'
import { useFetch, cop, num } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'

const DIAS_SEMANA = ['L', 'M', 'X', 'J', 'V', 'S', 'D']
const MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio',
  'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']

// Intensidad 0..4 relativa al mejor día del mes (para el heatmap con el color primario del tenant).
function nivel(total, max) {
  if (!total || !max) return 0
  const r = total / max
  if (r > 0.75) return 4
  if (r > 0.5) return 3
  if (r > 0.25) return 2
  return 1
}
const NIVEL_CLS = [
  'bg-surface-2 text-muted-foreground',
  'bg-primary/15', 'bg-primary/30', 'bg-primary/55 text-primary-foreground',
  'bg-primary text-primary-foreground',
]

export default function VistaMes() {
  const [{ anio, mes }, setPeriodo] = useState(hoyCO())
  const q = useFetch(`/reportes/calendario?anio=${anio}&mes=${mes}`, [anio, mes])
  useRealtimeEvent(['venta_registrada', 'venta_anulada', 'venta_editada', 'reconnected'], q.refetch)

  const dias = Array.isArray(q.data) ? q.data : []
  const porFecha = useMemo(() => Object.fromEntries(dias.map(d => [d.fecha, d])), [dias])
  const max = useMemo(() => Math.max(0, ...dias.map(d => Number(d.total))), [dias])
  const total = useMemo(() => dias.reduce((a, d) => a + Number(d.total), 0), [dias])
  const totalGastos = useMemo(() => dias.reduce((a, d) => a + Number(d.gastos), 0), [dias])

  function mover(delta) {
    setPeriodo(({ anio, mes }) => {
      const m = mes + delta
      if (m < 1) return { anio: anio - 1, mes: 12 }
      if (m > 12) return { anio: anio + 1, mes: 1 }
      return { anio, mes: m }
    })
  }

  // Grilla del mes: celdas vacías hasta el día de la semana del 1° (lunes = 0).
  const celdas = useMemo(() => {
    const primero = new Date(Date.UTC(anio, mes - 1, 1))
    const offset = (primero.getUTCDay() + 6) % 7
    const nDias = new Date(Date.UTC(anio, mes, 0)).getUTCDate()
    const out = Array.from({ length: offset }, () => null)
    for (let d = 1; d <= nDias; d++) {
      const fecha = `${anio}-${String(mes).padStart(2, '0')}-${String(d).padStart(2, '0')}`
      out.push({ dia: d, fecha, datos: porFecha[fecha] })
    }
    return out
  }, [anio, mes, porFecha])

  const conVentas = dias.filter(d => Number(d.total) > 0)

  return (
    <div className="space-y-3">
      <Card className="p-3.5">
        <div className="flex items-center justify-between mb-3">
          <button onClick={() => mover(-1)} aria-label="Mes anterior"
            className="size-7 grid place-items-center rounded-md border border-border hover:bg-surface-2">
            <ChevronLeft className="size-4" />
          </button>
          <div className="text-center">
            <h2 className="text-sm font-semibold">{MESES[mes - 1]} {anio}</h2>
            <p className="text-caption text-muted-foreground">
              {cop(total)} vendidos · {cop(totalGastos)} en gastos
            </p>
          </div>
          <button onClick={() => mover(1)} aria-label="Mes siguiente"
            className="size-7 grid place-items-center rounded-md border border-border hover:bg-surface-2">
            <ChevronRight className="size-4" />
          </button>
        </div>

        <div className="grid grid-cols-7 gap-1 text-center">
          {DIAS_SEMANA.map(d => (
            <div key={d} className="text-caption text-muted-foreground py-1">{d}</div>
          ))}
          {celdas.map((c, i) => c === null ? <div key={`v-${i}`} /> : (
            <div key={c.fecha}
              title={c.datos
                ? `${c.fecha}: ${cop(Number(c.datos.total))} en ${num(c.datos.num_ventas)} venta(s)`
                  + (Number(c.datos.gastos) > 0 ? ` · gastos ${cop(Number(c.datos.gastos))}` : '')
                : `${c.fecha}: sin movimiento`}
              className={`rounded-md py-1.5 text-[11px] tabular ${NIVEL_CLS[nivel(Number(c.datos?.total || 0), max)]}`}>
              {c.dia}
            </div>
          ))}
        </div>
      </Card>

      <Card className="p-0 overflow-hidden">
        <div className="flex items-center justify-between px-3.5 py-2.5 border-b border-border-subtle">
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Detalle por día</h2>
          <span className="text-[12px] tabular font-semibold">{cop(total)}</span>
        </div>
        {q.loading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : conVentas.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Sin ventas este mes.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {[...conVentas].reverse().map(d => (
              <li key={d.fecha} className="flex items-center gap-2 px-3.5 py-2 text-[13px]">
                <span className="tabular text-muted-foreground w-28 shrink-0">{d.fecha}</span>
                <span className="flex-1 text-[12px] text-muted-foreground">
                  {num(d.num_ventas)} {d.num_ventas === 1 ? 'venta' : 'ventas'}
                  {Number(d.gastos) > 0 ? ` · gastos ${cop(Number(d.gastos))}` : ''}
                </span>
                <span className="tabular font-semibold shrink-0">{cop(Number(d.total))}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}
