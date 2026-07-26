/*
 * TabGastos — a dónde se va la plata de la ferretería (0071).
 *
 * Antes era la lista de los gastos de HOY y nada más. Ahora responde las tres preguntas que un dueño
 * necesita y ningún cuaderno le da:
 *   1. ¿Cuánto me estoy gastando y en qué? — desglose por categoría, separando FIJOS (se pagan
 *      vendas o no) de variables, y contra el período anterior.
 *   2. ¿Cuánto tengo que vender para no perder? — punto de equilibrio = gastos fijos ÷ margen bruto.
 *   3. ¿Qué me falta por pagar este mes? — el checklist de arriendo, luz, nómina…
 *
 * Y separa lo que NO es gasto aunque salga de la caja: el retiro del dueño reparte utilidad y una
 * vitrina es un activo. Contarlos como gasto haría ver pérdidas que no existen.
 *
 * El resumen es admin-only (habla de márgenes y utilidad); el vendedor ve registrar y la lista.
 * Live: gasto_registrado / gasto_rechazado / reconnected.
 */
import { useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import {
  Percent, Plus, Receipt, Scale, TrendingDown, TrendingUp, Wallet,
} from '@/lib/icons.jsx'
import { api } from '@/lib/api'
import { useFetch, cop, EstadoVacio, SkeletonFilas } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Label } from '@/components/ui/label.jsx'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'
import ModalGastoRapido from '@/components/ModalGastoRapido.jsx'
import BandejaRevision from './construccion/BandejaRevision.jsx'
import PanelRecurrentes from './gastos/PanelRecurrentes.jsx'
import {
  CATEGORIAS_GASTO, CATEGORIA_LABEL, PERIODOS, TIPO_LABEL, periodo, rangoISO,
} from '@/lib/gastos.js'

const SELECT = 'h-9 rounded-md border border-input bg-surface px-2 text-body-sm text-foreground'
const hoyCO = () => new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })

