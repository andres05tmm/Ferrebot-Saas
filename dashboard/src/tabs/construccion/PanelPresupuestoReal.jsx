/*
 * PanelPresupuestoReal — el diferenciador del vertical construcción (Fase 3): PRESUPUESTO VS REAL de
 * una obra, en vivo. Vive dentro del detalle expandible de `TabObras` (una obra = una fila que se abre).
 * Compara lo que la obra CONSUME (gastos + compras + nómina prorrateada + horas de máquina + consumos de
 * inventario) contra lo PRESUPUESTADO (el ingreso y la utilidad de la cotización GANADA) y pinta un
 * SEMÁFORO grande de rentabilidad —los márgenes reales del cliente son de 3–4%, así que la alerta debe
 * llegar ANTES de la pérdida.
 *
 * Contrato de API (conciliado por el integrador D3 contra el backend real H3/C3/O3 — el backend con tests
 * ES el contrato; gasto y horas NO son sub-recursos de obra, imputan por sus routers propios con `obra_id`):
 *   - GET  /obras/{id}/gasto-real   → desglose money-safe (los 7 campos de `DesgloseGasto`) + presupuesto:
 *       { total_gastos, total_compras, total_prorrateo_nomina, total_horas_maquina,
 *         total_consumos_inventario, gasto_total, semaforo: "verde"|"amarillo"|"rojo",
 *         ingreso_presupuestado, utilidad_presupuestada, utilidad_real }
 *       Robustez: `gasto_total` se lee también como `total`; el desglose se acepta plano o anidado bajo
 *       `desglose`. Dinero llega como STRING (Decimal sin float).
 *   - POST /gastos                  → imputa un gasto a la obra (router de caja, feature `caja`). Body:
 *       { obra_id, categoria, concepto, monto, categoria_gasto?, metodo_pago?, numero_referencia? }.
 *       `categoria` es la taxonomía POS NOT NULL (se deriva del vertical `categoria_gasto` con CATEGORIA_POS);
 *       `monto` STRING. Postea SU egreso de caja → exige caja abierta (si no, 409).
 *   - POST /maquinas/{maquina_id}/horas → registra un parte de horas (router de maquinaria, feature
 *       `maquinaria`). Body: { obra_id, fecha, horas_trabajadas, observaciones? }. Respuesta
 *       `RegistroHorasResultado`: { horas_facturables, minimo_cubierto, precio_hora, ingreso, replay }
 *       para el aviso "6h facturables, mínimo cubierto, ingreso $X". El backend resuelve la asignación
 *       activa (obra, máquina) para tarifar; sin asignación que cubra la fecha → 409.
 *   - POST /obras/{id}/consumos     → imputa material del catálogo. Body:
 *       { producto_id, cantidad, costo_unitario, fecha?, responsable?, observaciones? }.
 *   - POST /obras/{id}/liquidar     → congela el snapshot inmutable (idempotente: re-liquidar devuelve el
 *       mismo snapshot con 200). Solo con la obra FINALIZADA (si no, 409); al liquidar la obra pasa a LIQUIDADA.
 *   - GET  /obras/{id}/liquidacion  → snapshot congelado (cuando la obra está LIQUIDADA).
 * Endpoints REUSADOS (ya existen): GET /maquinas (select de máquina), GET /productos?q= (buscar material).
 *
 * Presentación tokenizada (design system del repo, comunes.jsx). El total autoritativo es el del backend
 * (función pura money-safe `calcular_gasto_real_obra`); acá solo se formatea y se dibujan proporciones.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import {
  Gauge, Wallet, ShoppingCart, Users, Timer, Package, Lock, Search,
  TrendingUp, TrendingDown, Minus, X, CheckCircle2,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch, cop, num } from '@/components/shared.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Campo, EstadoVacio, BTN_PRIMARY, BTN_OUTLINE, SELECT_CLS } from './comunes.jsx'

// Select compacto (h-8) para los formularios inline: deriva del SELECT_CLS del design system cambiando
// solo la altura (h-9→h-8). Se hace por `replace` —no por `${SELECT_CLS} h-8`— porque dos utilidades de
// altura en el mismo elemento chocan y gana la del stylesheet (h-9), desalineando con los Input h-8.
const SELECT_COMPACTO = SELECT_CLS.replace('h-9', 'h-8')

// Número desde el string Decimal del backend (COP sin float): NaN/null → 0.
const n = (v) => { const x = Number(v); return Number.isFinite(x) ? x : 0 }
// Clamp a [0, 100] para anchos de barra en %.
const pct = (parte, todo) => (todo > 0 ? Math.min(100, Math.max(0, (parte / todo) * 100)) : 0)

// Semáforo del backend (minúsculas, espeja services.calculations.obra.Semaforo.value) → presentación.
// `amarillo` mapea al token `warning` (ámbar). Cada estado lleva icono + texto (regla color-not-only).
const SEMAFORO = {
  verde:    { clase: 'text-success bg-success/10 border-success/25',           punto: 'bg-success',     label: 'Rentable',                titulo: 'La obra cubre la utilidad presupuestada.' },
  amarillo: { clase: 'text-warning bg-warning/10 border-warning/25',           punto: 'bg-warning',     label: 'Comiéndose la utilidad',  titulo: 'El margen es positivo pero por debajo de la utilidad presupuestada.' },
  rojo:     { clase: 'text-destructive bg-destructive/10 border-destructive/25', punto: 'bg-destructive', label: 'En pérdida',              titulo: 'El gasto real superó el ingreso presupuestado.' },
}
// Tono del relleno del medidor por semáforo (barra = costo consumido).
const FILL = { verde: 'bg-success', amarillo: 'bg-warning', rojo: 'bg-destructive' }

const metaSemaforo = (s) => SEMAFORO[s] || { clase: 'text-muted-foreground bg-surface-2 border-border', punto: 'bg-muted-foreground', label: '—', titulo: '' }

// Normaliza la respuesta de gasto-real a una forma estable, tolerando divergencias del backend:
// plano o anidado bajo `desglose`; total como `total` o `gasto_total`; utilidad_real ausente se deriva.
function normalizarGastoReal(raw) {
  if (!raw) return null
  const d = raw.desglose && typeof raw.desglose === 'object' ? { ...raw, ...raw.desglose } : raw
  const ingreso = n(d.ingreso_presupuestado)
  const total = n(d.total ?? d.gasto_total)
  return {
    componentes: {
      gastos: n(d.total_gastos),
      compras: n(d.total_compras),
      nomina: n(d.total_prorrateo_nomina),
      horas: n(d.total_horas_maquina),
      consumos: n(d.total_consumos_inventario),
    },
    total,
    semaforo: d.semaforo || 'verde',
    ingreso,
    utilidadPresup: n(d.utilidad_presupuestada),
    utilidadReal: d.utilidad_real != null ? n(d.utilidad_real) : ingreso - total,
  }
}

export default function PanelPresupuestoReal({ obra, onCambio }) {
  const liquidada = obra.estado === 'LIQUIDADA'
  const finalizada = obra.estado === 'FINALIZADA'
  const gastoQ = useFetch(`/obras/${obra.id}/gasto-real`)
  // El snapshot congelado solo se pide si la obra ya está liquidada (si no, el path falsy deja el hook en reposo).
  const liqQ = useFetch(liquidada ? `/obras/${obra.id}/liquidacion` : null)

  const [form, setForm] = useState(null)   // null | 'gasto' | 'horas' | 'consumo'

  const g = normalizarGastoReal(gastoQ.data)
  const cerrarForm = () => setForm(null)
  const trasImputar = () => { cerrarForm(); gastoQ.refetch() }

  return (
    <section className="rounded-md border border-border-subtle bg-surface p-3.5" aria-label="Presupuesto vs real de la obra">
      <div className="mb-3 flex items-center gap-2">
        <Gauge className="size-4 text-muted-foreground" aria-hidden="true" />
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Presupuesto vs real</h3>
        {liquidada && (
          <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground">
            <Lock className="size-3" aria-hidden="true" /> Liquidada
          </span>
        )}
      </div>

      {gastoQ.loading ? (
        <EsqueletoPanel />
      ) : gastoQ.error ? (
        <p className="rounded-md bg-destructive/10 px-3 py-2 text-[12px] text-destructive">No se pudo calcular el gasto real de la obra.</p>
      ) : !g ? (
        <p className="py-6 text-center text-[12px] text-muted-foreground">Sin datos de gasto real.</p>
      ) : (
        <>
          <Encabezado g={g} liquidada={liquidada} liq={liqQ.data} />
          <MedidorRentabilidad g={g} />
          <Desglose g={g} />

          {liquidada ? (
            <p className="mt-3 flex items-center gap-1.5 rounded-md bg-surface-2 px-3 py-2 text-[11px] text-muted-foreground">
              <Lock className="size-3.5 shrink-0" aria-hidden="true" />
              Obra liquidada: el desglose quedó congelado y no admite nuevas imputaciones.
            </p>
          ) : (
            <>
              <Acciones activo={form} onAbrir={setForm} />
              {form === 'gasto' && <FormImputarGasto obraId={obra.id} onHecho={trasImputar} onCancelar={cerrarForm} />}
              {form === 'horas' && <FormRegistrarHoras obraId={obra.id} onHecho={trasImputar} onCancelar={cerrarForm} />}
              {form === 'consumo' && <FormRegistrarConsumo obraId={obra.id} onHecho={trasImputar} onCancelar={cerrarForm} />}
              <BarraLiquidar obra={obra} finalizada={finalizada} onHecho={() => { gastoQ.refetch(); onCambio?.() }} />
            </>
          )}
        </>
      )}
    </section>
  )
}

// ── Encabezado: semáforo grande + tres KPIs (ingreso presupuestado · gasto real · utilidad real) ──────
function Encabezado({ g, liquidada, liq }) {
  const meta = metaSemaforo(g.semaforo)
  const sinPresupuesto = g.ingreso <= 0
  // Delta de utilidad: real − presupuestada. Positivo = mejor de lo esperado.
  const delta = g.utilidadReal - g.utilidadPresup
  const IconDelta = delta > 0 ? TrendingUp : delta < 0 ? TrendingDown : Minus
  const tonoUtil = g.utilidadReal < 0 ? 'text-destructive' : g.utilidadReal < g.utilidadPresup ? 'text-warning' : 'text-success'

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-[auto_1fr] sm:items-center">
      {/* Semáforo grande: punto + etiqueta, con el porqué debajo. Nunca solo color. */}
      <div className={`flex flex-col gap-1 rounded-lg border px-4 py-3 ${meta.clase}`} role="status" title={meta.titulo}>
        <span className="inline-flex items-center gap-2 text-[15px] font-semibold leading-none">
          <span className={`size-2.5 shrink-0 rounded-full ${meta.punto}`} aria-hidden="true" />
          {meta.label}
        </span>
        <span className="text-[11px] font-normal opacity-80">{meta.titulo}</span>
      </div>

      {/* KPIs */}
      <dl className="grid grid-cols-1 gap-2 text-center sm:grid-cols-3">
        <Kpi etiqueta={liquidada ? 'Ingreso presup.' : 'Presupuestado'} valor={g.ingreso} tenue={sinPresupuesto} />
        <Kpi etiqueta="Gasto real" valor={g.total} />
        <div className="rounded-md bg-surface-2 px-2 py-2">
          <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">Utilidad real</dt>
          <dd className={`tabular text-[14px] font-semibold ${tonoUtil}`}>{cop(g.utilidadReal)}</dd>
          {g.utilidadPresup > 0 && !sinPresupuesto && (
            <dd className="mt-0.5 inline-flex items-center justify-center gap-0.5 text-[10px] text-muted-foreground">
              <IconDelta className="size-3" aria-hidden="true" />
              <span className="tabular">{cop(Math.abs(delta))}</span> vs presup.
            </dd>
          )}
        </div>
      </dl>
      {liquidada && liq?.fecha_liquidacion && (
        <p className="sm:col-span-2 -mt-0.5 text-[11px] text-muted-foreground">
          Liquidada el {String(liq.fecha_liquidacion).slice(0, 10)}.
        </p>
      )}
    </div>
  )
}

