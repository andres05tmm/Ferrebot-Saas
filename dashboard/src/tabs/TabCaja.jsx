/*
 * TabCaja — flujo de caja del día. Gateada por la feature fina 'caja'.
 *
 * Panel profesional: KPIs (apertura / ventas / gastos / efectivo esperado), estado de la caja
 * (abrir o cerrar con arqueo), cuadre de efectivo EN VIVO (GET /caja/arqueo — misma fórmula que el
 * cierre, fuente única), ingresos por método (GET /reportes/resumen), movimientos manuales
 * (POST /caja/movimiento) y gastos del día (GET /gastos). Live: caja/venta/gasto/reconnected.
 *
 * Familia construcción (esConstruccion): la caja es CAJA MENOR de campo, no un mostrador. Una obra no
 * vende tickets, así que se ocultan "Ventas hoy" y los "ingresos por método" (siempre $0), y el cuadre
 * pierde la fila "+ Ventas en efectivo". Apertura/cierre, movimientos manuales y gastos quedan intactos.
 * Para retail (Punto Rojo, demos) TODO queda idéntico.
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import {
  Wallet, Lock, LockOpen, TrendingUp, TrendingDown, Coins, Receipt, ArrowRightLeft,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch, cop, rangoHoyCO } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useFeatures, esConstruccion } from '@/lib/features.jsx'
import KpiCard from '@/components/KpiCard.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'

const EVENTOS = ['caja_abierta', 'caja_cerrada', 'caja_movimiento', 'venta_registrada',
  'venta_anulada', 'gasto_registrado', 'reconnected']

const HORA_CO = { hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota' }
const num = (v) => Number(v || 0)
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s)
function nuevaKey() { return crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random()}` }

export default function TabCaja() {
  const { refreshKey } = useOutletContext() ?? {}
  const construccion = esConstruccion(useFeatures())
  const hoy = rangoHoyCO()

  const arqueoQ = useFetch('/caja/arqueo', [refreshKey])
  const resumenQ = useFetch('/reportes/resumen', [refreshKey])
  const gastosQ = useFetch(`/gastos?desde=${hoy.desde}&hasta=${hoy.hasta}`, [refreshKey])

  const recargar = () => { arqueoQ.refetch(); resumenQ.refetch(); gastosQ.refetch() }
  useRealtimeEvent(EVENTOS, recargar)

  const arqueo = arqueoQ.data || {}
  const abierta = arqueo.estado === 'abierta'
  const resumen = resumenQ.data || {}
  const gastos = Array.isArray(gastosQ.data) ? gastosQ.data : []
  const totalGastos = gastos.reduce((a, g) => a + num(g.monto), 0)

  const apertura = num(arqueo.saldo_inicial)
  const ventasHoy = num(resumen.total_vendido)
  const esperado = num(arqueo.saldo_esperado)

  if (arqueoQ.loading && !arqueoQ.data) {
    return <Card className="p-8 text-center text-sm text-muted-foreground">Cargando caja…</Card>
  }

  return (
    <div className="space-y-3">
      {/* KPIs — bandas de color por tipo. Construcción (caja menor) no muestra "Ventas hoy": su caja no
          registra ventas de mostrador. La grilla pasa de 4 a 3 columnas para no dejar un hueco. */}
      <div className={`grid grid-cols-2 gap-2.5 ${construccion ? 'lg:grid-cols-3' : 'lg:grid-cols-4'}`}>
        <KpiCard headerBand tone="muted" icon={Wallet} label="Apertura"
          value={cop(apertura)} sub="Base inicial de la caja" />
        {!construccion && (
          <KpiCard headerBand tone="success" icon={TrendingUp} label="Ventas hoy"
            value={cop(ventasHoy)} sub={`${resumen.num_ventas ?? 0} ventas · todos los métodos`} coloredValue />
        )}
        <KpiCard headerBand tone="danger" icon={TrendingDown} label="Gastos"
          value={cop(totalGastos)} sub={`${gastos.length} egresos del día`} coloredValue />
        {/* En construcción el efectivo esperado es el KPI clave de la caja menor: ocupa fila completa en
            móvil (col-span-2) para que la grilla de 3 no deje una celda vacía abajo. */}
        <div className={construccion ? 'col-span-2 lg:col-span-1' : 'contents'}>
          <KpiCard headerBand tone="primary" icon={Coins} label="Efectivo esperado"
            value={cop(esperado)}
            sub={construccion ? 'Apertura + movimientos − gastos' : 'Apertura + ventas efectivo − gastos'} coloredValue />
        </div>
      </div>

      {/* Estado de la caja: abrir (cerrada) o cerrar con arqueo (abierta) */}
      <EstadoCaja arqueo={arqueo} abierta={abierta} esperado={esperado} onDone={recargar} />

      {/* Cuadre (+ ingresos por método solo en retail: la caja menor de obra no tiene ventas por método) */}
      {construccion ? (
        <CuadreEfectivo arqueo={arqueo} abierta={abierta} totalGastos={totalGastos} construccion />
      ) : (
        <div className="grid lg:grid-cols-2 gap-3">
          <IngresosPorMetodo porMetodo={resumen.por_metodo_pago} />
          <CuadreEfectivo arqueo={arqueo} abierta={abierta} totalGastos={totalGastos} />
        </div>
      )}

      {/* Movimientos manuales (solo con caja abierta) */}
      {abierta && <MovimientoForm onDone={recargar} />}

      {/* Gastos del día */}
      <GastosDelDia gastos={gastos} total={totalGastos} />
    </div>
  )
}

