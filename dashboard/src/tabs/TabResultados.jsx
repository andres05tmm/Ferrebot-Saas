/*
 * TabResultados — analítica financiera del negocio, SOLO admin (Fase 12 Slice 2 + reforma F3).
 *
 * Tres sub-tabs sobre el mismo rango:
 *   - P&L: estado de resultados (ingresos, costo exacto, utilidades) + proyección de cierre de mes
 *     (promedio de los últimos 14 días con movimiento — fórmula del dashboard viejo).
 *   - Flujo de dinero: qué ENTRÓ (ventas cobradas, abonos de fiados, ingresos de caja) y qué SALIÓ
 *     (gastos, abonos a proveedor, egresos) con el neto. El fiado NO es entrada (es cartera).
 *   - Margen por producto/categoría: ingresos vs COGS snapshot con COBERTURA honesta (un margen
 *     sin costo registrado no es margen: se marca).
 * Live: re-fetch ante venta_registrada / gasto_registrado / inventario_actualizado / reconnected.
 */
import { useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import {
  DollarSign, Boxes, Receipt, TrendingUp, Wallet, ArrowDownToLine, ArrowUpFromLine, CalendarClock,
} from 'lucide-react'
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell } from 'recharts'
import { useFetch, cop, num, mesActualCO } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const EVENTOS = ['venta_registrada', 'gasto_registrado', 'inventario_actualizado', 'reconnected']

const SUBTABS = [
  { id: 'pl', label: 'Resultados' },
  { id: 'flujo', label: 'Flujo de dinero' },
  { id: 'margen', label: 'Margen por producto' },
]

// Etiquetas legibles de métodos/categorías (los valores canónicos vienen del backend).
const METODO_LABEL = {
  efectivo: 'Efectivo', transferencia: 'Transferencia', tarjeta: 'Tarjeta',
  nequi: 'Nequi', daviplata: 'Daviplata', datafono: 'Datáfono',
}
const CATEGORIA_LABEL = {
  transporte: 'Transporte', papeleria: 'Papelería', servicios: 'Servicios',
  nomina: 'Nómina', mantenimiento: 'Mantenimiento', otros: 'Otros',
}

export default function TabResultados() {
  const { isAdmin } = useAuth()
  // Analítica del negocio completo: oculta para el vendedor (no se piden los endpoints).
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        Los resultados financieros son solo para administradores.
      </Card>
    )
  }
  return <ResultadosContenido />
}