function Kpi({ etiqueta, valor, tenue = false }) {
  return (
    <div className="rounded-md bg-surface-2 px-2 py-2">
      <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{etiqueta}</dt>
      <dd className={`tabular text-[14px] font-semibold ${tenue ? 'text-muted-foreground' : 'text-foreground'}`}>{cop(valor)}</dd>
    </div>
  )
}

// ── Medidor de rentabilidad: el gasto real como fracción del ingreso presupuestado, con una marca en el
// umbral donde empieza a comerse la utilidad. Sin presupuesto no hay contra qué medir → nota tenue. ──────
function MedidorRentabilidad({ g }) {
  if (g.ingreso <= 0) {
    return (
      <p className="mt-3 rounded-md bg-surface-2 px-3 py-2 text-[11px] text-muted-foreground">
        Esta obra no tiene ingreso presupuestado (no nació de una cotización): el semáforo se calcula sin umbral de utilidad. Su gasto real se sigue acumulando abajo.
      </p>
    )
  }
  const relleno = pct(g.total, g.ingreso)
  const sobrepaso = g.total > g.ingreso
  // Umbral: posición donde el gasto = ingreso − utilidad presupuestada (a partir de ahí se come la utilidad).
  const umbral = pct(g.ingreso - g.utilidadPresup, g.ingreso)

  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center justify-between text-[10px] text-muted-foreground">
        <span>Gasto real sobre lo presupuestado</span>
        <span className="tabular">{num((g.total / g.ingreso) * 100)}%</span>
      </div>
      <div className="relative h-3 overflow-hidden rounded-full bg-surface-2" role="img"
        aria-label={`Gasto real ${cop(g.total)} de ${cop(g.ingreso)} presupuestado`}>
        <div className={`h-full rounded-full transition-[width] duration-500 ${FILL[g.semaforo] || 'bg-muted-foreground'}`} style={{ width: `${relleno}%` }} />
        {/* Marca del umbral de utilidad (solo si hay utilidad presupuestada y no se desbordó). */}
        {g.utilidadPresup > 0 && !sobrepaso && umbral < 100 && (
          <span className="absolute top-0 h-full w-px bg-foreground/40" style={{ left: `${umbral}%` }} aria-hidden="true"
            title="Umbral: a partir de aquí se come la utilidad" />
        )}
      </div>
      <div className="mt-1 flex items-center justify-between text-[10px] text-muted-foreground">
        <span className="tabular">{cop(g.total)}</span>
        {g.utilidadPresup > 0 && <span>Utilidad presup. {cop(g.utilidadPresup)}</span>}
        <span className="tabular">{cop(g.ingreso)}</span>
      </div>
    </div>
  )
}

