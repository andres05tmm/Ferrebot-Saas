/*
 * TabEstadosFinancieros — los estados del ledger doble partida (ADR 0030), SOLO admin.
 * Cuatro vistas sobre /contabilidad/*: balance de comprobación, estado de resultados, balance
 * general y flujo de efectivo. Selector de periodo (default mes en curso). Gate `contabilidad_ledger`.
 * Live: re-fetch ante venta/gasto/devolución/factura (los eventos que proyectan asientos).
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Scale, TrendingUp, Landmark, Droplets, CheckCircle2, AlertTriangle } from 'lucide-react'
import { useFetch, cop, mesActualCO } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'

const VISTAS = [
  { key: 'balance-comprobacion', label: 'Comprobación', icon: Scale, rango: true },
  { key: 'estado-resultados', label: 'Resultados', icon: TrendingUp, rango: true },
  { key: 'balance-general', label: 'Balance general', icon: Landmark, rango: false },
  { key: 'flujo-efectivo', label: 'Flujo de efectivo', icon: Droplets, rango: true },
]

export default function TabEstadosFinancieros() {
  const { isAdmin } = useAuth()
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-body-sm text-muted-foreground">
        Los estados financieros son solo para administradores.
      </Card>
    )
  }
  return <Contenido />
}

function Contenido() {
  const { refreshKey } = useOutletContext() ?? {}
  const [vista, setVista] = useState('balance-comprobacion')
  const [rango, setRango] = useState(mesActualCO())
  const setCampo = (k) => (e) => setRango((p) => ({ ...p, [k]: e.target.value }))

  const def = VISTAS.find((v) => v.key === vista)
  const params = def.key === 'balance-general'
    ? `fin=${rango.hasta}`
    : `inicio=${rango.desde}&fin=${rango.hasta}`
  const q = useFetch(`/contabilidad/${def.key}?${params}`, [refreshKey, vista, rango.desde, rango.hasta])
  useRealtimeEvent(
    ['venta_registrada', 'venta_anulada', 'gasto_registrado', 'devolucion_registrada',
     'factura_emitida', 'reconnected'],
    q.refetch,
  )

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-end gap-3">
          <h1 className="text-lg font-semibold tracking-tight mr-auto">Estados financieros</h1>
          {def.key !== 'balance-general' && (
            <label className="text-caption text-muted-foreground">
              Desde
              <Input type="date" value={rango.desde} onChange={setCampo('desde')} aria-label="Desde" className="h-9 mt-1" />
            </label>
          )}
          <label className="text-caption text-muted-foreground">
            {def.key === 'balance-general' ? 'A la fecha' : 'Hasta'}
            <Input type="date" value={rango.hasta} onChange={setCampo('hasta')} aria-label="Hasta" className="h-9 mt-1" />
          </label>
        </div>
        <div className="flex flex-wrap gap-1.5 mt-3">
          {VISTAS.map((v) => (
            <Button key={v.key} size="sm" variant={v.key === vista ? 'default' : 'outline'}
              onClick={() => setVista(v.key)} className="gap-1.5">
              <v.icon className="size-3.5" /> {v.label}
            </Button>
          ))}
        </div>
      </Card>

      {q.loading && <Card className="p-8 text-center text-body-sm text-muted-foreground">Cargando…</Card>}
      {q.error && (
        <Card className="p-8 text-center text-body-sm text-destructive">
          No se pudo cargar. Revisa que la contabilidad esté activa y el PUC sembrado.
        </Card>
      )}
      {!q.loading && !q.error && q.data && (
        <VistaContenido vista={def.key} data={q.data} />
      )}
    </div>
  )
}

function VistaContenido({ vista, data }) {
  if (vista === 'balance-comprobacion') return <Comprobacion d={data} />
  if (vista === 'estado-resultados') return <Resultados d={data} />
  if (vista === 'balance-general') return <BalanceGeneral d={data} />
  return <Flujo d={data} />
}

// --- Balance de comprobación ------------------------------------------------
function Comprobacion({ d }) {
  return (
    <Card className="overflow-hidden">
      <Cuadre cuadra={d.cuadra} izq={d.total_debitos} der={d.total_creditos}
        etIzq="Débitos" etDer="Créditos" />
      <div className="overflow-x-auto">
        <table className="w-full text-body-sm">
          <thead>
            <tr className="border-b text-caption uppercase tracking-wider text-muted-foreground">
              <Th>Código</Th><Th>Cuenta</Th><Th right>Débitos</Th><Th right>Créditos</Th><Th right>Saldo</Th>
            </tr>
          </thead>
          <tbody>
            {(d.filas || []).map((f) => (
              <tr key={f.codigo} className="border-b border-border/50 last:border-0">
                <Td mono>{f.codigo}</Td><Td>{f.nombre}</Td>
                <Td right>{cop(f.debitos)}</Td><Td right>{cop(f.creditos)}</Td>
                <Td right bold>{cop(f.saldo)}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

// --- Estado de resultados ---------------------------------------------------
function Resultados({ d }) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Metric label="Ingresos" value={cop(d.total_ingresos)} tone="text-success" />
        <Metric label="Costos" value={cop(d.total_costos)} tone="text-warning" />
        <Metric label="Gastos" value={cop(d.total_gastos)} tone="text-warning" />
        <Metric label="Utilidad" value={cop(d.utilidad)} hero
          tone={Number(d.utilidad) >= 0 ? 'text-success' : 'text-destructive'} />
      </div>
      <Seccion titulo="Ingresos" filas={d.ingresos} />
      <Seccion titulo="Costos" filas={d.costos} />
      <Seccion titulo="Gastos" filas={d.gastos} />
    </div>
  )
}

// --- Balance general --------------------------------------------------------
function BalanceGeneral({ d }) {
  return (
    <div className="space-y-3">
      <Cuadre cuadra={d.cuadra} izq={d.total_activos} der={Number(d.total_pasivos) + Number(d.total_patrimonio)}
        etIzq="Activos" etDer="Pasivo + Patrimonio" card />
      <div className="grid md:grid-cols-2 gap-3">
        <Seccion titulo="Activos" filas={d.activos} total={d.total_activos} />
        <div className="space-y-3">
          <Seccion titulo="Pasivos" filas={d.pasivos} total={d.total_pasivos} />
          <Seccion titulo="Patrimonio" filas={d.patrimonio} total={d.total_patrimonio} />
        </div>
      </div>
      <p className="text-caption text-muted-foreground px-1">
        Utilidad del ejercicio: <span className="tabular font-semibold">{cop(d.utilidad_ejercicio)}</span>
      </p>
    </div>
  )
}

// --- Flujo de efectivo ------------------------------------------------------
function Flujo({ d }) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Metric label="Entradas" value={cop(d.total_entradas)} tone="text-success" />
        <Metric label="Salidas" value={cop(d.total_salidas)} tone="text-warning" />
        <Metric label="Flujo neto" value={cop(d.flujo_neto)} hero
          tone={Number(d.flujo_neto) >= 0 ? 'text-success' : 'text-destructive'} />
      </div>
      <Seccion titulo="Entradas" filas={d.entradas} />
      <Seccion titulo="Salidas" filas={d.salidas} />
    </div>
  )
}

// --- Piezas compartidas -----------------------------------------------------
function Cuadre({ cuadra, izq, der, etIzq, etDer, card }) {
  const Icon = cuadra ? CheckCircle2 : AlertTriangle
  const tono = cuadra ? 'text-success' : 'text-destructive'
  const cuerpo = (
    <div className="flex items-center gap-2 text-body-sm">
      <Icon className={`size-4 ${tono}`} />
      <span className={`font-semibold ${tono}`}>{cuadra ? 'Cuadra' : 'No cuadra'}</span>
      <span className="text-muted-foreground ml-auto tabular">
        {etIzq} {cop(izq)} · {etDer} {cop(der)}
      </span>
    </div>
  )
  return card ? <Card className="p-3">{cuerpo}</Card> : <div className="p-3 border-b bg-muted/30">{cuerpo}</div>
}

function Seccion({ titulo, filas, total }) {
  return (
    <Card className="overflow-hidden">
      <div className="px-3.5 py-2 border-b flex items-center">
        <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground">{titulo}</h2>
        {total != null && <span className="ml-auto tabular text-body-sm font-semibold">{cop(total)}</span>}
      </div>
      {(!filas || filas.length === 0)
        ? <p className="px-3.5 py-3 text-caption text-muted-foreground">Sin movimientos.</p>
        : (
          <table className="w-full text-body-sm">
            <tbody>
              {filas.map((f) => (
                <tr key={f.codigo} className="border-b border-border/50 last:border-0">
                  <Td mono>{f.codigo}</Td><Td>{f.nombre}</Td><Td right bold>{cop(f.valor)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
    </Card>
  )
}

function Metric({ label, value, tone, hero }) {
  return (
    <Card className="p-3.5">
      <div className="text-caption font-semibold uppercase tracking-wider text-muted-foreground mb-1.5">{label}</div>
      <div className={`tabular font-semibold ${hero ? 'text-xl' : 'text-[15px]'} ${tone}`}>{value}</div>
    </Card>
  )
}

const Th = ({ children, right }) => (
  <th className={`px-3 py-2 font-semibold ${right ? 'text-right' : 'text-left'}`}>{children}</th>
)
const Td = ({ children, right, bold, mono }) => (
  <td className={`px-3 py-2 ${right ? 'text-right tabular' : ''} ${bold ? 'font-semibold' : ''} ${mono ? 'font-mono text-caption text-muted-foreground' : ''}`}>{children}</td>
)