function ResultadosContenido() {
  const [sub, setSub] = useState('pl')
  const [rango, setRango] = useState(mesActualCO())
  const setCampo = (k) => (e) => setRango(prev => ({ ...prev, [k]: e.target.value }))

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-end gap-3">
          <h1 className="text-lg font-semibold tracking-tight mr-auto">Resultados financieros</h1>
          <label className="text-[11px] text-muted-foreground">
            Desde
            <Input type="date" value={rango.desde} onChange={setCampo('desde')} aria-label="Desde" className="h-9 mt-1" />
          </label>
          <label className="text-[11px] text-muted-foreground">
            Hasta
            <Input type="date" value={rango.hasta} onChange={setCampo('hasta')} aria-label="Hasta" className="h-9 mt-1" />
          </label>
        </div>
        <div className="mt-3 flex gap-2">
          {SUBTABS.map(t => (
            <button key={t.id} onClick={() => setSub(t.id)} aria-pressed={sub === t.id}
              className={`px-2.5 py-1 rounded-md border text-body-sm ${
                sub === t.id ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>
              {t.label}
            </button>
          ))}
        </div>
      </Card>

      {sub === 'pl' && <PanelPL rango={rango} />}
      {sub === 'flujo' && <PanelFlujo rango={rango} />}
      {sub === 'margen' && <PanelMargen rango={rango} />}
    </div>
  )
}

// --- Sub-tab: P&L + proyección ------------------------------------------------
function PanelPL({ rango }) {
  const { refreshKey } = useOutletContext() ?? {}
  const q = useFetch(
    `/reportes/resultados?desde=${rango.desde}&hasta=${rango.hasta}`,
    [refreshKey, rango.desde, rango.hasta],
  )
  const proyQ = useFetch('/reportes/proyeccion-caja', [refreshKey])
  useRealtimeEvent(EVENTOS, () => { q.refetch(); proyQ.refetch() })

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

  const p = proyQ.data || {}

  return (
    <>
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

      <Card className="p-3.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2 inline-flex items-center gap-1.5">
          <CalendarClock className="size-3.5" aria-hidden="true" /> Proyección del cierre de mes
        </h2>
        {proyQ.loading ? (
          <p className="text-body-sm text-muted-foreground">Calculando…</p>
        ) : (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 text-body-sm">
            <div>
              <div className="text-caption text-muted-foreground">Venta diaria promedio (14d)</div>
              <div className="tabular font-semibold">{cop(Number(p.promedio_venta_diaria || 0))}</div>
            </div>
            <div>
              <div className="text-caption text-muted-foreground">Gasto diario promedio (14d)</div>
              <div className="tabular font-semibold">{cop(Number(p.promedio_gasto_diario || 0))}</div>
            </div>
            <div>
              <div className="text-caption text-muted-foreground">Ventas proyectadas del mes</div>
              <div className="tabular font-semibold">{cop(Number(p.proyeccion_ventas_mes || 0))}</div>
            </div>
            <div>
              <div className="text-caption text-muted-foreground">
                Neto proyectado ({p.dias_restantes ?? 0} días restantes)
              </div>
              <div className={`tabular font-semibold ${Number(p.proyeccion_neto_mes || 0) >= 0 ? 'text-success' : 'text-destructive'}`}>
                {cop(Number(p.proyeccion_neto_mes || 0))}
              </div>
            </div>
          </div>
        )}
      </Card>

      <p className="text-[11px] text-muted-foreground px-1">
        El costo de ventas es exacto desde que se registra por venta; las ventas anteriores a esa fecha
        (sin costo) cuentan como 0.
      </p>
    </>
  )
}

// --- Sub-tab: flujo de dinero ---------------------------------------------------
function PanelFlujo({ rango }) {
  const { refreshKey } = useOutletContext() ?? {}
  const q = useFetch(
    `/reportes/flujo-dinero?desde=${rango.desde}&hasta=${rango.hasta}`,
    [refreshKey, rango.desde, rango.hasta],
  )
  useRealtimeEvent(EVENTOS, q.refetch)

  const d = q.data || {}
  const entradas = Number(d.total_entradas ?? 0)
  const salidas = Number(d.total_salidas ?? 0)
  const neto = Number(d.neto ?? 0)

  const filaEntradas = [
    ...Object.entries(d.ventas_por_metodo || {}).map(([m, v]) => [METODO_LABEL[m] || m, v]),
    ['Abonos de clientes (fiados)', d.abonos_fiados],
    ['Otros ingresos de caja', d.ingresos_caja],
  ].filter(([, v]) => Number(v) > 0)

  const filaSalidas = [
    ...Object.entries(d.gastos_por_categoria || {}).map(([c, v]) => [`Gastos · ${CATEGORIA_LABEL[c] || c}`, v]),
    ['Abonos a proveedores', d.abonos_proveedores],
    ['Otros egresos de caja', d.egresos_caja],
  ].filter(([, v]) => Number(v) > 0)

  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <Metric label="Entró" value={cop(entradas)} icon={ArrowDownToLine} tone="text-success" />
        <Metric label="Salió" value={cop(salidas)} icon={ArrowUpFromLine} tone="text-warning" />
        <Metric label="Neto" value={cop(neto)} icon={Wallet}
          tone={neto >= 0 ? 'text-success' : 'text-destructive'} hero />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Card className="p-3.5">
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
            Entradas de dinero
          </h2>
          {q.loading ? <p className="text-body-sm text-muted-foreground">Cargando…</p> : (
            <DesgloseLista filas={filaEntradas} vacio="No entró dinero en el periodo." />
          )}
          {Number(d.ventas_fiado || 0) > 0 && (
            <p className="mt-2 text-caption text-muted-foreground">
              Además se vendió {cop(Number(d.ventas_fiado))} fiado — eso es cartera, no dinero en mano
              (entra cuando el cliente abona).
            </p>
          )}
        </Card>
        <Card className="p-3.5">
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
            Salidas de dinero
          </h2>
          {q.loading ? <p className="text-body-sm text-muted-foreground">Cargando…</p> : (
            <DesgloseLista filas={filaSalidas} vacio="No salió dinero en el periodo." />
          )}
        </Card>
      </div>
    </>
  )
}

function DesgloseLista({ filas, vacio }) {
  if (filas.length === 0) return <p className="text-body-sm text-muted-foreground">{vacio}</p>
  return (
    <ul className="divide-y divide-border-subtle">
      {filas.map(([label, v]) => (
        <li key={label} className="py-1.5 flex justify-between text-body-sm">
          <span>{label}</span>
          <span className="tabular font-medium">{cop(Number(v))}</span>
        </li>
      ))}
    </ul>
  )
}

// --- Sub-tab: margen por producto/categoría --------------------------------------
function PanelMargen({ rango }) {
  const { refreshKey } = useOutletContext() ?? {}
  const [por, setPor] = useState('producto')
  const q = useFetch(
    `/reportes/margen-productos?desde=${rango.desde}&hasta=${rango.hasta}&por=${por}&limite=100`,
    [refreshKey, rango.desde, rango.hasta, por],
  )
  useRealtimeEvent(EVENTOS, q.refetch)
  const filas = Array.isArray(q.data) ? q.data : []

  return (
    <Card className="p-3.5">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Margen bruto (ingresos sin IVA − costo)
        </h2>
        <div className="flex gap-1.5">
          {[['producto', 'Por producto'], ['categoria', 'Por categoría']].map(([v, l]) => (
            <button key={v} onClick={() => setPor(v)} aria-pressed={por === v}
              className={`px-2 py-0.5 rounded-md border text-caption ${
                por === v ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>
              {l}
            </button>
          ))}
        </div>
      </div>
      {q.loading ? (
        <p className="text-body-sm text-muted-foreground py-6 text-center">Cargando…</p>
      ) : filas.length === 0 ? (
        <p className="text-body-sm text-muted-foreground py-6 text-center">Sin ventas de catálogo en el periodo.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-body-sm">
            <thead>
              <tr className="text-left text-caption text-muted-foreground">
                <th className="py-1 pr-2 font-normal">{por === 'categoria' ? 'Categoría' : 'Producto'}</th>
                <th className="py-1 pr-2 font-normal text-right">Cant.</th>
                <th className="py-1 pr-2 font-normal text-right">Ingresos</th>
                <th className="py-1 pr-2 font-normal text-right">Costo</th>
                <th className="py-1 pr-2 font-normal text-right">Margen</th>
                <th className="py-1 font-normal text-right">Margen %</th>
              </tr>
            </thead>
            <tbody>
              {filas.map(f => (
                <tr key={f.clave} className="border-t border-border-subtle">
                  <td className="py-1.5 pr-2">
                    {f.clave}
                    {Number(f.cobertura_pct) < 100 && (
                      <Badge variant="outline" className="ml-1.5 text-[10px] bg-warning/10 text-warning border-warning/20"
                        title="Parte de las unidades vendidas no tiene costo registrado: el margen real puede ser menor.">
                        costo incompleto ({num(Number(f.cobertura_pct))}%)
                      </Badge>
                    )}
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular">{num(Number(f.cantidad))}</td>
                  <td className="py-1.5 pr-2 text-right tabular">{cop(Number(f.ingresos))}</td>
                  <td className="py-1.5 pr-2 text-right tabular">{cop(Number(f.cogs))}</td>
                  <td className={`py-1.5 pr-2 text-right tabular font-medium ${Number(f.margen) >= 0 ? 'text-success' : 'text-destructive'}`}>
                    {cop(Number(f.margen))}
                  </td>
                  <td className="py-1.5 text-right tabular">
                    {f.margen_pct != null ? `${num(Number(f.margen_pct))}%` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
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
