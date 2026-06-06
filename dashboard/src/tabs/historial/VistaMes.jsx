/*
 * VistaMes — agregación por día del mes en curso, calculada en cliente sobre GET /ventas del mes
 * (no hay endpoint de agregación mensual; no se inventa backend). Live: venta_registrada / reconnected.
 */
import { useMemo } from 'react'
import { useFetch, cop, num } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'

const hoyCO = () => new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
const diaDe = (fecha) => new Date(fecha).toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })

export default function VistaMes() {
  const hoy = hoyCO()
  const desde = `${hoy.slice(0, 8)}01`   // primer día del mes en curso
  const ventasQ = useFetch(`/ventas?desde=${desde}&hasta=${hoy}`, [])
  useRealtimeEvent(['venta_registrada', 'venta_anulada', 'venta_editada', 'reconnected'], ventasQ.refetch)

  const { dias, total } = useMemo(() => {
    const acc = {}
    let total = 0
    for (const v of (Array.isArray(ventasQ.data) ? ventasQ.data : [])) {
      const dia = diaDe(v.fecha)
      if (!acc[dia]) acc[dia] = { dia, count: 0, total: 0 }
      acc[dia].count += 1
      acc[dia].total += Number(v.total)
      total += Number(v.total)
    }
    return { dias: Object.values(acc).sort((a, b) => b.dia.localeCompare(a.dia)), total }
  }, [ventasQ.data])

  return (
    <Card className="p-0 overflow-hidden">
      <div className="flex items-center justify-between px-3.5 py-2.5 border-b border-border-subtle">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Mes en curso</h2>
        <span className="text-[12px] tabular font-semibold">{cop(total)}</span>
      </div>
      {ventasQ.loading ? (
        <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
      ) : dias.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted-foreground">Sin ventas este mes.</p>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {dias.map(d => (
            <li key={d.dia} className="flex items-center gap-2 px-3.5 py-2 text-[13px]">
              <span className="tabular text-muted-foreground w-28 shrink-0">{d.dia}</span>
              <span className="flex-1 text-[12px] text-muted-foreground">{num(d.count)} {d.count === 1 ? 'venta' : 'ventas'}</span>
              <span className="tabular font-semibold shrink-0">{cop(d.total)}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