export default function TabGastos() {
  const { refreshKey } = useOutletContext() ?? {}
  const { isAdmin } = useAuth()
  const admin = isAdmin()
  const [periodoId, setPeriodoId] = useState('mes')
  const [filtroCat, setFiltroCat] = useState('')
  const [filtroTipo, setFiltroTipo] = useState('')
  const [modalAbierto, setModalAbierto] = useState(false)
  const [anulando, setAnulando] = useState(null)

  const hoy = useMemo(hoyCO, [])
  const rango = useMemo(() => periodo(periodoId, hoy), [periodoId, hoy])
  const mesActual = useMemo(() => periodo('mes', hoy), [hoy])
  const iso = rangoISO(rango)

  const clave = [refreshKey, rango.desde, rango.hasta]
  const resumenQ = useFetch(
    admin ? `/reportes/gastos?desde=${rango.desde}&hasta=${rango.hasta}` : null, clave,
  )
  const gastosQ = useFetch(
    `/gastos?desde=${encodeURIComponent(iso.desde)}&hasta=${encodeURIComponent(iso.hasta)}`
    + `&limite=500${filtroCat ? `&categoria=${filtroCat}` : ''}`
    + `${filtroTipo ? `&tipo_egreso=${filtroTipo}` : ''}`,
    [...clave, filtroCat, filtroTipo],
  )

  const refrescar = () => { gastosQ.refetch(); resumenQ.refetch() }
  useRealtimeEvent(['gasto_registrado', 'gasto_rechazado', 'reconnected'], refrescar)

  const gastos = Array.isArray(gastosQ.data) ? gastosQ.data : []
  const r = resumenQ.data

  return (
    <div className="space-y-3">
      {admin && <BandejaRevision refreshKey={refreshKey} />}

      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold tracking-tight mr-auto">Gastos</h1>
          <Button size="sm" onClick={() => setModalAbierto(true)} className="h-8 gap-1">
            <Plus className="size-3.5" /> Registrar
          </Button>
        </div>
        <div className="mt-2.5 flex gap-1.5 flex-wrap">
          {PERIODOS.map(([id, label]) => (
            <button key={id} type="button" onClick={() => setPeriodoId(id)} aria-pressed={periodoId === id}
              className={`px-2.5 py-1 rounded-md border text-body-sm transition-colors ${
                periodoId === id ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>
              {label}
            </button>
          ))}
        </div>
      </Card>

      {admin && <Kpis resumen={r} cargando={resumenQ.loading} />}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        {admin && (
          <div className="lg:col-span-2">
            <Desglose resumen={r} cargando={resumenQ.loading} />
          </div>
        )}
        <div className={admin ? '' : 'lg:col-span-3'}>
          <PanelRecurrentes mes={mesActual} refreshKey={refreshKey} onPagado={refrescar} />
        </div>
      </div>

      <Card className="p-3.5">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2.5">
          <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
            <Receipt className="size-3.5" /> Movimientos
          </h2>
          <div className="flex gap-1.5">
            <select value={filtroCat} onChange={(e) => setFiltroCat(e.target.value)}
              aria-label="Filtrar por categoría" className={SELECT}>
              <option value="">Todas las categorías</option>
              {CATEGORIAS_GASTO.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <select value={filtroTipo} onChange={(e) => setFiltroTipo(e.target.value)}
              aria-label="Filtrar por tipo" className={SELECT}>
              <option value="">Todo</option>
              {Object.entries(TIPO_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
        </div>

        {gastosQ.loading ? (
          <SkeletonFilas filas={6} />
        ) : gastos.length === 0 ? (
          <EstadoVacio
            icon={Receipt}
            titulo="Sin movimientos en este período"
            detalle="Cada bolsa, pote, flete o pago de nómina que anotes aquí es plata que deja de ser invisible al final del mes."
            accion={<Button size="sm" onClick={() => setModalAbierto(true)}>Registrar el primero</Button>}
          />
        ) : (
          <ul className="divide-y divide-border-subtle">
            {gastos.map((g) => (
              <li key={g.id} className="py-2 flex items-center gap-2 text-body-sm">
                <span className="text-caption tabular text-muted-foreground w-11 shrink-0">
                  {new Date(g.creado_en).toLocaleDateString('es-CO', {
                    day: '2-digit', month: '2-digit', timeZone: 'America/Bogota',
                  })}
                </span>
                {/* En móvil la categoría cede el paso al concepto, que es lo que identifica la fila. */}
                <span className="hidden sm:block w-24 shrink-0 truncate text-muted-foreground">
                  {CATEGORIA_LABEL[g.categoria] ?? g.categoria}
                </span>
                <span className="min-w-0 flex-1 truncate">{g.concepto || '—'}</span>
                {TIPO_LABEL[g.tipo_egreso] && g.tipo_egreso !== 'gasto' && (
                  <span className="shrink-0 px-1.5 py-0.5 rounded border border-border-subtle text-caption text-muted-foreground">
                    {TIPO_LABEL[g.tipo_egreso]}
                  </span>
                )}
                <span className="tabular font-semibold shrink-0">{cop(Number(g.monto))}</span>
                {admin && (
                  <button type="button" onClick={() => setAnulando(g)}
                    aria-label={`Anular gasto de ${cop(Number(g.monto))}`}
                    className="shrink-0 text-caption text-muted-foreground hover:text-destructive">
                    Anular
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <ModalGastoRapido abierto={modalAbierto} onCerrar={() => setModalAbierto(false)}
        onRegistrado={refrescar} />
      {anulando && (
        <ModalAnular gasto={anulando} onCerrar={() => setAnulando(null)}
          onAnulado={() => { setAnulando(null); refrescar() }} />
      )}
    </div>
  )
}

/** Los cuatro números del período. `null` mientras carga (no se inventan ceros). */
function Kpis({ resumen, cargando }) {
  if (cargando || !resumen) {
    return (
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => <Card key={i} className="p-3.5 h-[86px] animate-pulse" />)}
      </div>
    )
  }
  const total = Number(resumen.total_gasto)
  const previo = Number(resumen.gasto_periodo_anterior)
  const delta = previo > 0 ? Math.round(((total - previo) / previo) * 100) : null
  const fueraDeGasto = Number(resumen.total_retiro) + Number(resumen.total_inversion)

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Kpi icon={Receipt} titulo="Gastos del período" valor={cop(total)}
        pie={delta === null
          ? 'Sin período anterior para comparar'
          : `${delta >= 0 ? '+' : ''}${delta}% vs. el período anterior`}
        tono={delta === null ? '' : delta > 0 ? 'text-destructive' : 'text-success'}
        flecha={delta === null ? null : delta > 0 ? TrendingUp : TrendingDown} />

      <Kpi icon={Percent} titulo="Peso sobre la venta"
        valor={resumen.pct_ventas === null ? '—' : `${Math.round(resumen.pct_ventas)}%`}
        pie={resumen.pct_ventas === null
          ? 'Sin ventas en el período'
          : `De cada $100 vendidos, ${Math.round(resumen.pct_ventas)} se van en gastos`} />

      <Kpi icon={Scale} titulo="Para no perder, vende"
        valor={resumen.punto_equilibrio_dia === null
          ? '—' : `${cop(resumen.punto_equilibrio_dia)} / día`}
        pie={resumen.punto_equilibrio_dia === null
          ? 'Falta margen o gastos fijos del mes'
          : `${cop(resumen.punto_equilibrio_mes)} en el mes, con ${Math.round(resumen.margen_bruto_pct)}% de margen`} />

      <Kpi icon={Wallet} titulo="Salió, pero no es gasto" valor={cop(fueraDeGasto)}
        pie={fueraDeGasto > 0
          ? `Retiros ${cop(resumen.total_retiro)} · inversión ${cop(resumen.total_inversion)}`
          : 'Retiros tuyos y compras de vitrinas o equipos'} />
    </div>
  )
}

function Kpi({ icon: Icon, titulo, valor, pie, tono = '', flecha: Flecha = null }) {
  return (
    <Card className="p-3.5">
      <p className="text-caption uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
        <Icon className="size-3.5" /> {titulo}
      </p>
      <p className="mt-1 text-xl font-semibold tabular tracking-tight">{valor}</p>
      <p className={`mt-0.5 text-caption inline-flex items-center gap-1 ${tono || 'text-muted-foreground'}`}>
        {Flecha && <Flecha className="size-3" />} {pie}
      </p>
    </Card>
  )
}

/** Desglose por categoría, agrupado en fijos y variables (el corte que define el equilibrio). */
function Desglose({ resumen, cargando }) {
  if (cargando || !resumen) return <Card className="p-3.5"><SkeletonFilas filas={5} /></Card>

  const filas = resumen.por_categoria ?? []
  const mayor = filas.reduce((m, f) => Math.max(m, Number(f.total)), 0)
  const fijos = filas.filter((f) => f.fijo)
  const variables = filas.filter((f) => !f.fijo)

  return (
    <Card className="p-3.5">
      <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground mb-2.5">
        En qué se fue
      </h2>
      {filas.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">
          Sin gastos registrados en este período.
        </p>
      ) : (
        <div className="space-y-3">
          <Grupo titulo="Fijos" ayuda="Se pagan igual vendas mucho o nada" filas={fijos}
            total={resumen.fijos} mayor={mayor} />
          <Grupo titulo="Variables" ayuda="Suben y bajan con la venta" filas={variables}
            total={resumen.variables} mayor={mayor} />
        </div>
      )}
    </Card>
  )
}

function Grupo({ titulo, ayuda, filas, total, mayor }) {
  if (filas.length === 0) return null
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-body-sm font-medium">
          {titulo} <span className="text-caption text-muted-foreground">· {ayuda}</span>
        </p>
        <p className="text-body-sm tabular font-semibold">{cop(total)}</p>
      </div>
      <ul className="mt-1.5 space-y-1.5">
        {filas.map((f) => (
          <li key={f.categoria} className="flex items-center gap-2 text-body-sm">
            <span className="w-24 sm:w-32 shrink-0 truncate text-muted-foreground">
              {CATEGORIA_LABEL[f.categoria] ?? f.categoria}
            </span>
            <span className="h-1.5 flex-1 rounded-full bg-surface-2 overflow-hidden">
              <span className="block h-full rounded-full bg-primary"
                style={{ width: `${mayor > 0 ? (Number(f.total) / mayor) * 100 : 0}%` }} />
            </span>
            <span className="w-10 shrink-0 text-right text-caption tabular text-muted-foreground">
              {Math.round(f.pct)}%
            </span>
            <span className="w-24 shrink-0 text-right tabular">{cop(Number(f.total))}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * Anular un gasto ya registrado: devuelve la plata a la caja con un movimiento inverso (nunca borra
 * — la caja tiene que poder explicarse). Es el arreglo de un error de digitación.
 */
function ModalAnular({ gasto, onCerrar, onAnulado }) {
  const [motivo, setMotivo] = useState('')
  const [enviando, setEnviando] = useState(false)

  async function anular() {
    setEnviando(true)
    try {
      const res = await api(`/gastos/${gasto.id}/anular`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ motivo: motivo.trim() || null }),
      })
      if (res.ok) { toast.success('Gasto anulado, la plata volvió a la caja'); onAnulado() }
      else if (res.status === 409) {
        toast.error('No se puede anular: abre la caja, o el gasto pagó una factura de proveedor.')
      } else toast.error('No se pudo anular')
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o && !enviando) onCerrar() }}>
      <DialogContent aria-describedby="anular-desc">
        <DialogHeader>
          <DialogTitle>Anular gasto de {cop(Number(gasto.monto))}</DialogTitle>
          <DialogDescription id="anular-desc">
            {gasto.concepto || CATEGORIA_LABEL[gasto.categoria] || 'Gasto'} — la plata vuelve a la caja
            con un movimiento de ingreso. El gasto no se borra: queda anulado con su motivo.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="anular-motivo">Motivo (opcional)</Label>
            <Input id="anular-motivo" autoFocus value={motivo} onChange={(e) => setMotivo(e.target.value)}
              placeholder="Monto equivocado, gasto repetido…" />
          </div>
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" disabled={enviando} onClick={onCerrar}>
              Cancelar
            </Button>
            <Button variant="destructive" className="flex-1" disabled={enviando} onClick={anular}>
              {enviando ? 'Anulando…' : 'Anular'}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