// ── Desglose por componente: 5 filas con barra de proporción + total. ─────────────────────────────────
const COMPONENTES = [
  { clave: 'gastos', label: 'Gastos', icono: Wallet },
  { clave: 'compras', label: 'Compras', icono: ShoppingCart },
  { clave: 'nomina', label: 'Nómina prorrateada', icono: Users },
  { clave: 'horas', label: 'Horas de máquina', icono: Timer },
  { clave: 'consumos', label: 'Consumos de inventario', icono: Package },
]

function Desglose({ g }) {
  const vacio = g.total <= 0
  if (vacio) {
    return (
      <div className="mt-3">
        <EstadoVacio
          icono={Gauge}
          titulo="Aún no se ha imputado nada a esta obra"
          descripcion="Imputa gastos, compras, horas de máquina o consumos de inventario para ver crecer su gasto real y encender el semáforo de rentabilidad."
        />
      </div>
    )
  }
  return (
    <ul className="mt-3 space-y-1.5">
      {COMPONENTES.map(({ clave, label, icono: Icono }) => {
        const valor = g.componentes[clave]
        return (
          <li key={clave} className="grid grid-cols-[1fr_auto] items-center gap-x-3">
            <div className="flex items-center gap-2 min-w-0">
              <Icono className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="truncate text-[12px] text-secondary-foreground">{label}</span>
            </div>
            <span className="tabular text-[12px] font-medium text-foreground">{cop(valor)}</span>
            <div className="col-span-2 mt-0.5 h-1 overflow-hidden rounded-full bg-surface-2">
              <div className="h-full rounded-full bg-primary/60" style={{ width: `${pct(valor, g.total)}%` }} aria-hidden="true" />
            </div>
          </li>
        )
      })}
      <li className="mt-1 flex items-center justify-between border-t border-border pt-1.5">
        <span className="text-[12px] font-semibold text-foreground">Gasto real total</span>
        <span className="tabular text-[14px] font-semibold text-primary">{cop(g.total)}</span>
      </li>
    </ul>
  )
}

