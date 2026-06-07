/*
 * TabHoy — cockpit operativo del día (layout portado del dashboard original, recableado a endpoints SaaS).
 * KPIs/métodos → /reportes/resumen · evolución+sparkline → /reportes/serie-ventas · semana/mes →
 * /reportes/totales · caja → /caja/actual (404 = cerrada) · gastos → /gastos (hoy) · últimas ventas →
 * /ventas · top productos → /reportes/top-productos · stock bajo → /inventario/stock?bajo=true.
 * Cada panel maneja su loading/empty (un fallo no rompe el resto). Live: re-fetch ante venta/caja/gasto/
 * inventario/reconnected. Datos por api.js.
 */
import { useMemo, useState } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import {
  AreaChart, Area, ResponsiveContainer, CartesianGrid, XAxis, YAxis, Tooltip,
} from 'recharts'
import {
  ArrowRight, Plus, AlertTriangle, ShoppingCart, Receipt, Package, Search, Activity,
  CreditCard, Briefcase, CalendarDays,
} from 'lucide-react'
import { useFetch, cop, num, rangoHoyCO, ProductThumb } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Badge } from '@/components/ui/badge.jsx'
import KpiCard from '@/components/KpiCard.jsx'
import { cn } from '@/lib/utils'

const HORA_CO = { hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota' }
const EVENTOS = [
  'venta_registrada', 'venta_anulada', 'venta_editada', 'caja_abierta', 'caja_cerrada',
  'caja_movimiento', 'gasto_registrado', 'inventario_actualizado', 'reconnected',
]

export default function TabHoy() {
  const navigate = useNavigate()
  const { refreshKey } = useOutletContext() ?? {}
  const deps = [refreshKey]

  // Rango/fecha de HOY en Colombia (para gastos y top-productos del día).
  const rangoHoy = useMemo(() => rangoHoyCO(), [refreshKey])
  const hoyStr = useMemo(() => new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' }), [refreshKey])

  const resumenQ = useFetch('/reportes/resumen', deps)
  const serieQ = useFetch('/reportes/serie-ventas?dias=30', deps)
  const totalesQ = useFetch('/reportes/totales', deps)
  const cajaQ = useFetch('/caja/actual', deps)                       // 404 = caja cerrada (useFetch → error)
  const gastosQ = useFetch(`/gastos?desde=${rangoHoy.desde}&hasta=${rangoHoy.hasta}`, deps)
  const ventasQ = useFetch('/ventas', deps)
  const topQ = useFetch(`/reportes/top-productos?desde=${hoyStr}&hasta=${hoyStr}&limite=5`, deps)
  const stockQ = useFetch('/inventario/stock?bajo=true', deps)

  useRealtimeEvent(EVENTOS, () => {
    resumenQ.refetch(); serieQ.refetch(); totalesQ.refetch(); cajaQ.refetch()
    gastosQ.refetch(); ventasQ.refetch(); topQ.refetch(); stockQ.refetch()
  })

  // ── KPIs principales (resumen del día) ──────────────────────────────────────
  const resumen = resumenQ.data
  const totalHoy = Number(resumen?.total_vendido ?? 0)
  const pedidosHoy = resumen?.num_ventas ?? 0
  const ticketProm = Number(resumen?.ticket_promedio ?? 0)

  // ── Totales semana/mes ──────────────────────────────────────────────────────
  const totalSemana = Number(totalesQ.data?.semana ?? 0)
  const totalMes = Number(totalesQ.data?.mes ?? 0)

  // ── Serie de ventas (30d → 7d para sparkline/delta) ─────────────────────────
  const serie30 = useMemo(
    () => (Array.isArray(serieQ.data) ? serieQ.data : []).map(p => ({ fecha: p.fecha, total: Number(p.total) || 0 })),
    [serieQ.data],
  )
  const serie7 = useMemo(() => serie30.slice(-7), [serie30])
  const deltaAyer = useMemo(() => {
    if (serie7.length < 2) return null
    const last = serie7[serie7.length - 1].total
    const prev = serie7[serie7.length - 2].total
    if (prev <= 0) return null
    return ((last - prev) / prev) * 100
  }, [serie7])

  // ── Métodos de pago (del resumen) ───────────────────────────────────────────
  const metodosPago = useMemo(() => {
    const obj = resumen?.por_metodo_pago || {}
    const arr = Object.entries(obj)
      .map(([nombre, monto]) => ({ nombre: capitalizar(nombre), monto: Number(monto) }))
      .sort((a, b) => b.monto - a.monto)
    const total = arr.reduce((a, m) => a + m.monto, 0)
    return arr.map(m => ({ ...m, pct: total > 0 ? Math.round((m.monto / total) * 100) : 0 }))
  }, [resumen])
  const totalMetodos = metodosPago.reduce((a, m) => a + m.monto, 0)

  // ── Caja ────────────────────────────────────────────────────────────────────
  const caja = cajaQ.data
  const cajaAbierta = !!caja && caja.estado === 'abierta'
  const aperturaCaja = Number(caja?.saldo_inicial ?? 0)
  const horaApertura = caja?.fecha_apertura
    ? new Date(caja.fecha_apertura).toLocaleTimeString('es-CO', HORA_CO)
    : ''

  // ── Gastos de hoy ───────────────────────────────────────────────────────────
  const gastos = Array.isArray(gastosQ.data) ? gastosQ.data : []
  const totalGastos = gastos.reduce((a, g) => a + Number(g.monto || 0), 0)
  const numGastos = gastos.length

  // ── Últimas ventas (hoy) ────────────────────────────────────────────────────
  const ventas = Array.isArray(ventasQ.data) ? ventasQ.data : []
  const ultimas = useMemo(
    () => [...ventas].sort((a, b) => String(b.fecha).localeCompare(String(a.fecha))).slice(0, 6),
    [ventas],
  )
  const numMovs = ventas.length + numGastos

  // ── Top productos (hoy) ─────────────────────────────────────────────────────
  const topProductos = useMemo(() => {
    const arr = (Array.isArray(topQ.data) ? topQ.data : [])
      .map(p => ({ nombre: p.nombre, monto: Number(p.ingreso) || 0, cant: Number(p.cantidad) || 0 }))
    const max = arr[0]?.monto || 1
    return arr.slice(0, 5).map(p => ({ ...p, pct: Math.max(8, Math.round((p.monto / max) * 100)) }))
  }, [topQ.data])

  // ── Stock bajo ──────────────────────────────────────────────────────────────
  const stockArr = Array.isArray(stockQ.data) ? stockQ.data : []
  const stockBajo = useMemo(
    () => stockArr.map(p => ({ nombre: p.nombre, stock: Number(p.stock_actual) || 0 })).slice(0, 5),
    [stockArr],
  )

  return (
    <div className="space-y-3">
      {/* KPI STRIP — Ventas / Caja / Gastos */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <KpiCard
          tone="primary" label="Ventas hoy" value={cop(totalHoy)} icon={ShoppingCart}
          loading={resumenQ.loading} iconStyle="filled" heroValue deltaPct={deltaAyer} spark={serie7}
          sub={`${pedidosHoy} ${pedidosHoy === 1 ? 'venta' : 'ventas'}`}
        />
        <CajaCard
          abierta={cajaAbierta} apertura={aperturaCaja} horaApertura={horaApertura}
          numMovs={numMovs} onClick={() => navigate('/caja')}
        />
        <KpiCard
          tone="danger" label="Gastos hoy" value={cop(totalGastos)} icon={Receipt}
          onClick={() => navigate('/gastos')} actionLabel="Registrar gasto" iconStyle="filled" heroValue
          sub={`${numGastos} ${numGastos === 1 ? 'registro' : 'registros'}`}
        />
      </div>

      {/* MINI METRIC STRIP — Pedidos / Ticket / Semana / Mes */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard headerBand tone="primary" icon={CalendarDays} label="Pedidos" value={num(pedidosHoy)}
          sub={pedidosHoy > 0 ? `de ${cop(totalHoy)}` : 'sin ventas'} />
        <KpiCard headerBand tone="info" icon={CalendarDays} label="Ticket prom." value={cop(ticketProm)}
          sub="por venta" />
        <KpiCard headerBand tone="success" icon={CalendarDays} label="Total semana" value={cop(totalSemana)}
          sub="últimos 7 días" />
        <KpiCard headerBand coloredValue tone="warning" icon={CalendarDays} label="Total mes" value={cop(totalMes)}
          sub="mes en curso" />
      </div>

      {/* HERO — Evolución (2/3) + Feed live (1/3) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <EvolucionChart serie7={serie7} serie30={serie30} loading={serieQ.loading} />
        <FeedLive ventas={ultimas} productos={topProductos} loading={ventasQ.loading} onMore={() => navigate('/historial')} />
      </div>

      {/* OPERATIVA — Métodos / Top productos / Stock bajo */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <MetodosPago items={metodosPago} total={totalMetodos} />
        <TopProductos items={topProductos} onMore={() => navigate('/top-productos')} />
        <StockBajo items={stockBajo} total={stockArr.length} onMore={() => navigate('/inventario')} />
      </div>

      <QuickActions navigate={navigate} />
    </div>
  )
}

function capitalizar(s) {
  const t = String(s || '')
  return t.charAt(0).toUpperCase() + t.slice(1)
}

// ── CAJA CARD ────────────────────────────────────────────────────────────────
function CajaCard({ abierta, apertura, horaApertura, numMovs, onClick }) {
  const iconColor = abierta ? 'hsl(var(--success))' : 'hsl(var(--warning))'
  const bandBg = abierta ? 'bg-success/15 border-success/25' : 'bg-warning/15 border-warning/25'
  const pillBg = abierta ? 'bg-success text-white' : 'bg-warning text-white'

  return (
    <Card role="button" tabIndex={0} onClick={onClick}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } }}
      aria-label={abierta ? 'Cerrar caja' : 'Abrir caja'}
      className={cn(
        'group relative overflow-hidden p-2.5 cursor-pointer text-left w-full bg-surface border-border',
        'transition-all duration-base ease-out-quad hover:-translate-y-0.5 hover:shadow-md',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
      )}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground truncate">Caja</span>
        <span className="grid place-items-center rounded-md size-6 shrink-0" style={{ background: iconColor }}>
          <Briefcase className="size-3 text-white" aria-hidden="true" />
        </span>
      </div>
      <div className={cn('mt-2 px-2 py-1.5 rounded-md border flex items-center gap-2 min-h-[36px]', bandBg)}>
        <span className={cn('inline-flex items-center px-2 h-5 rounded text-[10px] font-bold uppercase tracking-wide shrink-0', pillBg)}>
          {abierta ? 'Abierta' : 'Cerrada'}
        </span>
        <span className="text-[11px] text-foreground/80 truncate">
          {abierta
            ? (horaApertura ? `${horaApertura} · Base ${cop(apertura)} · ${numMovs} movs` : `Base ${cop(apertura)} · ${numMovs} movs`)
            : 'Pendiente de apertura'}
        </span>
      </div>
    </Card>
  )
}

// ── EVOLUCIÓN — chart con toggle 7d / 30d ─────────────────────────────────────
function EvolucionChart({ serie7, serie30, loading }) {
  const [periodo, setPeriodo] = useState('7d')
  const data = useMemo(() => {
    const src = periodo === '7d' ? serie7 : serie30
    return (src || []).map(d => {
      const fecha = String(d.fecha || '').slice(0, 10)
      const dia = fecha ? new Date(fecha + 'T12:00:00').toLocaleDateString('es-CO', { weekday: 'short', day: 'numeric' }) : ''
      return { fecha, dia, total: Number(d.total) || 0 }
    })
  }, [serie7, serie30, periodo])

  const totalPeriodo = data.reduce((acc, d) => acc + d.total, 0)
  const promDia = data.length > 0 ? totalPeriodo / data.length : 0

  return (
    <Card className="lg:col-span-2 p-3.5">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Evolución de ventas</h2>
          <div className="flex items-baseline gap-2 mt-1 flex-wrap">
            <span className="text-xl font-semibold tracking-tight tabular text-foreground">{cop(totalPeriodo)}</span>
            <span className="text-[10.5px] text-muted-foreground">acumulado · prom. {cop(promDia)}/día</span>
          </div>
        </div>
        <div className="flex gap-1 bg-surface-2 p-1 rounded-md">
          <PeriodPill active={periodo === '7d'} onClick={() => setPeriodo('7d')}>7d</PeriodPill>
          <PeriodPill active={periodo === '30d'} onClick={() => setPeriodo('30d')}>30d</PeriodPill>
        </div>
      </div>

      {loading ? (
        <div className="h-[170px] grid place-items-center text-sm text-muted-foreground">Cargando…</div>
      ) : data.length === 0 ? (
        <div className="h-[170px] grid place-items-center text-sm text-muted-foreground">Sin datos para este período.</div>
      ) : (
        <ResponsiveContainer width="100%" height={170}>
          <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="hoyEvolGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="hsl(var(--accent))" stopOpacity={0.15} />
                <stop offset="95%" stopColor="hsl(var(--accent))" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="hsl(var(--border-subtle))" vertical={false} strokeDasharray="3 3" />
            <XAxis dataKey="dia" tick={{ fill: 'hsl(var(--text-muted))', fontSize: 10 }} axisLine={false}
              tickLine={false} tickMargin={8} interval={periodo === '30d' ? 'preserveStartEnd' : 0} minTickGap={20} />
            <YAxis tick={{ fill: 'hsl(var(--text-muted))', fontSize: 10 }} axisLine={false} tickLine={false}
              tickFormatter={v => v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}k` : v} width={42} />
            <Tooltip
              contentStyle={{ background: 'hsl(var(--bg-surface))', border: '1px solid hsl(var(--border))', borderRadius: 8, color: 'hsl(var(--text-primary))', fontSize: 11 }}
              labelStyle={{ color: 'hsl(var(--text-muted))', marginBottom: 4 }}
              formatter={v => [cop(v), 'Ventas']}
              cursor={{ stroke: 'hsl(var(--accent))', strokeWidth: 1, strokeDasharray: '4 4' }} />
            <Area type="monotone" dataKey="total" stroke="hsl(var(--accent))" strokeWidth={2} fill="url(#hoyEvolGrad)"
              activeDot={{ r: 4, fill: 'hsl(var(--accent))', stroke: 'hsl(var(--bg-surface))', strokeWidth: 2 }} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </Card>
  )
}

function PeriodPill({ active, onClick, children }) {
  return (
    <button onClick={onClick}
      className={cn('px-2.5 py-1 text-[11px] font-medium rounded transition-colors',
        active ? 'bg-surface text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground')}>
      {children}
    </button>
  )
}

// ── FEED LIVE — últimas ventas + productos del día ────────────────────────────
function FeedLive({ ventas, productos = [], loading, onMore }) {
  const prodsTop = (productos || []).slice(0, 4)
  return (
    <Card className="p-3.5">
      <div className="flex items-center justify-between mb-2.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-2">
          Últimas ventas
          <span className="relative flex size-2">
            <span className="absolute inline-flex size-full rounded-full bg-success/60 animate-ping opacity-75" />
            <span className="relative inline-flex size-2 rounded-full bg-success" />
          </span>
        </h2>
        <button onClick={onMore} className="text-[11px] text-muted-foreground hover:text-foreground inline-flex items-center gap-1">
          ver todas <ArrowRight className="size-3" />
        </button>
      </div>

      {loading ? (
        <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
      ) : ventas.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted-foreground">Sin ventas registradas hoy.</p>
      ) : (
        <>
          <ul className="divide-y divide-border-subtle">
            {ventas.map(v => (
              <li key={v.id} className="py-1.5 flex items-center gap-2.5">
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2">
                    <span className="text-[11px] text-muted-foreground tabular">
                      {v.fecha ? new Date(v.fecha).toLocaleTimeString('es-CO', HORA_CO) : ''}
                    </span>
                    <span className="text-[13px] font-semibold tabular">{cop(Number(v.total))}</span>
                  </div>
                  <div className="text-[11px] text-muted-foreground truncate mt-0.5">N.º {v.consecutivo}</div>
                </div>
                <Badge variant="outline" className={cn('text-[10px] h-5 px-1.5 shrink-0 capitalize', metodoTone(v.metodo_pago))}>
                  {v.metodo_pago || '—'}
                </Badge>
              </li>
            ))}
          </ul>

          {prodsTop.length > 0 && (
            <div className="mt-3 pt-2.5 border-t border-border-subtle">
              <ul className="space-y-1.5">
                {prodsTop.map((p, i) => (
                  <li key={`prod-${i}`} className="flex items-center gap-2.5">
                    <ProductThumb nombre={p.nombre} size={28} />
                    <span className="flex-1 text-[12px] text-foreground truncate">{p.nombre}</span>
                    <span className="text-[11px] tabular text-muted-foreground shrink-0">{num(p.cant)} ud</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </Card>
  )
}

function metodoTone(metodo) {
  const m = String(metodo || '').toLowerCase()
  if (m.includes('efectivo')) return 'bg-success/10 text-success border-success/20'
  if (m.includes('transf')) return 'bg-warning/10 text-warning border-warning/20'
  // datafono = método vigente; 'tarj' se conserva para colorear ventas históricas con tarjeta.
  if (m.includes('datafono') || m.includes('tarj')) return 'bg-info/10 text-info border-info/20'
  if (m.includes('fiado') || m.includes('credito')) return 'bg-danger/10 text-danger border-danger/20'
  return 'bg-surface-2 text-muted-foreground border-border'
}

// ── MÉTODOS DE PAGO ───────────────────────────────────────────────────────────
const METODO_BAR_COLORS = {
  Efectivo: 'hsl(var(--success))', Transferencia: 'hsl(var(--accent))',
  Datafono: 'hsl(var(--info))', Fiado: 'hsl(var(--warning))',
  Tarjeta: 'hsl(var(--info))',   // histórico (las claves ausentes caen al color muted por defecto)
}

function MetodosPago({ items, total }) {
  return (
    <Card className="p-3.5">
      <div className="flex items-center justify-between mb-2.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
          <CreditCard className="size-3.5" /> Métodos de pago · Hoy
        </h2>
        {total > 0 && <span className="text-[11px] text-muted-foreground tabular">{cop(total)}</span>}
      </div>
      {items.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">Sin ventas hoy.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((m, i) => (
            <li key={i}>
              <div className="flex items-baseline justify-between mb-1 text-[12px]">
                <span className="font-medium truncate">{m.nombre}</span>
                <span className="tabular font-semibold shrink-0">{cop(m.monto)}</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-2 rounded-full bg-surface-2 overflow-hidden">
                  <div className="h-full rounded-full transition-all duration-base"
                    style={{ width: `${Math.max(4, m.pct)}%`, background: METODO_BAR_COLORS[m.nombre] || 'hsl(var(--text-muted))' }} />
                </div>
                <span className="text-[10px] text-muted-foreground tabular w-9 text-right shrink-0">{m.pct}%</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

// ── TOP PRODUCTOS / STOCK BAJO / QUICK ACTIONS ────────────────────────────────
function TopProductos({ items, onMore }) {
  return (
    <Card className="p-3.5">
      <div className="flex items-center justify-between mb-2.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Top productos hoy</h2>
        <button onClick={onMore} className="text-[11px] text-muted-foreground hover:text-foreground inline-flex items-center gap-1">
          ver todos <ArrowRight className="size-3" />
        </button>
      </div>
      {items.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">Sin ventas hoy.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((p, i) => (
            <li key={i} className="flex items-center gap-2.5">
              <ProductThumb nombre={p.nombre} size={32} />
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline justify-between mb-1 gap-2 text-[12px]">
                  <span className="font-medium truncate">{p.nombre}</span>
                  <span className="tabular font-semibold shrink-0">{cop(p.monto)}</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex-1 h-1.5 rounded-full bg-surface-2 overflow-hidden">
                    <div className="h-full rounded-full bg-primary transition-all duration-base" style={{ width: `${p.pct}%` }} />
                  </div>
                  <span className="text-[10px] text-muted-foreground tabular w-12 text-right shrink-0">{num(p.cant)} ud</span>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function StockBajo({ items, total, onMore }) {
  return (
    <Card className="p-3.5">
      <div className="flex items-center justify-between mb-2.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
          <AlertTriangle className="size-3.5 text-warning" /> Stock bajo
        </h2>
        {total > 0 && (
          <Badge variant="outline" className="h-5 text-[10px] bg-warning/10 text-warning border-warning/20">
            {total} {total === 1 ? 'alerta' : 'alertas'}
          </Badge>
        )}
      </div>
      {items.length === 0 ? (
        <div className="py-6 flex flex-col items-center gap-2 text-muted-foreground">
          <AlertTriangle className="size-5 text-warning opacity-60" />
          <p className="text-sm">Stock sin alertas.</p>
        </div>
      ) : (
        <>
          <ul className="divide-y divide-border-subtle">
            {items.map((p, i) => {
              const isCrit = p.stock <= 5
              return (
                <li key={i} className="py-1.5 flex items-center gap-2 text-[12px]">
                  <Package className="size-3.5 text-muted-foreground shrink-0" />
                  <span className="flex-1 truncate">{p.nombre}</span>
                  <span className={cn('tabular font-semibold shrink-0', isCrit ? 'text-primary' : 'text-warning')}>
                    {num(p.stock)}
                  </span>
                </li>
              )
            })}
          </ul>
          <button onClick={onMore} className="w-full mt-3 text-[11px] text-primary hover:underline font-medium inline-flex items-center justify-center gap-1">
            ver todos en inventario <ArrowRight className="size-3" />
          </button>
        </>
      )}
    </Card>
  )
}

function QuickActions({ navigate }) {
  const actions = [
    { label: 'Nueva venta', icon: Plus, tone: 'primary', to: '/ventas' },
    { label: 'Gasto', icon: Receipt, tone: 'warning', to: '/gastos' },
    { label: 'Cliente', icon: Search, tone: 'info', to: '/clientes' },
    { label: 'Inventario', icon: Package, tone: 'success', to: '/inventario' },
  ]
  const toneStyles = {
    primary: { color: 'hsl(var(--accent))', bg: 'bg-primary/10' },
    warning: { color: 'hsl(var(--warning))', bg: 'bg-warning/10' },
    info: { color: 'hsl(var(--info))', bg: 'bg-info/10' },
    success: { color: 'hsl(var(--success))', bg: 'bg-success/10' },
  }
  return (
    <Card className="p-3">
      <div className="flex items-center justify-between mb-2.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
          <Activity className="size-3.5" /> Acciones rápidas
        </h2>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {actions.map(a => {
          const t = toneStyles[a.tone]
          const Icon = a.icon
          return (
            <button key={a.label} onClick={() => navigate(a.to)}
              className="group flex items-center gap-2.5 p-3 rounded-md border border-border bg-surface hover:border-primary/40 hover:bg-primary/[0.03] transition-colors text-left">
              <span className={cn('grid place-items-center rounded-md size-8 shrink-0', t.bg)} style={{ color: t.color }}>
                <Icon className="size-4" />
              </span>
              <span className="text-[12px] font-medium truncate">{a.label}</span>
            </button>
          )
        })}
      </div>
    </Card>
  )
}
