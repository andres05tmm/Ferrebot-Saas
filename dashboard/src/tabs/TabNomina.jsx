/*
 * TabNomina — nómina del vertical construcción (Fase 4, flag `nomina`). Liquida quincenas/meses de los
 * trabajadores (directos con motor prestacional completo, patacalientes por hora) y PRORRATEA el costo
 * total de cada uno entre las obras según los días trabajados: así el gasto de personal cae en la obra
 * correcta (alimenta presupuesto vs. real). Corazón del diferenciador del cliente.
 *
 * Contrato de API (pinneado): /api/v1/nomina — GET /periodos (lista), POST /periodos (crea, CONGELA el
 * snapshot de parametros_legales), GET /periodos/{id} (detalle: snapshot + detalles por trabajador +
 * totales), GET /periodos/{id}/trabajador/{tid} (detalle individual + prorrateo por obra),
 * POST /periodos/{id}/{liquidar|cerrar|pagar}. Los campos JSON son los nombres del ORM en español. El
 * dinero se muestra en pesos (cop); los montos internos son NUMERIC(18,4). Live: re-fetch ante 'reconnected'.
 *
 * Ciclo de vida del periodo: ABIERTO (se liquida/re-liquida) → LIQUIDADO (cerrado, se paga) → PAGADO.
 * Reutiliza los átomos de `construccion/comunes.jsx` (Semaforo, Chips, Campo, EstadoVacio, Esqueleto) para
 * leerse como TabObras/TabMaquinas. Calca ese patrón: lista con filas expandibles + formulario de alta.
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import {
  Wallet, ChevronDown, ChevronRight, Plus, CalendarDays, Users, Coins, HandCoins,
  Lock, CheckCircle2, Building2, SlidersHorizontal, Play,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch, cop, num } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Semaforo, Chips, Campo, EstadoVacio, Esqueleto, BTN_PRIMARY, BTN_OUTLINE, SELECT_CLS } from './construccion/comunes.jsx'

// Estado del periodo (enum del ORM) → tono del semáforo + etiqueta. Abierto (por liquidar) azul ·
// liquidado (cerrado, por pagar) ámbar · pagado (cerrado, saldado) verde.
const ESTADO = {
  ABIERTO:   { tono: 'azul',  label: 'Abierto' },
  LIQUIDADO: { tono: 'ambar', label: 'Liquidado' },
  PAGADO:    { tono: 'verde', label: 'Pagado' },
}
const ORDEN_ESTADOS = ['ABIERTO', 'LIQUIDADO', 'PAGADO']
const TIPO_LABEL = { QUINCENAL: 'Quincenal', MENSUAL: 'Mensual', SEMANAL: 'Semanal' }

function metaEstado(estado) {
  return ESTADO[estado] || { tono: 'gris', label: estado || '—' }
}

export default function TabNomina() {
  const { refreshKey } = useOutletContext() ?? {}
  const periodosQ = useFetch('/nomina/periodos', [refreshKey])
  useRealtimeEvent(['reconnected'], periodosQ.refetch)

  const [estado, setEstado] = useState(null)   // null = todos
  const [creando, setCreando] = useState(false)

  const periodos = Array.isArray(periodosQ.data) ? periodosQ.data : []
  const conteos = periodos.reduce((acc, p) => { acc[p.estado] = (acc[p.estado] || 0) + 1; return acc }, {})
  const chips = [
    { valor: null, label: 'Todos', conteo: periodos.length },
    ...ORDEN_ESTADOS
      .filter((e) => conteos[e])
      .map((e) => ({ valor: e, label: metaEstado(e).label, tono: metaEstado(e).tono, conteo: conteos[e] })),
  ]
  const visibles = estado ? periodos.filter((p) => p.estado === estado) : periodos

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-0 flex-1">
            <h2 className="inline-flex items-center gap-1.5 text-sm font-semibold text-foreground">
              <Wallet className="size-4" aria-hidden="true" /> Nómina
            </h2>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              Cada periodo congela los parámetros legales al crearse y reparte el costo por obra.
            </p>
          </div>
          <button onClick={() => setCreando((v) => !v)} className={`${BTN_PRIMARY} h-9 shrink-0`}>
            <Plus className="size-4" /> Nuevo periodo
          </button>
        </div>
        {chips.length > 1 && (
          <div className="mt-2.5">
            <Chips opciones={chips} valor={estado} onChange={setEstado} ariaLabel="Filtrar periodos por estado" />
          </div>
        )}
      </Card>

      {creando && (
        <PeriodoForm onClose={() => setCreando(false)} onCreado={() => { setCreando(false); periodosQ.refetch() }} />
      )}

      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border-subtle flex items-center gap-2">
          <CalendarDays className="size-4 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Periodos {periodos.length > 0 && <span className="tabular">· {visibles.length}</span>}
          </h2>
        </div>

        {periodosQ.loading ? (
          <Esqueleto filas={3} />
        ) : periodos.length === 0 ? (
          <EstadoVacio
            icono={Wallet}
            titulo="Todavía no hay periodos de nómina"
            descripcion="Un periodo liquida a los trabajadores del rango (directos con prestaciones, patacalientes por hora) y reparte su costo a las obras donde trabajaron. Crea el primero para liquidar."
          >
            <button onClick={() => setCreando(true)} className={`${BTN_PRIMARY} h-9`}>
              <Plus className="size-4" /> Crear el primer periodo
            </button>
          </EstadoVacio>
        ) : visibles.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">Ningún periodo con ese estado.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {visibles.map((p) => (
              <PeriodoFila key={p.id} periodo={p} onCambio={periodosQ.refetch} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

// ── Fila de periodo: cabecera clicable (expande) + detalle perezoso ──────────────────────────────
function PeriodoFila({ periodo, onCambio }) {
  const [abierta, setAbierta] = useState(false)
  const est = metaEstado(periodo.estado)
  const panelId = `periodo-detalle-${periodo.id}`

  return (
    <li>
      <button
        type="button"
        onClick={() => setAbierta((v) => !v)}
        aria-expanded={abierta}
        aria-controls={panelId}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors duration-fast hover:bg-surface-2"
      >
        <span className="grid size-9 shrink-0 place-items-center rounded-md bg-surface-2 text-muted-foreground">
          <CalendarDays className="size-[18px]" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-[14px] font-medium text-foreground">
              {periodo.nombre || `Periodo #${periodo.id}`}
            </span>
            <Semaforo tono={est.tono}>{est.label}</Semaforo>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11px] text-muted-foreground">
            <span className="rounded bg-surface-2 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide">
              {TIPO_LABEL[periodo.tipo] || periodo.tipo}
            </span>
            <span className="tabular inline-flex items-center gap-1">
              <CalendarDays className="size-3" aria-hidden="true" />{periodo.fecha_inicio} → {periodo.fecha_fin}
            </span>
          </div>
        </div>
        {abierta ? <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" /> : <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
      </button>

      {abierta && <PeriodoDetalle id={panelId} periodo={periodo} onCambio={onCambio} />}
    </li>
  )
}

function PeriodoDetalle({ id, periodo, onCambio }) {
  const detalleQ = useFetch(`/nomina/periodos/${periodo.id}`)
  const detalle = detalleQ.data
  const [ocupado, setOcupado] = useState(false)

  async function accion(verbo, { confirmar } = {}) {
    if (confirmar && !window.confirm(confirmar)) return
    setOcupado(true)
    try {
      const res = await api(`/nomina/periodos/${periodo.id}/${verbo}`, { method: 'POST' })
      if (!res.ok) {
        const cuerpo = await res.json().catch(() => ({}))
        toast.error(cuerpo?.detail || `No se pudo ${verbo} el periodo`)
        return
      }
      const data = await res.json().catch(() => ({}))
      if (verbo === 'liquidar') toast.success(`Liquidados ${data.trabajadores_liquidados ?? 0} trabajadores`)
      else if (verbo === 'cerrar') toast.success('Periodo cerrado')
      else toast.success('Periodo marcado como pagado')
      detalleQ.refetch(); onCambio()
    } catch { toast.error('Error de conexión') } finally { setOcupado(false) }
  }

  const acciones = {
    ABIERTO: [
      { verbo: 'liquidar', label: 'Liquidar', icono: Play, primario: true },
      { verbo: 'cerrar', label: 'Cerrar', icono: Lock, confirmar: 'Cerrar bloquea la re-liquidación del periodo. ¿Continuar?' },
    ],
    LIQUIDADO: [
      { verbo: 'pagar', label: 'Marcar pagado', icono: HandCoins, primario: true, confirmar: 'Marcar el periodo como pagado. ¿Continuar?' },
    ],
    PAGADO: [],
  }[periodo.estado] || []

  return (
    <div id={id} className="border-t border-border-subtle bg-surface-2/40 px-4 py-3.5 space-y-3.5">
      {/* Acciones del ciclo de vida */}
      {acciones.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {acciones.map((a) => (
            <button
              key={a.verbo}
              onClick={() => accion(a.verbo, { confirmar: a.confirmar })}
              disabled={ocupado}
              className={`${a.primario ? BTN_PRIMARY : BTN_OUTLINE} h-8`}
            >
              <a.icono className="size-3.5" /> {a.label}
            </button>
          ))}
        </div>
      )}

      {detalleQ.loading ? (
        <p className="py-6 text-center text-[12px] text-muted-foreground">Cargando liquidación…</p>
      ) : !detalle ? (
        <p className="py-6 text-center text-[12px] text-muted-foreground">No se pudo cargar la liquidación.</p>
      ) : (
        <>
          <TotalesStrip totales={detalle.totales} />
          <TablaLiquidacion periodoId={periodo.id} detalles={detalle.detalles} pagado={periodo.estado === 'PAGADO'} />
          <ParametrosSnapshot parametros={detalle.parametros} />
        </>
      )}
    </div>
  )
}

