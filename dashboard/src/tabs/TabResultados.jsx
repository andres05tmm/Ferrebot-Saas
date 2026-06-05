/*
 * TabResultados — estado de resultados (P&L) del rango, SOLO admin (Fase 12, Slice 2).
 * GET /reportes/resultados?desde&hasta (default mes en curso). Tarjetas del P&L + gráfica (recharts).
 * El costo de ventas es EXACTO desde que se registra por venta; las ventas previas sin costo cuentan 0.
 * Live: re-fetch ante venta_registrada / gasto_registrado / inventario_actualizado / reconnected.
 */
import { useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { DollarSign, Boxes, Receipt, TrendingUp, Wallet } from 'lucide-react'
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell } from 'recharts'
import { useFetch, cop, mesActualCO } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'

export default function TabResultados() {
  const { isAdmin } = useAuth()
  // Resultados es del negocio completo: oculto para el vendedor (no se pide el endpoint).
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        El estado de resultados es solo para administradores.
      </Card>
    )
  }
  return <ResultadosContenido />
}

function ResultadosContenido() {
  const { refreshKey } = useOutletContext() ?? {}
  const [rango, setRango] = useState(mesActualCO())
  const setCampo = (k) => (e) => setRango(prev => ({ ...prev, [k]: e.target.value }))

  const path = `/reportes/resultados?desde=${rango.desde}&hasta=${rango.hasta}`
  const q = useFetch(path, [refreshKey, rango.desde, rango.hasta])
  useRealtimeEvent(
    ['venta_registrada', 'gasto_registrado', 'inventario_actualizado', 'reconnected'],
    q.refetch,
  )

  const d = q.data || {}
  const ingresos = Number(d.ingresos ?? 0)
  const costo = Number(d.costo_ventas ?? 0)
  const bruta = Number(d.utilidad_bruta ?? 0)
  const gastos = Number(d.gastos ?? 0)
  const neta = Number(d.utilidad_neta ?? 0)

  const chart = useMemo(() => [
    { nombre: 'Ingresos', valor: ingresos, color: 'hsl(var(--success))' },
    { nombre: 'Costo', valor: costo, color: 'hsl(var(--warning))' },
    { nombre: 'U. bruta', valor: bruta, color: 'hsl(var(--info))' },
    { nombre: 'Gastos', valor: gastos, color: 'hsl(var(--warning))' },
    { nombre: 'U. neta', valor: neta, color: 'hsl(var(--accent))' },
  ], [ingresos, costo, bruta, gastos, neta])

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-end gap-3">
          <h1 className="text-lg font-semibold tracking-tight mr-auto">Estado de resultados</h1>
          <label className="text-[11px] text-muted-foreground">
            Desde
            <Input type="date" value={rango.desde} onChange={setCampo('desde')} aria-label="Desde" className="h-9 mt-1" />
          </label>
          <label className="text-[11px] text-muted-foreground">
            Hasta
            <Input type="date" value={rango.hasta} onChange={setCampo('hasta')} aria-label="Hasta" className="h-9 mt-1" />
          </label>
        </div>
      </Card>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Metric label="Ingresos" value={cop(ingresos)} icon={DollarSign} tone="text-success" />
        <Metric label="Costo de ventas" value={cop(costo)} icon={Boxes} tone="text-warning" />
        <Metric label="Utilidad bruta" value={cop(bruta)} icon={TrendingUp} tone="text-info" />
        <Metric label="Gastos" value={cop(gastos)} icon={Receipt} tone="text-warning" />
        <Metric label="Utilidad neta" value={cop(neta)} icon={Wallet}
          tone={neta >= 0 ? 'text-success' : 'text-destructive'} hero />
      </div>

      <Card className="p-3.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-3">
          Composición del periodo
        </h2>
        <div style={{ width: '100%', height: 240 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chart} margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
              <XAxis dataKey="nombre" tick={{ fontSize: 11 }} stroke="hsl(var(--text-muted))" />
              <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--text-muted))" width={70}
                tickFormatter={(v) => cop(v)} />
              <Tooltip formatter={(v) => cop(Number(v))} />
              <Bar dataKey="valor" radius={[4, 4, 0, 0]}>
                {chart.map((c, i) => <Cell key={i} fill={c.color} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <p className="text-[11px] text-muted-foreground px-1">
        El costo de ventas es exacto desde que se registra por venta; las ventas anteriores a esa fecha
        (sin costo) cuentan como 0.
      </p>
    </div>
  )
}

function Metric({ label, value, icon: Icon, tone, hero }) {
  return (
    <Card className="p-3.5">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-1.5">
        <Icon className="size-3.5" /> {label}
      </div>
      <div className={`tabular font-semibold ${hero ? 'text-xl' : 'text-[15px]'} ${tone}`}>{value}</div>
    </Card>
  )
}