// ── Estado de la caja ─────────────────────────────────────────────────────────
function EstadoCaja({ arqueo, abierta, esperado, onDone }) {
  const Icono = abierta ? LockOpen : Lock
  const tono = abierta ? 'text-success bg-success/15' : 'text-muted-foreground bg-surface-2'
  const horaApertura = arqueo.fecha_apertura
    ? new Date(arqueo.fecha_apertura).toLocaleTimeString('es-CO', HORA_CO) : null
  return (
    <Card className="p-3.5">
      <div className="flex items-center gap-2.5">
        <span className={`grid place-items-center rounded-lg size-9 ${tono}`}>
          <Icono className="size-4.5" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold">{abierta ? 'Caja abierta' : 'Caja cerrada'}</p>
          <p className="text-[11px] text-muted-foreground">
            {abierta && horaApertura
              ? `Desde las ${horaApertura} · base ${cop(num(arqueo.saldo_inicial))}`
              : 'Registra el saldo inicial para empezar el día.'}
          </p>
        </div>
      </div>
      <div className="mt-3">
        {abierta ? <CierreForm esperado={esperado} onDone={onDone} /> : <AperturaForm onDone={onDone} />}
      </div>
    </Card>
  )
}

function AperturaForm({ onDone }) {
  const [saldo, setSaldo] = useState('')
  const [enviando, setEnviando] = useState(false)

  async function abrir() {
    const n = Number(saldo)
    if (Number.isNaN(n) || n < 0) { toast.error('Indica el saldo inicial'); return }
    setEnviando(true)
    try {
      const res = await api('/caja/apertura', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ saldo_inicial: n }),
      })
      if (res.ok) { toast.success('Caja abierta'); setSaldo(''); onDone() }
      else toast.error('No se pudo abrir la caja')
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <div className="flex items-center gap-2">
      <div className="relative flex-1">
        <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground text-sm">$</span>
        <Input type="number" min="0" step="any" value={saldo} onChange={(e) => setSaldo(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') abrir() }}
          placeholder="Saldo inicial" aria-label="Saldo inicial" className="h-10 pl-6" />
      </div>
      <Button onClick={abrir} disabled={enviando} className="gap-1.5">
        <LockOpen className="size-4" />{enviando ? 'Abriendo…' : 'Abrir caja'}
      </Button>
    </div>
  )
}