// ── Acciones (progressive disclosure): un formulario a la vez. ────────────────────────────────────────
function Acciones({ activo, onAbrir }) {
  const botones = [
    { clave: 'gasto', label: 'Imputar gasto', icono: Wallet },
    { clave: 'horas', label: 'Registrar horas', icono: Timer },
    { clave: 'consumo', label: 'Registrar consumo', icono: Package },
  ]
  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5">
      {botones.map(({ clave, label, icono: Icono }) => (
        <button key={clave} type="button" onClick={() => onAbrir(activo === clave ? null : clave)} aria-pressed={activo === clave}
          className={`inline-flex h-8 items-center gap-1.5 rounded-md border px-3 text-sm font-medium transition-colors duration-fast ${
            activo === clave ? 'border-primary bg-primary-soft text-primary' : 'border-border bg-surface text-secondary-foreground hover:bg-surface-2'
          }`}>
          <Icono className="size-3.5" aria-hidden="true" /> {label}
        </button>
      ))}
    </div>
  )
}

// Marco común de los formularios inline: título + botón cerrar + acciones al pie.
function MarcoForm({ titulo, onCancelar, enviando, onGuardar, textoGuardar, children }) {
  return (
    <form className="mt-2 rounded-md border border-border-subtle bg-surface-2/50 p-3"
      onSubmit={(e) => { e.preventDefault(); onGuardar() }}>
      <div className="mb-2.5 flex items-center justify-between">
        <h4 className="text-[12px] font-semibold text-foreground">{titulo}</h4>
        <button type="button" onClick={onCancelar} aria-label="Cerrar formulario"
          className="grid size-6 place-items-center rounded text-muted-foreground hover:bg-surface-2 hover:text-foreground">
          <X className="size-3.5" aria-hidden="true" />
        </button>
      </div>
      {children}
      <div className="mt-3 flex items-center justify-end gap-2">
        <button type="button" onClick={onCancelar} className={`${BTN_OUTLINE} h-8`}>Cancelar</button>
        <button type="submit" disabled={enviando} className={`${BTN_PRIMARY} h-8`}>
          {enviando ? 'Guardando…' : textoGuardar}
        </button>
      </div>
    </form>
  )
}

