/*
 * TabHoy — cockpit del día (E6, recableado a endpoints SaaS).
 * KPIs de GET /reportes/resumen, últimas ventas de GET /ventas (hoy) y stock bajo de
 * GET /inventario/stock?bajo=true. Live: re-fetch ante venta_registrada / inventario_actualizado /
 * reconnected. Diferido a Fase 12 (sin backend aún): evolución, top productos, gastos, detalle de caja.
 */
import { useMemo } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { AlertTriangle, CreditCard, Package, Plus, Receipt, Search, ShoppingCart } from 'lucide-react'
import { useFetch, cop, num } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Badge } from '@/components/ui/badge.jsx'
import KpiCard from '@/components/KpiCard.jsx'
import { cn } from '@/lib/utils'

const HORA_CO = { hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota' }

export default function TabHoy() {
  const navigate = useNavigate()
  const { refreshKey } = useOutletContext() ?? {}
  const deps = [refreshKey]

  const resumenQ = useFetch('/reportes/resumen', deps)
  const ventasQ = useFetch('/ventas', deps)
  const stockQ = useFetch('/inventario/stock?bajo=true', deps)

  // Re-fetch en vivo: el stream SSE de la empresa (una sola conexión, vía RealtimeProvider).
  useRealtimeEvent(['venta_registrada', 'inventario_actualizado', 'reconnected'], () => {
    resumenQ.refetch(); ventasQ.refetch(); stockQ.refetch()
  })

  const resumen = resumenQ.data
  const numVentas = resumen?.num_ventas ?? 0
  const totalVendido = Number(resumen?.total_vendido ?? 0)
  const ticket = Number(resumen?.ticket_promedio ?? 0)

  const metodos = useMemo(() => {
    const obj = resumen?.por_metodo_pago || {}
    const arr = Object.entries(obj)
      .map(([nombre, monto]) => ({ nombre, monto: Number(monto) }))
      .sort((a, b) => b.monto - a.monto)
    const total = arr.reduce((a, m) => a + m.monto, 0)
    return arr.map(m => ({ ...m, pct: total > 0 ? Math.round((m.monto / total) * 100) : 0 }))
  }, [resumen])

  const ultimas = useMemo(() => {
    const arr = Array.isArray(ventasQ.data) ? ventasQ.data : []
    return [...arr].sort((a, b) => String(b.fecha).localeCompare(String(a.fecha))).slice(0, 6)
  }, [ventasQ.data])

  const stockBajo = Array.isArray(stockQ.data) ? stockQ.data : []

  return (
    <div className="space-y-3">
      {/* KPIs del día */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <KpiCard tone="primary" label="Ventas hoy" value={cop(totalVendido)} icon={ShoppingCart}
          loading={resumenQ.loading} heroValue iconStyle="filled"
          sub={`${numVentas} ${numVentas === 1 ? 'venta' : 'ventas'}`} />
        <KpiCard headerBand tone="info" label="Pedidos" value={num(numVentas)} icon={ShoppingCart}
          sub="hoy" />
        <KpiCard headerBand tone="success" label="Ticket promedio" value={cop(ticket)} icon={CreditCard}
          sub="por venta" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <MetodosPago items={metodos} />
        <UltimasVentas ventas={ultimas} loading={ventasQ.loading} onMore={() => navigate('/historial')} />
        <StockBajo items={stockBajo} onMore={() => navigate('/inventario')} />
      </div>

      <QuickActions navigate={navigate} />
    </div>
  )
}

const METODO_COLOR = {
  efectivo: 'hsl(var(--success))',
  nequi: 'hsl(var(--accent))',
  transferencia: 'hsl(var(--accent))',
  tarjeta: 'hsl(var(--info))',
  daviplata: 'hsl(var(--info))',
  fiado: 'hsl(var(--warning))',
}

function MetodosPago({ items }) {
  const total = items.reduce((a, m) => a + m.monto, 0)
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
                <span className="font-medium capitalize truncate">{m.nombre}</span>
                <span className="tabular font-semibold shrink-0">{cop(m.monto)}</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-2 rounded-full bg-surface-2 overflow-hidden">
                  <div className="h-full rounded-full transition-all duration-base"
                    style={{ width: `${Math.max(4, m.pct)}%`, background: METODO_COLOR[m.nombre] || 'hsl(var(--text-muted))' }} />
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

function UltimasVentas({ ventas, loading, onMore }) {
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
        <button onClick={onMore} className="text-[11px] text-muted-foreground hover:text-foreground">ver todas</button>
      </div>
      {loading ? (
        <p className="py-8 text-center text-sm text-muted-foreground">Cargando…</p>
      ) : ventas.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">Sin ventas registradas hoy.</p>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {ventas.map(v => (
            <li key={v.id} className="py-1.5 flex items-center gap-2.5">
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-[11px] text-muted-foreground tabular">
                    {new Date(v.fecha).toLocaleTimeString('es-CO', HORA_CO)}
                  </span>
                  <span className="text-[13px] font-semibold tabular">{cop(Number(v.total))}</span>
                </div>
                <div className="text-[11px] text-muted-foreground truncate mt-0.5">N.º {v.consecutivo}</div>
              </div>
              <Badge variant="outline" className="text-[10px] h-5 px-1.5 shrink-0 capitalize">{v.metodo_pago}</Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function StockBajo({ items, onMore }) {
  return (
    <Card className="p-3.5">
      <div className="flex items-center justify-between mb-2.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
          <AlertTriangle className="size-3.5 text-warning" /> Stock bajo
        </h2>
        {items.length > 0 && (
          <Badge variant="outline" className="h-5 text-[10px] bg-warning/10 text-warning border-warning/20">
            {items.length} {items.length === 1 ? 'alerta' : 'alertas'}
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
            {items.slice(0, 6).map(p => (
              <li key={p.producto_id} className="py-1.5 flex items-center gap-2 text-[12px]">
                <Package className="size-3.5 text-muted-foreground shrink-0" />
                <span className="flex-1 truncate">{p.nombre}</span>
                <span className={cn('tabular font-semibold shrink-0',
                  Number(p.stock_actual) <= 5 ? 'text-primary' : 'text-warning')}>
                  {num(Number(p.stock_actual))}
                </span>
              </li>
            ))}
          </ul>
          <button onClick={onMore} className="w-full mt-3 text-[11px] text-primary hover:underline font-medium">
            ver todos en inventario
          </button>
        </>
      )}
    </Card>
  )
}

function QuickActions({ navigate }) {
  const actions = [
    { label: 'Nueva venta', icon: Plus, to: '/ventas' },
    { label: 'Gasto', icon: Receipt, to: '/gastos' },
    { label: 'Cliente', icon: Search, to: '/clientes' },
    { label: 'Inventario', icon: Package, to: '/inventario' },
  ]
  return (
    <Card className="p-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {actions.map(a => {
          const Icon = a.icon
          return (
            <button key={a.label} onClick={() => navigate(a.to)}
              className="flex items-center gap-2.5 p-3 rounded-md border border-border bg-surface hover:border-primary/40 hover:bg-primary/[0.03] transition-colors text-left">
              <span className="grid place-items-center rounded-md size-8 shrink-0 bg-primary/10 text-primary">
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
