/*
 * TabCaja — flujo de caja del día. Gateada por la feature fina 'caja'.
 *
 * Panel profesional: KPIs (apertura / ventas / gastos / efectivo esperado), estado de la caja
 * (abrir o cerrar con arqueo), cuadre de efectivo EN VIVO (GET /caja/arqueo — misma fórmula que el
 * cierre, fuente única), ingresos por método (GET /reportes/resumen), movimientos del turno
 * (GET /caja/movimientos + POST /caja/movimiento) y gastos del día (GET /gastos).
 * Live: caja/venta/gasto/reconnected.
 *
 * Layout en dos bandas para no dejar cards anchas y vacías en escritorio: banda 1 = estado + cuadre +
 * ingresos por método (3 columnas), banda 2 = movimientos del turno + gastos del día (2 columnas).
 * En móvil todo cae a una columna.
 *
 * Familia construcción (esConstruccion): la caja es CAJA MENOR de campo, no un mostrador. Una obra no
 * vende tickets, así que se ocultan "Ventas hoy" y los "ingresos por método" (siempre $0), y el cuadre
 * pierde la fila "+ Ventas en efectivo". Apertura/cierre, movimientos manuales y gastos quedan intactos.
 * Para retail (Punto Rojo, demos) TODO queda idéntico.
 */
import { useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import {
  Wallet, Lock, LockOpen, TrendingUp, TrendingDown, Coins, Receipt, ArrowRightLeft,
  ArrowDownLeft, ArrowUpRight, Clock,
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
const hora = (iso) => (iso ? new Date(iso).toLocaleTimeString('es-CO', HORA_CO) : '')
function nuevaKey() { return crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random()}` }

/** "3h 20m" desde la apertura — cuánto lleva abierto el turno (sin timer: se recalcula en cada render). */
function transcurrido(iso) {
  const ms = Date.now() - new Date(iso || 0).getTime()
  if (!Number.isFinite(ms) || ms < 0) return null
  const h = Math.floor(ms / 3600000)
  const m = Math.floor((ms % 3600000) / 60000)
  return h ? `${h}h ${m}m` : `${m}m`
}

/** Card de sección con encabezado uniforme (mismo alto de título en toda la pantalla). */
function Seccion({ icon: Icon, titulo, extra, children, className = '' }) {
  return (
    <Card className={`p-3.5 flex flex-col ${className}`}>
      <div className="flex items-center justify-between gap-2 mb-2.5 min-h-5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
          <Icon className="size-3.5" /> {titulo}
        </h2>
        {extra}
      </div>
      {children}
    </Card>
  )
}

const Vacio = ({ children }) => (
  <p className="flex-1 grid place-items-center py-6 text-center text-sm text-muted-foreground">{children}</p>
)

export default function TabCaja() {
  const { refreshKey } = useOutletContext() ?? {}
  const construccion = esConstruccion(useFeatures())
  const hoy = rangoHoyCO()

  const arqueoQ = useFetch('/caja/arqueo', [refreshKey])
  const resumenQ = useFetch('/reportes/resumen', [refreshKey])
  const gastosQ = useFetch(`/gastos?desde=${hoy.desde}&hasta=${hoy.hasta}`, [refreshKey])
  const movsQ = useFetch('/caja/movimientos', [refreshKey])

  const recargar = () => { arqueoQ.refetch(); resumenQ.refetch(); gastosQ.refetch(); movsQ.refetch() }
  useRealtimeEvent(EVENTOS, recargar)

  const arqueo = arqueoQ.data || {}
  const abierta = arqueo.estado === 'abierta'
  const resumen = resumenQ.data || {}
  const gastos = Array.isArray(gastosQ.data) ? gastosQ.data : []
  const totalGastos = gastos.reduce((a, g) => a + num(g.monto), 0)
  // Los gastos postean SU egreso en caja_movimientos (referencia 'gasto:<id>'): se excluyen del ledger
  // manual para no repetir la misma plata en dos cards de la pantalla.
  const movimientos = (Array.isArray(movsQ.data) ? movsQ.data : [])
    .filter(m => !String(m.referencia || '').startsWith('gasto:'))

  const apertura = num(arqueo.saldo_inicial)
  const ventasHoy = num(resumen.total_vendido)
  const esperado = num(arqueo.saldo_esperado)
  const ticket = num(resumen.ticket_promedio)

  if (arqueoQ.loading && !arqueoQ.data) {
    return <Card className="p-8 text-center text-sm text-muted-foreground">Cargando caja…</Card>
  }

  return (
    <div className="space-y-3">
      {/* KPIs — misma tarjeta hero del tab Hoy (ícono sólido + cifra grande en una sola fila, sin banda
          de color arriba) y el mismo gap-3, para que las dos portadas del POS se lean igual.
          Construcción (caja menor) no muestra "Ventas hoy": su caja no registra ventas de mostrador; la
          grilla pasa de 4 a 3 columnas para no dejar un hueco. */}
      <div className={`grid grid-cols-1 sm:grid-cols-2 gap-3 ${construccion ? 'lg:grid-cols-3' : 'lg:grid-cols-4'}`}>
        <KpiCard tone="muted" icon={Wallet} label="Apertura" iconStyle="filled" heroValue
          value={cop(apertura)}
          sub={abierta && arqueo.fecha_apertura ? `Desde las ${hora(arqueo.fecha_apertura)}` : 'Base inicial de la caja'} />
        {!construccion && (
          <KpiCard tone="success" icon={TrendingUp} label="Ventas hoy" iconStyle="filled" heroValue
            value={cop(ventasHoy)}
            sub={`${resumen.num_ventas ?? 0} ventas · ticket ${cop(ticket)}`} />
        )}
        <KpiCard tone="danger" icon={TrendingDown} label="Gastos" iconStyle="filled" heroValue
          value={cop(totalGastos)} sub={`${gastos.length} egresos del día`} />
        {/* En construcción el efectivo esperado es el KPI clave de la caja menor: ocupa fila completa en
            móvil (sm:col-span-2) para que la grilla de 3 no deje una celda vacía abajo. */}
        <div className={construccion ? 'sm:col-span-2 lg:col-span-1' : 'contents'}>
          <KpiCard tone="primary" icon={Coins} label="Efectivo esperado" iconStyle="filled" heroValue coloredValue
            value={cop(esperado)}
            sub={construccion ? 'Apertura + movimientos − gastos' : 'Apertura + ventas efectivo − gastos'} />
        </div>
      </div>

      {/* Banda 1: estado + cuadre (+ ingresos por método solo en retail: la caja menor de obra no
          tiene ventas por método). */}
      <div className={`grid gap-3 ${construccion ? 'lg:grid-cols-2' : 'lg:grid-cols-3'}`}>
        <EstadoCaja arqueo={arqueo} abierta={abierta} esperado={esperado} onDone={recargar} construccion={construccion} />
        <CuadreEfectivo arqueo={arqueo} abierta={abierta} construccion={construccion} />
        {!construccion && <IngresosPorMetodo porMetodo={resumen.por_metodo_pago} />}
      </div>

      {/* Banda 2: el detalle del día, lado a lado */}
      <div className="grid gap-3 lg:grid-cols-2">
        <MovimientosTurno movimientos={movimientos} abierta={abierta} onDone={recargar} />
        <GastosDelDia gastos={gastos} total={totalGastos} />
      </div>
    </div>
  )
}

// ── Estado de la caja ─────────────────────────────────────────────────────────
function EstadoCaja({ arqueo, abierta, esperado, onDone, construccion = false }) {
  const Icono = abierta ? LockOpen : Lock
  const tono = abierta ? 'text-success bg-success/15' : 'text-muted-foreground bg-surface-2'
  const desde = arqueo.fecha_apertura ? hora(arqueo.fecha_apertura) : null
  const llevaAbierta = abierta && arqueo.fecha_apertura ? transcurrido(arqueo.fecha_apertura) : null
  return (
    <Card className="p-3.5 flex flex-col">
      <div className="flex items-center gap-2.5">
        <span className={`grid place-items-center rounded-lg size-9 shrink-0 ${tono}`}>
          <Icono className="size-4.5" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold">{abierta ? 'Caja abierta' : 'Caja cerrada'}</p>
          <p className="text-[11px] text-muted-foreground">
            {abierta && desde
              ? `Desde las ${desde} · base ${cop(num(arqueo.saldo_inicial))}`
              : 'Registra el saldo inicial para empezar el día.'}
          </p>
        </div>
        {llevaAbierta && (
          <span className="ml-auto shrink-0 inline-flex items-center gap-1 h-5 px-1.5 rounded bg-surface-2 text-[10px] text-muted-foreground tabular-nums">
            <Clock className="size-3" />{llevaAbierta}
          </span>
        )}
      </div>
      <div className="mt-3">
        {abierta ? <CierreForm esperado={esperado} onDone={onDone} construccion={construccion} /> : <AperturaForm onDone={onDone} />}
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
      <Button onClick={abrir} disabled={enviando} className="gap-1.5 shrink-0">
        <LockOpen className="size-4" />{enviando ? 'Abriendo…' : 'Abrir caja'}
      </Button>
    </div>
  )
}

function CierreForm({ esperado, onDone, construccion = false }) {
  const [contado, setContado] = useState('')
  const [enviando, setEnviando] = useState(false)
  const dif = contado === '' ? null : Number(contado) - esperado   // contado − esperado
  // Key estable mientras el contado no cambie: reintentar el sobrante tras timeout es replay, no
  // una segunda venta ficticia (F2.7).
  const idemKeySobrante = useMemo(() => nuevaKey(), [contado])

  async function cerrarCaja(n) {
    const res = await api('/caja/cierre', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ saldo_contado: n }),
    })
    if (!res.ok) { toast.error('No se pudo cerrar la caja'); return false }
    const data = await res.json()
    toast.success(`Caja cerrada · diferencia ${cop(Number(data.diferencia ?? 0))}`)
    setContado(''); onDone()
    return true
  }

  async function cerrar() {
    const n = Number(contado)
    if (Number.isNaN(n) || n < 0) { toast.error('Indica el saldo contado'); return }
    setEnviando(true)
    try { await cerrarCaja(n) }
    catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  // Cuadre en un clic (F5): con SOBRANTE, la plata de más se registra como venta varia en efectivo
  // ("Sobrante cierre de caja") y se cierra — el esperado sube hasta el contado y el cierre queda en
  // $0 de diferencia, con el dinero contabilizado como ingreso real (no como descuadre). Con
  // faltante no hay atajo: la diferencia negativa se persiste tal cual en el cierre normal.
  async function registrarSobranteYCerrar() {
    const n = Number(contado)
    if (dif == null || dif <= 0) return
    setEnviando(true)
    try {
      const res = await api('/ventas', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idemKeySobrante },
        body: JSON.stringify({
          metodo_pago: 'efectivo', origen: 'web',
          lineas: [{ descripcion: 'Sobrante cierre de caja', cantidad: 1, precio_unitario: dif }],
        }),
      })
      if (!res.ok) { toast.error('No se pudo registrar el sobrante como venta'); return }
      await cerrarCaja(n)
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
        <Button variant="outline" onClick={cerrar} disabled={enviando} className="gap-1.5 shrink-0">
          <Lock className="size-4" />{enviando ? 'Cerrando…' : 'Cerrar caja'}
        </Button>
      </div>
      {dif !== null && (
        <div className={`rounded-md px-2.5 py-2 text-[12px] tabular-nums ${
          dif < 0 ? 'bg-danger/10' : dif > 0 ? 'bg-success/10' : 'bg-surface-2'}`}>
          Esperado <span className="font-semibold">{cop(esperado)}</span> · diferencia{' '}
          <span className={dif < 0 ? 'text-danger font-semibold' : dif > 0 ? 'text-success font-semibold' : 'font-semibold'}>
            {cop(dif)}
          </span>
          {dif < 0 ? ' (faltante)' : dif > 0 ? ' (sobrante)' : ' (cuadra)'}
        </div>
      )}
      {/* Solo retail (F2.7): una constructora no vende por mostrador — convertir el sobrante de la
          caja menor en una "venta" fabricaría ingresos ficticios; su descuadre se persiste tal cual. */}
      {!construccion && dif !== null && dif > 0 && (
        <Button onClick={registrarSobranteYCerrar} disabled={enviando} className="w-full gap-1.5">
          <Coins className="size-4" />
          {enviando ? 'Cerrando…' : `Registrar sobrante (${cop(dif)}) como venta y cerrar`}
        </Button>
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
    <Seccion icon={ArrowRightLeft} titulo="Ingresos por método · Hoy"
      extra={total > 0 && <span className="text-[12px] tabular-nums font-semibold text-success">{cop(total)}</span>}>
      {entradas.length === 0 ? (
        <Vacio>Sin ventas registradas hoy.</Vacio>
      ) : (
        <ul className="space-y-2">
          {entradas.map(m => {
            const pct = total > 0 ? Math.round((m.monto / total) * 100) : 0
            return (
              <li key={m.nombre} className="text-[13px]">
                <div className="flex items-baseline justify-between gap-2">
                  <span>{m.nombre}</span>
                  <span className="tabular-nums font-medium">
                    {cop(m.monto)} <span className="text-[11px] text-muted-foreground">{pct}%</span>
                  </span>
                </div>
                <div className="mt-1 h-1.5 rounded-full bg-surface-2 overflow-hidden">
                  <div className="h-full rounded-full bg-primary/70" style={{ width: `${pct}%` }} />
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </Seccion>
  )
}

// ── Cuadre de efectivo esperado (componentes del arqueo en vivo) ───────────────
function CuadreEfectivo({ arqueo, abierta, construccion = false }) {
  if (!abierta) {
    return (
      <Seccion icon={Coins} titulo="Cuadre de efectivo">
        <Vacio>Abre la caja para ver el cuadre del día.</Vacio>
      </Seccion>
    )
  }
  // Construcción (caja menor): la fila "+ Ventas en efectivo" se omite SOLO cuando de verdad es $0 —
  // el backend SÍ la suma en saldo_esperado (F2.7: si el bot u otra vía registró una venta, ocultarla
  // dejaba componentes que no sumaban el total). El resto del arqueo es idéntico.
  const filas = [
    { label: 'Apertura', val: num(arqueo.saldo_inicial), signo: '' },
    ...(construccion && num(arqueo.ventas_efectivo) === 0
      ? []
      : [{ label: '+ Ventas en efectivo', val: num(arqueo.ventas_efectivo), signo: '+' }]),
    ...(num(arqueo.ingresos) > 0 ? [{ label: '+ Ingresos manuales', val: num(arqueo.ingresos), signo: '+' }] : []),
    { label: '− Egresos (gastos)', val: num(arqueo.egresos), signo: '-' },
  ]
  return (
    <Seccion icon={Coins} titulo="Cuadre de efectivo">
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
      <div className="mt-auto pt-2.5 border-t border-border-subtle flex items-baseline justify-between">
        <span className="text-[13px] font-semibold">= Efectivo esperado</span>
        <span className="tabular-nums font-semibold text-primary text-base">{cop(num(arqueo.saldo_esperado))}</span>
      </div>
    </Seccion>
  )
}

// ── Movimientos manuales del turno (ledger + alta) ────────────────────────────
function MovimientosTurno({ movimientos, abierta, onDone }) {
  const ingresos = movimientos.filter(m => m.tipo === 'ingreso').reduce((a, m) => a + num(m.monto), 0)
  const egresos = movimientos.filter(m => m.tipo === 'egreso').reduce((a, m) => a + num(m.monto), 0)
  return (
    <Seccion icon={ArrowRightLeft} titulo={`Movimientos del turno (${movimientos.length})`}
      extra={movimientos.length > 0 && (
        <span className="text-[11px] tabular-nums text-muted-foreground">
          <span className="text-success font-semibold">+{cop(ingresos)}</span>
          {' · '}
          <span className="text-danger font-semibold">−{cop(egresos)}</span>
        </span>
      )}>
      {abierta ? <MovimientoForm onDone={onDone} /> : null}
      {movimientos.length === 0 ? (
        <Vacio>{abierta ? 'Sin movimientos manuales en este turno.' : 'Abre la caja para registrar movimientos.'}</Vacio>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {movimientos.map(m => {
            const ingreso = m.tipo === 'ingreso'
            const Flecha = ingreso ? ArrowDownLeft : ArrowUpRight
            return (
              <li key={m.id} className="py-2 flex items-center gap-2.5 text-[13px]">
                <span className={`grid place-items-center size-6 rounded shrink-0 ${
                  ingreso ? 'bg-success/15 text-success' : 'bg-danger/15 text-danger'}`}>
                  <Flecha className="size-3.5" />
                </span>
                <span className="flex-1 min-w-0 truncate text-muted-foreground">{m.concepto || (ingreso ? 'Ingreso' : 'Egreso')}</span>
                <span className="text-[11px] text-muted-foreground tabular-nums shrink-0">{hora(m.creado_en)}</span>
                <span className={`tabular-nums font-medium shrink-0 w-24 text-right ${ingreso ? 'text-success' : 'text-danger'}`}>
                  {ingreso ? '+' : '−'} {cop(num(m.monto))}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </Seccion>
  )
}

function MovimientoForm({ onDone }) {
  const [tipo, setTipo] = useState('ingreso')
  const [monto, setMonto] = useState('')
  const [concepto, setConcepto] = useState('')
  const [enviando, setEnviando] = useState(false)
  // Key estable mientras el payload no cambie: el reintento tras timeout es replay, no doble movimiento.
  const idemKey = useMemo(() => nuevaKey(), [tipo, monto, concepto])

  async function registrar() {
    const n = Number(monto)
    if (!n || n <= 0) { toast.error('Indica el monto'); return }
    setEnviando(true)
    try {
      const res = await api('/caja/movimiento', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idemKey },
        body: JSON.stringify({ tipo, monto: n, concepto: concepto.trim() || null }),
      })
      if (res.ok) { toast.success('Movimiento registrado'); setMonto(''); setConcepto(''); onDone() }
      else toast.error('No se pudo registrar el movimiento')
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 pb-2.5 mb-1 border-b border-border-subtle">
      <select value={tipo} onChange={(e) => setTipo(e.target.value)} aria-label="Tipo de movimiento"
        className="h-9 px-2 rounded-md border border-border bg-surface text-sm">
        <option value="ingreso">Ingreso</option>
        <option value="egreso">Egreso</option>
      </select>
      <Input type="number" min="0" step="any" value={monto} onChange={(e) => setMonto(e.target.value)}
        placeholder="Monto" aria-label="Monto" className="w-28 h-9" />
      <Input value={concepto} onChange={(e) => setConcepto(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') registrar() }}
        placeholder="Concepto (opcional)" aria-label="Concepto" className="flex-1 min-w-[120px] h-9" />
      <Button variant="outline" onClick={registrar} disabled={enviando}>Registrar</Button>
    </div>
  )
}

// ── Gastos del día ────────────────────────────────────────────────────────────
function GastosDelDia({ gastos, total }) {
  // Desglose por categoría: dónde se fue la plata del día de un vistazo (el listado ya está abajo).
  const porCategoria = Object.entries(
    gastos.reduce((acc, g) => ({ ...acc, [g.categoria]: (acc[g.categoria] || 0) + num(g.monto) }), {}),
  ).sort((a, b) => b[1] - a[1])

  return (
    <Seccion icon={Receipt} titulo={`Gastos del día (${gastos.length})`}
      extra={total > 0 && <span className="text-[12px] tabular-nums font-semibold text-danger">{cop(total)}</span>}>
      {gastos.length === 0 ? (
        <Vacio>Sin gastos registrados hoy.</Vacio>
      ) : (
        <>
          <div className="flex flex-wrap gap-1.5 pb-2.5 mb-1 border-b border-border-subtle">
            {porCategoria.map(([categoria, monto]) => (
              <span key={categoria}
                className="inline-flex items-center gap-1.5 h-6 px-2 rounded-full bg-surface-2 text-[11px] capitalize">
                {categoria}
                <span className="tabular-nums font-semibold text-danger">{cop(monto)}</span>
              </span>
            ))}
          </div>
          <ul className="divide-y divide-border-subtle">
            {gastos.map(g => (
              <li key={g.id} className="py-2 flex items-center gap-2.5 text-[13px]">
                <span className="inline-flex items-center h-5 px-1.5 rounded bg-surface-2 text-[10px] uppercase tracking-wide text-muted-foreground capitalize shrink-0">
                  {g.categoria}
                </span>
                <span className="flex-1 min-w-0 truncate text-muted-foreground">{g.concepto || '—'}</span>
                <span className="text-[11px] text-muted-foreground tabular-nums shrink-0">{hora(g.creado_en)}</span>
                <span className="tabular-nums font-medium shrink-0 w-24 text-right">{cop(num(g.monto))}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Seccion>
  )
}