// Fecha de hoy en Colombia (YYYY-MM-DD) como default de los formularios (regla zona horaria).
function hoyCO() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
}

const CATEGORIA_GASTO = [
  ['REPUESTOS', 'Repuestos'], ['MANTENIMIENTO_MAQUINA', 'Mantenimiento de máquina'], ['ALMUERZOS', 'Almuerzos'],
  ['TRANSPORTE_PERSONAL', 'Transporte de personal'], ['COMBUSTIBLE', 'Combustible'], ['PAPELERIA', 'Papelería'],
  ['SERVICIOS_PUBLICOS', 'Servicios públicos'], ['ARRIENDO', 'Arriendo'], ['IMPUESTOS', 'Impuestos'], ['OTRO', 'Otro'],
]
// El backend (GastoCrear) exige la `categoria` POS NOT NULL (taxonomía del retail); la del vertical
// (`categoria_gasto`) convive con ella. Se deriva la POS más cercana desde la del vertical (default 'otros').
const CATEGORIA_POS = {
  REPUESTOS: 'mantenimiento', MANTENIMIENTO_MAQUINA: 'mantenimiento', ALMUERZOS: 'otros',
  TRANSPORTE_PERSONAL: 'transporte', COMBUSTIBLE: 'transporte', PAPELERIA: 'papeleria',
  SERVICIOS_PUBLICOS: 'servicios', ARRIENDO: 'servicios', IMPUESTOS: 'otros', OTRO: 'otros',
}
const METODO_PAGO = [
  ['EFECTIVO', 'Efectivo'], ['TRANSFERENCIA_BANCOLOMBIA', 'Transferencia Bancolombia'],
  ['TRANSFERENCIA_OTRO_BANCO', 'Transferencia otro banco'], ['TARJETA_CREDITO', 'Tarjeta de crédito'],
  ['TARJETA_DEBITO', 'Tarjeta débito'], ['CHEQUE', 'Cheque'],
]

