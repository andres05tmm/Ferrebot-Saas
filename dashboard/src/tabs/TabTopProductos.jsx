/*
 * TabTopProductos — ranking de productos por ingreso/cantidad del rango (Fase 12, Slice 2).
 * GET /reportes/top-productos?desde&hasta&limite (default mes en curso). Tabla + gráfica (recharts).
 * Scoping por rol lo decide el backend (vendedor: lo suyo; admin: todo). Live: re-fetch ante
 * venta_registrada / reconnected.
 */
import { useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Trophy } from 'lucide-react'
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip } from 'recharts'
import { useFetch, cop, num, mesActualCO } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'

const LIMITE = 10

export default function TabTopProductos() {
  const { refreshKey } = useOutletContext() ?? {}
  const [rango, setRango] = useState(mesActualCO())
  const setCampo = (k) => (e) => setRango(prev => ({ ...prev, [k]: e.target.value }))

  const path = `/reportes/top-productos?desde=${rango.desde}&hasta=${rango.hasta}&limite=${LIMITE}`
  const q = useFetch(path, [refreshKey, rango.desde, rango.hasta])
  useRealtimeEvent(['venta_registrada', 'reconnected'], q.refetch)

  const filas = Array.isArray(q.data) ? q.data : []
  // Gráfica horizontal: el más vendido arriba (recharts dibuja la primera fila abajo → invertir).
  const chart = useMemo(
    () => filas.map(f => ({ nombre: f.nombre, ingreso: Number(f.ingreso) })).reverse(),
    [filas],
  )

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-end gap-3">
          <h1 className="text-lg font-semibold tracking-tight mr-auto inline-flex items-center gap-2">
            <Trophy className="size-4 text-warning" /> Top productos
          </h1>
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

      {filas.length > 0 && (
        <Card className="p-3.5">
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-3">
            Ingreso por producto
          </h2>
          <div style={{ width: '100%', height: Math.max(160, chart.length * 36) }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chart} layout="vertical" margin={{ top: 4, right: 12, bottom: 4, left: 8 }}>
                <XAxis type="number" tick={{ fontSize: 11 }} stroke="hsl(var(--text-muted))" tickFormatter={(v) => cop(v)} />
                <YAxis type="category" dataKey="nombre" tick={{ fontSize: 11 }} width={110} stroke="hsl(var(--text-muted))" />
                <Tooltip formatter={(v) => cop(Number(v))} />
                <Bar dataKey="ingreso" fill="hsl(var(--accent))" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      <Card className="p-0 overflow-hidden">
        {q.loading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : filas.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Sin ventas en el periodo.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {filas.map((f, i) => (
              <li key={f.producto_id} className="flex items-center gap-3 px-3.5 py-2.5">
                <span className="grid place-items-center size-6 rounded-full bg-surface-2 text-[11px] font-semibold tabular shrink-0">
                  {i + 1}
                </span>
                <span className="min-w-0 flex-1 text-[13px] font-medium truncate">{f.nombre}</span>
                <span className="text-[12px] text-muted-foreground tabular shrink-0">{num(Number(f.cantidad))} u.</span>
                <span className="text-[13px] font-semibold tabular shrink-0 w-24 text-right">{cop(Number(f.ingreso))}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}