function CierreForm({ esperado, onDone }) {
  const [contado, setContado] = useState('')
  const [enviando, setEnviando] = useState(false)
  const dif = contado === '' ? null : Number(contado) - esperado   // contado − esperado

  async function cerrar() {
    const n = Number(contado)
    if (Number.isNaN(n) || n < 0) { toast.error('Indica el saldo contado'); return }
    setEnviando(true)
    try {
      const res = await api('/caja/cierre', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ saldo_contado: n }),
      })
      if (res.ok) {
        const data = await res.json()
        toast.success(`Caja cerrada · diferencia ${cop(Number(data.diferencia ?? 0))}`)
        setContado(''); onDone()
      } else toast.error('No se pudo cerrar la caja')
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground text-sm">$</span>
          <Input type="number" min="0" step="any" value={contado} onChange={(e) => setContado(e.target.value)}
            placeholder="Efectivo contado en caja" aria-label="Saldo contado" className="h-10 pl-6" />
        </div>
        <Button variant="outline" onClick={cerrar} disabled={enviando} className="gap-1.5">
          <Lock className="size-4" />{enviando ? 'Cerrando…' : 'Cerrar caja'}
        </Button>
      </div>
      {dif !== null && (
        <p className="text-[12px] tabular-nums">
          Esperado <span className="font-semibold">{cop(esperado)}</span> · diferencia{' '}
          <span className={dif < 0 ? 'text-danger font-semibold' : dif > 0 ? 'text-success font-semibold' : 'font-semibold'}>
            {cop(dif)}
          </span>
          {dif < 0 ? ' (faltante)' : dif > 0 ? ' (sobrante)' : ' (cuadra)'}
        </p>
      )}
    </div>
  )
}

// ── Ingresos por método (del resumen del día) ─────────────────────────────────
function IngresosPorMetodo({ porMetodo }) {
  const entradas = Object.entries(porMetodo || {})
    .map(([nombre, monto]) => ({ nombre: cap(nombre), monto: num(monto) }))
    .sort((a, b) => b.monto - a.monto)
  const total = entradas.reduce((a, m) => a + m.monto, 0)
  return (
    <Card className="p-3.5">
      <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5 mb-2.5">
        <ArrowRightLeft className="size-3.5" /> Ingresos por método · Hoy
      </h2>
      {entradas.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">Sin ventas registradas hoy.</p>
      ) : (
        <>
          <ul className="space-y-1.5">
            {entradas.map(m => {
              const pct = total > 0 ? Math.round((m.monto / total) * 100) : 0
              return (
                <li key={m.nombre} className="text-[13px]">
                  <div className="flex items-baseline justify-between gap-2">
                    <span>{m.nombre}</span>
                    <span className="tabular-nums font-medium">{cop(m.monto)}</span>
                  </div>
                  <div className="mt-1 h-1.5 rounded-full bg-surface-2 overflow-hidden">
                    <div className="h-full rounded-full bg-primary/70" style={{ width: `${pct}%` }} />
                  </div>
                </li>
              )
            })}
          </ul>
          <div className="mt-2.5 pt-2 border-t border-border-subtle flex items-baseline justify-between">
            <span className="text-[11px] uppercase tracking-wider text-muted-foreground font-semibold">Total</span>
            <span className="tabular-nums font-semibold text-success">{cop(total)}</span>
          </div>
        </>
      )}
    </Card>
  )
}