// ── Imputar gasto a la obra (POST /gastos con obra_id — router de caja, no sub-recurso de obra) ────────
function FormImputarGasto({ obraId, onHecho, onCancelar }) {
  const [f, setF] = useState({ concepto: '', monto: '', categoria_gasto: 'OTRO', metodo_pago: 'EFECTIVO', numero_referencia: '' })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF((p) => ({ ...p, [k]: e.target.value }))

  async function guardar() {
    if (!f.concepto.trim()) { toast.error('Describe el gasto'); return }
    if (!(n(f.monto) > 0)) { toast.error('El monto debe ser mayor que cero'); return }
    const payload = {
      obra_id: obraId,                                        // imputa el gasto a la obra (sigue siendo gasto de caja)
      categoria: CATEGORIA_POS[f.categoria_gasto] || 'otros', // taxonomía POS (NOT NULL) derivada del vertical
      concepto: f.concepto.trim(),
      monto: String(n(f.monto)),                              // STRING → Decimal exacto en el backend
      categoria_gasto: f.categoria_gasto,                     // taxonomía del vertical construcción (spec 09)
      metodo_pago: f.metodo_pago,
      numero_referencia: f.numero_referencia.trim() || null,
    }
    setEnviando(true)
    try {
      const res = await api('/gastos', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (!res.ok) {
        // 409 = no hay caja abierta: el gasto postea SU egreso de caja (invariante), así que exige caja.
        toast.error(res.status === 409 ? 'Abre la caja antes de imputar un gasto' : 'No se pudo imputar el gasto')
        return
      }
      toast.success('Gasto imputado a la obra')
      onHecho()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <MarcoForm titulo="Imputar gasto a la obra" onCancelar={onCancelar} enviando={enviando} onGuardar={guardar} textoGuardar="Guardar gasto">
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        <Campo label="Concepto" requerido className="sm:col-span-2">
          <Input value={f.concepto} onChange={set('concepto')} placeholder="Ej. Combustible retroexcavadora" className="h-8" />
        </Campo>
        <Campo label="Monto" requerido>
          <Input type="number" min="0" step="0.01" value={f.monto} onChange={set('monto')} className="h-8 text-right" />
        </Campo>
        <Campo label="Categoría">
          <select value={f.categoria_gasto} onChange={set('categoria_gasto')} className={SELECT_COMPACTO}>
            {CATEGORIA_GASTO.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Campo>
        <Campo label="Método de pago">
          <select value={f.metodo_pago} onChange={set('metodo_pago')} className={SELECT_COMPACTO}>
            {METODO_PAGO.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Campo>
        <Campo label="N.º de referencia" className="sm:col-span-2" hint="Comprobante o transacción (opcional)">
          <Input value={f.numero_referencia} onChange={set('numero_referencia')} className="h-8" />
        </Campo>
      </div>
    </MarcoForm>
  )
}

// ── Registrar horas de máquina (POST /maquinas/{maquina_id}/horas con obra_id — router de maquinaria) ──
function FormRegistrarHoras({ obraId, onHecho, onCancelar }) {
  const maquinasQ = useFetch('/maquinas')   // catálogo Fase 1; el backend tarifa por la asignación activa
  const maquinas = (Array.isArray(maquinasQ.data) ? maquinasQ.data : []).filter((m) => m.estado !== 'BAJA')
  const [f, setF] = useState({ maquina_id: '', fecha: hoyCO(), horas_trabajadas: '', observaciones: '' })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF((p) => ({ ...p, [k]: e.target.value }))

  async function guardar() {
    if (!f.maquina_id) { toast.error('Elige la máquina'); return }
    if (!(n(f.horas_trabajadas) > 0)) { toast.error('Las horas trabajadas deben ser mayores que cero'); return }
    const payload = {
      obra_id: obraId,                                  // la obra viaja en el cuerpo; la máquina, en la ruta
      fecha: f.fecha,
      horas_trabajadas: String(n(f.horas_trabajadas)),  // STRING → Decimal exacto (las horas no se redondean)
      observaciones: f.observaciones.trim() || null,
    }
    setEnviando(true)
    try {
      const res = await api(`/maquinas/${Number(f.maquina_id)}/horas`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (!res.ok) {
        // 409 = no hay asignación activa de esa máquina a la obra que cubra la fecha → no se puede tarifar.
        toast.error(res.status === 409 ? 'La máquina no está asignada a esta obra' : 'No se pudieron registrar las horas')
        return
      }
      toast.success(mensajeHoras(await res.json().catch(() => ({}))))
      onHecho()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <MarcoForm titulo="Registrar horas de máquina" onCancelar={onCancelar} enviando={enviando} onGuardar={guardar} textoGuardar="Guardar horas">
      {maquinasQ.loading ? (
        <p className="py-4 text-center text-[12px] text-muted-foreground">Cargando máquinas…</p>
      ) : maquinas.length === 0 ? (
        <p className="rounded-md bg-surface px-3 py-3 text-[12px] text-muted-foreground">No hay máquinas registradas. Da de alta una máquina y asígnala a la obra antes de registrar horas.</p>
      ) : (
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
          <Campo label="Máquina" requerido className="sm:col-span-3">
            <select value={f.maquina_id} onChange={set('maquina_id')} className={SELECT_COMPACTO}>
              <option value="">Elige una máquina…</option>
              {maquinas.map((m) => <option key={m.id} value={m.id}>{m.codigo} · {m.nombre}</option>)}
            </select>
          </Campo>
          <Campo label="Horas trabajadas" requerido hint="Se factura el máximo entre esto y el mínimo pactado">
            <Input type="number" min="0" step="0.25" value={f.horas_trabajadas} onChange={set('horas_trabajadas')} className="h-8 text-right" />
          </Campo>
          <Campo label="Fecha">
            <Input type="date" value={f.fecha} onChange={set('fecha')} className="h-8" />
          </Campo>
          <Campo label="Observaciones">
            <Input value={f.observaciones} onChange={set('observaciones')} placeholder="Opcional" className="h-8" />
          </Campo>
        </div>
      )}
    </MarcoForm>
  )
}

// Aviso tras registrar horas desde `RegistroHorasResultado`: "6h facturables. Mínimo cubierto. Ingreso
// $900.000." Armado defensivo (cualquier campo puede faltar). `replay=true` = el parte de ese día ya
// existía (idempotencia por clave natural máquina·obra·fecha): no se duplicó.
function mensajeHoras(r) {
  const partes = []
  if (r.horas_facturables != null) partes.push(`${num(r.horas_facturables)}h facturables`)
  if (r.minimo_cubierto != null) partes.push(r.minimo_cubierto ? 'mínimo cubierto' : 'por debajo del mínimo')
  if (r.ingreso != null) partes.push(`ingreso ${cop(r.ingreso)}`)
  const base = r.replay ? 'Horas ya registradas para ese día' : 'Horas registradas'
  return partes.length ? `${base}. ${partes.join('. ')}.` : `${base}.`
}

// ── Registrar consumo de inventario (POST /obras/{id}/consumos) ───────────────────────────────────────
function FormRegistrarConsumo({ obraId, onHecho, onCancelar }) {
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(null)   // producto elegido { id, nombre }
  // Busca en el catálogo solo con 2+ caracteres (path falsy = hook en reposo, sin llamadas de más).
  const prodQ = useFetch(q.trim().length >= 2 ? `/productos?q=${encodeURIComponent(q.trim())}` : null)
  const productos = Array.isArray(prodQ.data) ? prodQ.data.slice(0, 6) : []
  const [f, setF] = useState({ cantidad: '', costo_unitario: '', fecha: hoyCO(), observaciones: '' })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF((p) => ({ ...p, [k]: e.target.value }))

  function elegir(p) { setSel({ id: p.id, nombre: p.nombre }); setQ('') }

  async function guardar() {
    if (!sel) { toast.error('Elige un producto del catálogo'); return }
    if (!(n(f.cantidad) > 0)) { toast.error('La cantidad debe ser mayor que cero'); return }
    if (!(n(f.costo_unitario) > 0)) { toast.error('El costo unitario debe ser mayor que cero'); return }
    const payload = {
      producto_id: sel.id,
      cantidad: String(n(f.cantidad)),
      costo_unitario: String(n(f.costo_unitario)),
      fecha: f.fecha,
      observaciones: f.observaciones.trim() || null,
    }
    setEnviando(true)
    try {
      const res = await api(`/obras/${obraId}/consumos`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (!res.ok) { toast.error('No se pudo registrar el consumo'); return }
      toast.success('Consumo de inventario registrado')
      onHecho()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  const total = n(f.cantidad) * n(f.costo_unitario)

  return (
    <MarcoForm titulo="Registrar consumo de inventario" onCancelar={onCancelar} enviando={enviando} onGuardar={guardar} textoGuardar="Guardar consumo">
      <Campo label="Producto del catálogo" requerido>
        {sel ? (
          <div className="flex h-8 items-center justify-between gap-2 rounded-md border border-input bg-surface px-2">
            <span className="truncate text-[12px] text-foreground">{sel.nombre}</span>
            <button type="button" onClick={() => setSel(null)} className="text-[11px] text-muted-foreground hover:text-foreground">Cambiar</button>
          </div>
        ) : (
          <div className="relative">
            <Search className="size-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar material por nombre o código…" className="h-8 pl-8" />
          </div>
        )}
      </Campo>

      {!sel && q.trim().length >= 2 && (
        <ul className="mt-1 max-h-40 overflow-y-auto rounded-md border border-border-subtle bg-surface">
          {prodQ.loading ? (
            <li className="px-3 py-2 text-[12px] text-muted-foreground">Buscando…</li>
          ) : productos.length === 0 ? (
            <li className="px-3 py-2 text-[12px] text-muted-foreground">Sin coincidencias en el catálogo.</li>
          ) : productos.map((p) => (
            <li key={p.id}>
              <button type="button" onClick={() => elegir(p)} className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-[12px] hover:bg-surface-2">
                <span className="truncate text-secondary-foreground">{p.nombre}</span>
                {p.codigo && <span className="tabular text-[11px] text-muted-foreground">{p.codigo}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-2.5 grid grid-cols-1 gap-2.5 sm:grid-cols-3">
        <Campo label="Cantidad" requerido>
          <Input type="number" min="0" step="0.01" value={f.cantidad} onChange={set('cantidad')} className="h-8 text-right" />
        </Campo>
        <Campo label="Costo unitario" requerido>
          <Input type="number" min="0" step="0.01" value={f.costo_unitario} onChange={set('costo_unitario')} className="h-8 text-right" />
        </Campo>
        <Campo label="Fecha">
          <Input type="date" value={f.fecha} onChange={set('fecha')} className="h-8" />
        </Campo>
      </div>
      {total > 0 && (
        <p className="mt-2 text-right text-[11px] text-muted-foreground">Costo del consumo: <span className="tabular font-medium text-foreground">{cop(total)}</span></p>
      )}
    </MarcoForm>
  )
}

// ── Liquidar la obra (POST /obras/{id}/liquidar) — irreversible, solo si FINALIZADA. ─────────────────
function BarraLiquidar({ obra, finalizada, onHecho }) {
  const [ocupado, setOcupado] = useState(false)

  async function liquidar() {
    if (!window.confirm(`Liquidar "${obra.nombre}" congela su gasto real en un snapshot inmutable y la cierra definitivamente. ¿Continuar?`)) return
    setOcupado(true)
    try {
      const res = await api(`/obras/${obra.id}/liquidar`, { method: 'POST' })
      if (res.ok) { toast.success('Obra liquidada'); onHecho() }
      else if (res.status === 409) { toast.message('La obra ya estaba liquidada'); onHecho() }
      else toast.error('No se pudo liquidar la obra')
    } catch { toast.error('Error de conexión') } finally { setOcupado(false) }
  }

  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-border-subtle pt-3">
      <p className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <CheckCircle2 className="size-3.5 shrink-0" aria-hidden="true" />
        {finalizada ? 'La obra está finalizada: puedes cerrarla y congelar su rentabilidad.' : 'Finaliza la obra para poder liquidarla.'}
      </p>
      <button type="button" onClick={liquidar} disabled={!finalizada || ocupado}
        className={`${BTN_PRIMARY} h-8`} title={finalizada ? 'Liquidar la obra' : 'Solo se liquida una obra finalizada'}>
        <Lock className="size-3.5" aria-hidden="true" /> {ocupado ? 'Liquidando…' : 'Liquidar obra'}
      </button>
    </div>
  )
}

// Placeholder de carga del panel (skeleton), no un spinner suelto.
function EsqueletoPanel() {
  return (
    <div className="animate-pulse space-y-3" aria-hidden="true">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[auto_1fr]">
        <div className="h-14 w-40 rounded-lg bg-surface-2" />
        <div className="grid grid-cols-3 gap-2">
          <div className="h-14 rounded-md bg-surface-2" />
          <div className="h-14 rounded-md bg-surface-2" />
          <div className="h-14 rounded-md bg-surface-2" />
        </div>
      </div>
      <div className="h-3 rounded-full bg-surface-2" />
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-4 rounded bg-surface-2" />)}
      </div>
    </div>
  )
}