// ── Totales del periodo (tira de mini-stats con cifras tabulares) ────────────────────────────────
function TotalesStrip({ totales }) {
  if (!totales) return null
  const items = [
    { icono: Users, label: 'Trabajadores', valor: num(totales.trabajadores), plano: true },
    { icono: Coins, label: 'Devengado', valor: cop(totales.total_devengado) },
    { icono: HandCoins, label: 'Neto a pagar', valor: cop(totales.total_neto), destacado: true },
    { icono: Wallet, label: 'Costo total (obra)', valor: cop(totales.total_costo) },
  ]
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {items.map((it) => (
        <div key={it.label} className={`rounded-md border p-2.5 ${it.destacado ? 'border-primary/30 bg-primary-soft' : 'border-border-subtle bg-surface'}`}>
          <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
            <it.icono className="size-3" aria-hidden="true" /> {it.label}
          </div>
          <div className={`tabular mt-1 text-[15px] font-semibold ${it.destacado ? 'text-primary' : 'text-foreground'}`}>
            {it.valor}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Tabla de liquidación por trabajador (fila expandible → prorrateo por obra) ────────────────────
function TablaLiquidacion({ periodoId, detalles, pagado }) {
  if (!Array.isArray(detalles) || detalles.length === 0) {
    return (
      <div className="rounded-md border border-border-subtle bg-surface py-8 text-center">
        <p className="text-[12px] font-medium text-foreground">Sin liquidación todavía</p>
        <p className="mt-0.5 text-[11px] text-muted-foreground">Pulsa «Liquidar» para calcular la nómina de los trabajadores con asistencia en el rango.</p>
      </div>
    )
  }
  return (
    <div className="overflow-x-auto rounded-md border border-border-subtle bg-surface">
      <table className="w-full min-w-[560px] text-[12px]">
        <thead>
          <tr className="border-b border-border-subtle text-[10px] uppercase tracking-wider text-muted-foreground">
            <th className="px-3 py-2 text-left font-semibold">Trabajador</th>
            <th className="px-3 py-2 text-right font-semibold">Días</th>
            <th className="px-3 py-2 text-right font-semibold">Devengado</th>
            <th className="px-3 py-2 text-right font-semibold">Deducciones</th>
            <th className="px-3 py-2 text-right font-semibold">Neto</th>
            <th className="px-3 py-2 text-right font-semibold">Costo obra</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-subtle">
          {detalles.map((d) => (
            <TrabajadorFila key={d.id} periodoId={periodoId} detalle={d} />
          ))}
        </tbody>
      </table>
      {pagado && (
        <div className="flex items-center gap-1.5 border-t border-border-subtle px-3 py-1.5 text-[11px] text-success">
          <CheckCircle2 className="size-3.5" aria-hidden="true" /> Periodo pagado
        </div>
      )}
    </div>
  )
}

function TrabajadorFila({ periodoId, detalle }) {
  const [abierta, setAbierta] = useState(false)
  return (
    <>
      <tr
        className="cursor-pointer transition-colors duration-fast hover:bg-surface-2"
        onClick={() => setAbierta((v) => !v)}
      >
        <td className="px-3 py-2">
          <div className="flex items-center gap-1.5">
            {abierta ? <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden="true" /> : <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden="true" />}
            <div className="min-w-0">
              <div className="truncate font-medium text-foreground">{detalle.trabajador_nombre}</div>
              <div className="text-[10px] text-muted-foreground">
                {detalle.tipo_vinculacion === 'DIRECTO' ? 'Directo' : 'Patacaliente'} · {detalle.trabajador_documento}
              </div>
            </div>
          </div>
        </td>
        <td className="tabular px-3 py-2 text-right text-secondary-foreground">{num(detalle.dias_liquidados)}</td>
        <td className="tabular px-3 py-2 text-right text-secondary-foreground">{cop(detalle.total_devengado)}</td>
        <td className="tabular px-3 py-2 text-right text-muted-foreground">−{cop(detalle.total_deducciones)}</td>
        <td className="tabular px-3 py-2 text-right font-semibold text-foreground">{cop(detalle.neto_pagar)}</td>
        <td className="tabular px-3 py-2 text-right text-secondary-foreground">{cop(detalle.costo_total)}</td>
      </tr>
      {abierta && (
        <tr>
          <td colSpan={6} className="bg-surface-2/50 px-3 py-3">
            <DesglosePersonal periodoId={periodoId} detalle={detalle} />
          </td>
        </tr>
      )}
    </>
  )
}

// Detalle individual: desglose de la liquidación + prorrateo por obra (GET .../trabajador/{tid}).
function DesglosePersonal({ periodoId, detalle }) {
  const q = useFetch(`/nomina/periodos/${periodoId}/trabajador/${detalle.trabajador_id}`)
  const prorrateos = Array.isArray(q.data?.prorrateos) ? q.data.prorrateos : []

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {/* Desglose de conceptos */}
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 rounded-md border border-border-subtle bg-surface p-3 text-[12px]">
        <Concepto etiqueta="Salario devengado" valor={detalle.salario_devengado} />
        <Concepto etiqueta="Auxilio transporte" valor={detalle.auxilio_transporte} />
        <Concepto etiqueta="Horas extra" valor={detalle.valor_horas_extra} />
        <Concepto etiqueta="Total devengado" valor={detalle.total_devengado} fuerte />
        <Concepto etiqueta="Salud (empleado)" valor={detalle.salud_empleado} resta />
        <Concepto etiqueta="Pensión (empleado)" valor={detalle.pension_empleado} resta />
        <Concepto etiqueta="Neto a pagar" valor={detalle.neto_pagar} fuerte />
        <Concepto etiqueta="Aportes empleador" valor={detalle.aportes_empleador} tenue />
        <Concepto etiqueta="Provisiones" valor={detalle.provisiones} tenue />
      </dl>

      {/* Prorrateo por obra */}
      <div className="rounded-md border border-border-subtle bg-surface p-3">
        <div className="mb-2 flex items-center gap-1.5">
          <Building2 className="size-3.5 text-muted-foreground" aria-hidden="true" />
          <h4 className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Prorrateo por obra</h4>
        </div>
        {q.loading ? (
          <p className="py-3 text-center text-[11px] text-muted-foreground">Cargando…</p>
        ) : prorrateos.length === 0 ? (
          <p className="py-3 text-center text-[11px] text-muted-foreground">Sin prorrateo (aún no liquidado).</p>
        ) : (
          <ul className="space-y-1.5">
            {prorrateos.map((pr, i) => (
              <li key={i} className="flex items-center justify-between gap-2 text-[12px]">
                <span className="inline-flex min-w-0 items-center gap-1.5">
                  <span className={`size-1.5 shrink-0 rounded-full ${pr.obra_nombre ? 'bg-primary' : 'bg-muted-foreground'}`} aria-hidden="true" />
                  <span className="truncate text-secondary-foreground">{pr.obra_nombre || 'Administrativo'}</span>
                  <span className="tabular shrink-0 text-[10px] text-muted-foreground">{num(pr.dias_imputados)} d</span>
                </span>
                <span className="tabular shrink-0 font-medium text-foreground">{cop(pr.costo_imputado)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function Concepto({ etiqueta, valor, fuerte, resta, tenue }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className={`${tenue ? 'text-muted-foreground' : 'text-secondary-foreground'}`}>{etiqueta}</dt>
      <dd className={`tabular ${fuerte ? 'font-semibold text-foreground' : tenue ? 'text-muted-foreground' : 'text-secondary-foreground'}`}>
        {resta ? '−' : ''}{cop(valor)}
      </dd>
    </div>
  )
}

// ── Snapshot de parámetros congelado (verificación del contador) ──────────────────────────────────
function ParametrosSnapshot({ parametros }) {
  const [abierto, setAbierto] = useState(false)
  if (!parametros) return null
  const pct = (v) => `${(Number(v) * 100).toLocaleString('es-CO', { maximumFractionDigits: 2 })}%`
  const filas = [
    ['SMMLV', cop(parametros.smmlv)],
    ['Auxilio transporte', cop(parametros.auxilio_transporte)],
    ['Horas/mes', num(parametros.horas_mes)],
    ['Recargo HE diurna', `×${num(parametros.recargo_he_diurna)}`],
    ['Recargo HE nocturna', `×${num(parametros.recargo_he_nocturna)}`],
    ['Recargo dominical', `×${num(parametros.recargo_dominical)}`],
    ['Salud/pensión empleado', `${pct(parametros.salud_empleado_pct)} / ${pct(parametros.pension_empleado_pct)}`],
    ['ARL', pct(parametros.arl_pct)],
    ['Cesantías / prima', `${pct(parametros.cesantias_pct)} / ${pct(parametros.prima_pct)}`],
    ['Vacaciones', pct(parametros.vacaciones_pct)],
  ]
  return (
    <div className="rounded-md border border-border-subtle bg-surface">
      <button
        type="button"
        onClick={() => setAbierto((v) => !v)}
        aria-expanded={abierto}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left"
      >
        <SlidersHorizontal className="size-3.5 text-muted-foreground" aria-hidden="true" />
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Parámetros congelados</span>
        {abierto ? <ChevronDown className="ml-auto size-3.5 text-muted-foreground" aria-hidden="true" /> : <ChevronRight className="ml-auto size-3.5 text-muted-foreground" aria-hidden="true" />}
      </button>
      {abierto && (
        <dl className="grid grid-cols-1 gap-x-4 gap-y-1 border-t border-border-subtle px-3 py-2.5 text-[11px] sm:grid-cols-2">
          {filas.map(([k, v]) => (
            <div key={k} className="flex items-baseline justify-between gap-2">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="tabular text-secondary-foreground">{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

// ── Formulario de alta de periodo ─────────────────────────────────────────────────────────────────
function PeriodoForm({ onClose, onCreado }) {
  const [f, setF] = useState({ tipo: 'QUINCENAL', nombre: '', fecha_inicio: '', fecha_fin: '' })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF((prev) => ({ ...prev, [k]: e.target.value }))

  async function crear() {
    if (!f.fecha_inicio || !f.fecha_fin) { toast.error('Indica el rango de fechas del periodo'); return }
    if (f.fecha_fin < f.fecha_inicio) { toast.error('La fecha fin no puede ser anterior al inicio'); return }
    const payload = {
      tipo: f.tipo,
      fecha_inicio: f.fecha_inicio,
      fecha_fin: f.fecha_fin,
      nombre: f.nombre.trim() || null,
    }
    setEnviando(true)
    try {
      const res = await api('/nomina/periodos', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      if (!res.ok) {
        const cuerpo = await res.json().catch(() => ({}))
        toast.error(cuerpo?.detail || 'No se pudo crear el periodo')
        return
      }
      toast.success('Periodo creado')
      onCreado()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-4">
      <h2 className="mb-3 inline-flex items-center gap-1.5 text-sm font-semibold">
        <Wallet className="size-4" aria-hidden="true" /> Nuevo periodo de nómina
      </h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Campo label="Nombre" className="sm:col-span-2">
          <Input value={f.nombre} onChange={set('nombre')} placeholder="Ej. Quincena 1 — julio 2026" className="h-9" />
        </Campo>
        <Campo label="Tipo" requerido>
          <select value={f.tipo} onChange={set('tipo')} className={SELECT_CLS}>
            <option value="QUINCENAL">Quincenal</option>
            <option value="MENSUAL">Mensual</option>
            <option value="SEMANAL">Semanal</option>
          </select>
        </Campo>
        <div className="hidden sm:block" aria-hidden="true" />
        <Campo label="Fecha de inicio" requerido>
          <Input type="date" value={f.fecha_inicio} onChange={set('fecha_inicio')} className="h-9" />
        </Campo>
        <Campo label="Fecha fin" requerido>
          <Input type="date" value={f.fecha_fin} onChange={set('fecha_fin')} className="h-9" />
        </Campo>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">
        Al crear el periodo se congelan los parámetros legales vigentes: la liquidación usará esos valores aunque cambien después.
      </p>
      <div className="mt-4 flex items-center justify-end gap-2">
        <button onClick={onClose} className={`${BTN_OUTLINE} h-10`}>Cancelar</button>
        <button onClick={crear} disabled={enviando} className={`${BTN_PRIMARY} h-10`}>
          {enviando ? 'Creando…' : 'Crear periodo'}
        </button>
      </div>
    </Card>
  )
}