// ── Cuadre de efectivo esperado (componentes del arqueo en vivo) ───────────────
function CuadreEfectivo({ arqueo, abierta, totalGastos, construccion = false }) {
  if (!abierta) {
    return (
      <Card className="p-3.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5 mb-2.5">
          <Coins className="size-3.5" /> Cuadre de efectivo
        </h2>
        <p className="py-6 text-center text-sm text-muted-foreground">Abre la caja para ver el cuadre del día.</p>
      </Card>
    )
  }
  // Construcción (caja menor): sin ventas de mostrador, la fila "+ Ventas en efectivo" es siempre $0 → se
  // omite. El resto del arqueo (apertura, ingresos manuales, egresos) es idéntico.
  const filas = [
    { label: 'Apertura', val: num(arqueo.saldo_inicial), signo: '' },
    ...(construccion ? [] : [{ label: '+ Ventas en efectivo', val: num(arqueo.ventas_efectivo), signo: '+' }]),
    ...(num(arqueo.ingresos) > 0 ? [{ label: '+ Ingresos manuales', val: num(arqueo.ingresos), signo: '+' }] : []),
    { label: '− Egresos (gastos)', val: num(arqueo.egresos), signo: '-' },
  ]
  return (
    <Card className="p-3.5">
      <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5 mb-2.5">
        <Coins className="size-3.5" /> Cuadre de efectivo
      </h2>
      <dl className="space-y-1.5 text-[13px]">
        {filas.map(f => (
          <div key={f.label} className="flex items-baseline justify-between gap-2">
            <dt className="text-muted-foreground">{f.label}</dt>
            <dd className={`tabular-nums ${f.signo === '-' ? 'text-danger' : ''}`}>
              {f.signo === '-' ? `− ${cop(f.val)}` : cop(f.val)}
            </dd>
          </div>
        ))}
      </dl>
      <div className="mt-2.5 pt-2 border-t border-border-subtle flex items-baseline justify-between">
        <span className="text-[13px] font-semibold">= Efectivo esperado</span>
        <span className="tabular-nums font-semibold text-primary">{cop(num(arqueo.saldo_esperado))}</span>
      </div>
    </Card>
  )
}

// ── Movimiento manual de caja ─────────────────────────────────────────────────
function MovimientoForm({ onDone }) {
  const [tipo, setTipo] = useState('ingreso')
  const [monto, setMonto] = useState('')
  const [concepto, setConcepto] = useState('')
  const [enviando, setEnviando] = useState(false)

  async function registrar() {
    const n = Number(monto)
    if (!n || n <= 0) { toast.error('Indica el monto'); return }
    setEnviando(true)
    try {
      const res = await api('/caja/movimiento', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': nuevaKey() },
        body: JSON.stringify({ tipo, monto: n, concepto: concepto.trim() || null }),
      })
      if (res.ok) { toast.success('Movimiento registrado'); setMonto(''); setConcepto(''); onDone() }
      else toast.error('No se pudo registrar el movimiento')
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5 mb-2.5">
        <ArrowRightLeft className="size-3.5" /> Movimiento de caja
      </h2>
      <div className="flex flex-wrap items-center gap-2">
        <select value={tipo} onChange={(e) => setTipo(e.target.value)} aria-label="Tipo de movimiento"
          className="h-9 px-2 rounded-md border border-border bg-surface text-sm">
          <option value="ingreso">Ingreso</option>
          <option value="egreso">Egreso</option>
        </select>
        <Input type="number" min="0" step="any" value={monto} onChange={(e) => setMonto(e.target.value)}
          placeholder="Monto" aria-label="Monto" className="w-32 h-9" />
        <Input value={concepto} onChange={(e) => setConcepto(e.target.value)}
          placeholder="Concepto (opcional)" aria-label="Concepto" className="flex-1 min-w-[120px] h-9" />
        <Button variant="outline" onClick={registrar} disabled={enviando}>Registrar</Button>
      </div>
    </Card>
  )
}

// ── Gastos del día ────────────────────────────────────────────────────────────
function GastosDelDia({ gastos, total }) {
  return (
    <Card className="p-3.5">
      <div className="flex items-center justify-between mb-2.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
          <Receipt className="size-3.5" /> Gastos del día ({gastos.length})
        </h2>
        {total > 0 && <span className="text-[12px] tabular-nums font-semibold text-danger">{cop(total)}</span>}
      </div>
      {gastos.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">Sin gastos registrados hoy.</p>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {gastos.map(g => (
            <li key={g.id} className="py-2 flex items-center gap-2.5 text-[13px]">
              <span className="inline-flex items-center h-5 px-1.5 rounded bg-surface-2 text-[10px] uppercase tracking-wide text-muted-foreground capitalize shrink-0">
                {g.categoria}
              </span>
              <span className="flex-1 min-w-0 truncate text-muted-foreground">{g.concepto || '—'}</span>
              <span className="tabular-nums font-medium shrink-0">{cop(num(g.monto))}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
