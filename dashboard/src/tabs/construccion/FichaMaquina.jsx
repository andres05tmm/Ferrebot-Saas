/*
 * FichaMaquina — ficha rica de una máquina, montada como EXPANSIÓN de fila en TabMaquinas (mismo patrón
 * fila-expandible que TabObras → ObraDetalle). Convierte la lista CRUD plana en un tablero de operación
 * por activo: qué obra la tiene, cuántas horas facturó, cuánto rindió y cuándo toca mantenimiento.
 *
 * Contrato de API (solo lectura salvo el alta de mantenimiento y el cambio de estado):
 *   GET  /maquinas/{id}/asignaciones  → [{ id, maquina_id, obra_id, fecha_inicio, fecha_fin, precio_hora,
 *                                          minimo_horas, operador_id, activa }]           (fecha_inicio DESC)
 *   GET  /maquinas/{id}/horas?limite=30 → [{ id, obra_id, fecha, horas_trabajadas, horas_facturables,
 *                                            origen_registro, turnos: [...], ... }]         (fecha DESC)
 *   POST /maquinas/{id}/horas (cualquier rol, captura de campo) → registra parte/turno; el kárdex tiene el form.
 *   GET  /maquinas/{id}/mantenimientos → [{ id, tipo, fecha, descripcion, costo, horas_maquina,
 *                                           proximo_en_horas, proximo_en_fecha, ... }]     (fecha DESC)
 *   POST /maquinas/{id}/mantenimientos (admin, 201) · PATCH /maquinas/{id} (admin, cambio de estado).
 *
 * Reglas de negocio replicadas en cliente sobre los datos YA cargados (sin llamadas extra):
 *   - Ingreso por parte = horas_facturables × precio_hora de la asignación que cubre (obra, fecha). Misma
 *     resolución que `asignacion_activa` del backend: la de fecha_inicio más reciente que cubre la fecha.
 *   - Mantenimiento VENCIDO: proximo_en_fecha pasada · u horas acumuladas del kárdex (Σ horas_trabajadas
 *     con fecha posterior al servicio) ≥ proximo_en_horas.  PRÓXIMO: ≤7 días · o ≥80% del horómetro.
 *
 * RBAC: el TOTAL del ingreso y el costo interno/hora solo se pintan para admin. Errores por sección son
 * silenciosos (cada bloque degrada a su vacío) para no tumbar la ficha entera. Dinero llega como STRING
 * decimal → `cop()`; horas → `num()`. Todo por tokens semánticos (dark-mode-safe, white-label).
 */
import { Fragment, useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  Building2, Clock, Wrench, Plus, Bot, TriangleAlert, CalendarClock,
  Gauge, Pencil, Trash2, Coins,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch, cop, num } from '@/components/shared.jsx'
import { Semaforo, Campo, BTN_PRIMARY, BTN_OUTLINE, SELECT_CLS } from './comunes.jsx'
import FormAsignacionMaquina from './calendario/FormAsignacionMaquina.jsx'
import FormRegistroHoras from './calendario/FormRegistroHoras.jsx'
import TurnosSublineas from './calendario/Turnos.jsx'

// Etiquetas humanas del tipo de mantenimiento (enum del ORM) y del estado de máquina. Se mantienen
// locales para que la ficha sea autocontenida (y testeable) sin acoplarse al mapa de TabMaquinas.
const TIPO_MANT = { PREVENTIVO: 'Preventivo', CORRECTIVO: 'Correctivo', INSPECCION: 'Inspección' }
const ESTADO_MAQ = [
  ['DISPONIBLE', 'Disponible'], ['OCUPADA', 'En obra'], ['MANTENIMIENTO', 'Mantenimiento'],
  ['DAÑADA', 'Dañada'], ['BAJA', 'De baja'],
]

const n = (v) => { const x = Number(v); return Number.isFinite(x) ? x : 0 }

// Fecha de HOY en hora Colombia (YYYY-MM-DD). Las fechas del backend son 'YYYY-MM-DD', así que las
// comparaciones lexicográficas coinciden con las cronológicas (regla #4: nunca `date.today()` crudo).
function hoyCO() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
}
function diasEntre(desde, hasta) {
  const a = new Date(`${desde}T00:00:00`), b = new Date(`${hasta}T00:00:00`)
  return Math.round((b - a) / 86_400_000)
}

// Asignación que cubre (obra, fecha) de un parte. Las asignaciones vienen fecha_inicio DESC, así que el
// primer match respeta el desempate del backend (la más reciente que cubre la fecha).
function asignacionDe(registro, asignaciones) {
  return asignaciones.find((a) =>
    a.obra_id === registro.obra_id &&
    a.fecha_inicio <= registro.fecha &&
    (!a.fecha_fin || registro.fecha <= a.fecha_fin),
  ) || null
}

// Estado de un mantenimiento frente a hoy + horas acumuladas del kárdex: 'vencido' | 'proximo' | null.
function estadoMant(m, horasAcum, hoy) {
  const porFecha = !!m.proximo_en_fecha
  const porHoras = m.proximo_en_horas != null && n(m.proximo_en_horas) > 0
  const vencido = (porFecha && m.proximo_en_fecha < hoy) || (porHoras && horasAcum >= n(m.proximo_en_horas))
  if (vencido) return 'vencido'
  const proximo =
    (porFecha && diasEntre(hoy, m.proximo_en_fecha) <= 7) ||
    (porHoras && horasAcum >= 0.8 * n(m.proximo_en_horas))
  return proximo ? 'proximo' : null
}

export default function FichaMaquina({ id, maquina, isAdmin = false, obrasNombre = {}, onEditar, onCambio }) {
  const asignacionesQ = useFetch(`/maquinas/${maquina.id}/asignaciones`)
  const horasQ = useFetch(`/maquinas/${maquina.id}/horas?limite=30`)
  const mantsQ = useFetch(`/maquinas/${maquina.id}/mantenimientos`)

  const asignaciones = Array.isArray(asignacionesQ.data) ? asignacionesQ.data : []
  const horas = Array.isArray(horasQ.data) ? horasQ.data : []
  const mants = Array.isArray(mantsQ.data) ? mantsQ.data : []

  const obraLabel = (obraId) => obrasNombre?.[obraId] || `Obra #${obraId}`

  return (
    <div id={id} className="border-t border-border-subtle bg-surface-2/40 px-4 py-3.5 space-y-4">
      <Cabecera maquina={maquina} isAdmin={isAdmin} onEditar={onEditar} onCambio={onCambio} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SeccionAsignaciones
          q={asignacionesQ} asignaciones={asignaciones} obraLabel={obraLabel} hoy={hoyCO()}
          maquina={maquina} isAdmin={isAdmin} onCreado={asignacionesQ.refetch}
        />
        <SeccionMantenimientos
          q={mantsQ} mants={mants} horas={horas} maquinaId={maquina.id} isAdmin={isAdmin}
          onCreado={mantsQ.refetch}
        />
      </div>

      <SeccionKardex
        q={horasQ} horas={horas} asignaciones={asignaciones} obraLabel={obraLabel} isAdmin={isAdmin}
        maquina={maquina} onRegistrado={horasQ.refetch}
      />
    </div>
  )
}

// ── Cabecera: especificaciones + acciones admin ────────────────────────────────────────────────────
function Cabecera({ maquina, isAdmin, onEditar, onCambio }) {
  const [ocupado, setOcupado] = useState(false)

  async function cambiarEstado(e) {
    const nuevo = e.target.value
    if (nuevo === maquina.estado) return
    setOcupado(true)
    try {
      const res = await api(`/maquinas/${maquina.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ estado: nuevo }),
      })
      if (!res.ok) { toast.error('No se pudo cambiar el estado'); return }
      toast.success('Estado actualizado')
      onCambio?.()
    } catch { toast.error('Error de conexión') } finally { setOcupado(false) }
  }

  async function eliminar() {
    if (!window.confirm(`¿Dar de baja "${maquina.nombre}"? Dejará de aparecer en el parque.`)) return
    try {
      const res = await api(`/maquinas/${maquina.id}`, { method: 'DELETE' })
      if (res.ok) { toast.success('Máquina dada de baja'); onCambio?.() }
      else toast.error('No se pudo dar de baja la máquina')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      {/* Especificaciones: la tarifa manda (numeral Oswald), el resto en fila fina. */}
      <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
        <Spec etiqueta="Tarifa / hora" destacado>
          {cop(n(maquina.precio_hora_default))}<span className="text-[11px] font-normal text-muted-foreground">/h</span>
        </Spec>
        <Spec etiqueta="Mínimo facturable">
          <span className="inline-flex items-center gap-1"><Gauge className="size-3.5 text-muted-foreground" aria-hidden="true" />{num(maquina.minimo_horas_factura)} h</span>
        </Spec>
        {isAdmin && maquina.costo_operacion_hora != null && (
          <Spec etiqueta="Costo interno / hora">{cop(n(maquina.costo_operacion_hora))}<span className="text-[11px] font-normal text-muted-foreground">/h</span></Spec>
        )}
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Ficha técnica</span>
          <span className="flex flex-wrap items-center gap-x-2 text-[12px] text-secondary-foreground">
            <span className="tabular font-medium">{maquina.codigo}</span>
            {maquina.tipo && <span className="text-muted-foreground">· {maquina.tipo}</span>}
            {maquina.placa && <span className="text-muted-foreground">· {maquina.placa}</span>}
            {maquina.serial && <span className="text-muted-foreground">· S/N {maquina.serial}</span>}
            {maquina.anio_fabricacion && <span className="text-muted-foreground">· {maquina.anio_fabricacion}</span>}
          </span>
        </div>
      </div>

      {isAdmin && (
        <div className="flex flex-wrap items-center gap-1.5">
          <label className="text-[11px] text-muted-foreground" htmlFor={`estado-${maquina.id}`}>Estado:</label>
          <select
            id={`estado-${maquina.id}`} value={maquina.estado} onChange={cambiarEstado} disabled={ocupado}
            className={`${SELECT_CLS.replace('w-full', 'w-44')} h-8`}
          >
            {ESTADO_MAQ.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <button onClick={onEditar} className={`${BTN_OUTLINE} h-8`}><Pencil className="size-3.5" /> Editar</button>
          <button
            onClick={eliminar}
            className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-border bg-surface px-3 text-sm font-medium text-destructive transition-colors duration-fast hover:bg-destructive/10"
          >
            <Trash2 className="size-3.5" /> Baja
          </button>
        </div>
      )}
    </div>
  )
}

function Spec({ etiqueta, destacado = false, children }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{etiqueta}</span>
      <span className={destacado
        ? 'font-display tabular text-[22px] font-semibold leading-none text-foreground'
        : 'tabular text-[14px] font-semibold text-foreground'}>
        {children}
      </span>
    </div>
  )
}

// ── Panel de sección reutilizable (borde + header uppercase + acción opcional) ──────────────────────
function Panel({ icono: Icono, titulo, conteo, accion, children }) {
  return (
    <section className="rounded-md border border-border-subtle bg-surface p-3">
      <div className="mb-2.5 flex items-center gap-2">
        <Icono className="size-4 text-muted-foreground" aria-hidden="true" />
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{titulo}</h3>
        {typeof conteo === 'number' && conteo > 0 && <span className="tabular text-[11px] text-muted-foreground">· {conteo}</span>}
        {accion && <div className="ml-auto">{accion}</div>}
      </div>
      {children}
    </section>
  )
}

function Vacio({ children }) {
  return <p className="py-5 text-center text-[12px] text-muted-foreground">{children}</p>
}
function Cargando() {
  return <div className="h-16 animate-pulse rounded bg-surface-2" aria-hidden="true" />
}

// ── Asignaciones a obra ─────────────────────────────────────────────────────────────────────────────
function SeccionAsignaciones({ q, asignaciones, obraLabel, hoy, maquina, isAdmin = false, onCreado }) {
  const [abrirForm, setAbrirForm] = useState(false)
  const accion = isAdmin
    ? (
      <button onClick={() => setAbrirForm((v) => !v)} className={`${BTN_OUTLINE} h-7 px-2 text-[12px]`} aria-expanded={abrirForm}>
        <Plus className="size-3.5" /> Asignar
      </button>
    )
    : null

  return (
    <Panel icono={Building2} titulo="Asignaciones a obra" conteo={asignaciones.length} accion={accion}>
      {isAdmin && abrirForm && (
        <FormAsignacionMaquina
          maquinaFija={maquina}
          onExito={() => { setAbrirForm(false); onCreado?.() }}
          onCancelar={() => setAbrirForm(false)}
        />
      )}
      {q.loading ? <Cargando />
        : asignaciones.length === 0 ? <Vacio>Esta máquina no está asignada a ninguna obra todavía.</Vacio>
          : (
            <div className="-mx-1 overflow-x-auto">
              <table className="w-full min-w-[380px] text-[12px]">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wider text-muted-foreground">
                    <th className="px-1 pb-1 font-medium">Obra</th>
                    <th className="px-1 pb-1 font-medium">Vigencia</th>
                    <th className="px-1 pb-1 text-right font-medium">$/h pactado</th>
                    <th className="px-1 pb-1 text-right font-medium">Mín.</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {asignaciones.map((a) => {
                    const vigente = a.activa && a.fecha_inicio <= hoy && (!a.fecha_fin || hoy <= a.fecha_fin)
                    return (
                      <tr key={a.id} className="text-secondary-foreground">
                        <td className="px-1 py-1.5">
                          <span className="flex items-center gap-1.5">
                            <span className="truncate">{obraLabel(a.obra_id)}</span>
                            {vigente && <Semaforo tono="verde">Activa</Semaforo>}
                          </span>
                        </td>
                        <td className="tabular px-1 py-1.5 text-muted-foreground">
                          {a.fecha_inicio}{a.fecha_fin ? ` → ${a.fecha_fin}` : ' →'}
                        </td>
                        <td className="tabular px-1 py-1.5 text-right font-medium text-foreground">{cop(n(a.precio_hora))}</td>
                        <td className="tabular px-1 py-1.5 text-right text-muted-foreground">{num(a.minimo_horas)} h</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
    </Panel>
  )
}

// ── Kárdex de horas (con ingreso por parte; total solo admin) ────────────────────────────────────────
// Cada parte con ROTACIÓN de operadores lleva una sub-fila con el desglose de turnos (quién · franja · h);
// el total del día vive en la fila principal. Registrar horas está abierto a todos los roles (captura de campo).
function SeccionKardex({ q, horas, asignaciones, obraLabel, isAdmin, maquina, onRegistrado }) {
  const [abrir, setAbrir] = useState(false)
  const totalIngreso = useMemo(() => horas.reduce((acc, r) => {
    const a = asignacionDe(r, asignaciones)
    return acc + (a ? n(r.horas_facturables) * n(a.precio_hora) : 0)
  }, 0), [horas, asignaciones])

  const totalPill = isAdmin && horas.length > 0
    ? (
      <span className="inline-flex items-center gap-1 rounded-full bg-primary-soft px-2 py-0.5 text-[11px] font-semibold text-primary">
        <Coins className="size-3" aria-hidden="true" /> Total facturado {cop(totalIngreso)}
      </span>
    )
    : null
  const accion = (
    <div className="flex items-center gap-2">
      {totalPill}
      <button onClick={() => setAbrir((v) => !v)} className={`${BTN_OUTLINE} h-7 px-2 text-[12px]`} aria-expanded={abrir}>
        <Plus className="size-3.5" /> Registrar horas
      </button>
    </div>
  )

  return (
    <Panel icono={Clock} titulo="Kárdex de horas" conteo={horas.length} accion={accion}>
      {abrir && (
        <FormRegistroHoras
          maquinaFija={maquina}
          onExito={() => { setAbrir(false); onRegistrado?.() }}
          onCancelar={() => setAbrir(false)}
        />
      )}
      {q.loading ? <Cargando />
        : horas.length === 0 ? <Vacio>Sin partes de horas. Llegan del bot de campo o se registran contra la obra.</Vacio>
          : (
            <div className="-mx-1 overflow-x-auto">
              <table className="w-full min-w-[460px] text-[12px]">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wider text-muted-foreground">
                    <th className="px-1 pb-1 font-medium">Fecha</th>
                    <th className="px-1 pb-1 font-medium">Obra</th>
                    <th className="px-1 pb-1 text-right font-medium">H. trab.</th>
                    <th className="px-1 pb-1 text-right font-medium">H. fact.</th>
                    <th className="px-1 pb-1 text-right font-medium">Ingreso</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {horas.map((r) => {
                    const a = asignacionDe(r, asignaciones)
                    const ingreso = a ? n(r.horas_facturables) * n(a.precio_hora) : null
                    const turnos = Array.isArray(r.turnos) ? r.turnos : []
                    return (
                      <Fragment key={r.id}>
                        <tr className="text-secondary-foreground">
                          <td className="tabular px-1 py-1.5 whitespace-nowrap">
                            <span className="inline-flex items-center gap-1.5">
                              {r.fecha}
                              {r.origen_registro === 'TELEGRAM_BOT' && (
                                <span className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground" title="Registrado por el bot de campo">
                                  <Bot className="size-3" aria-hidden="true" /> bot
                                </span>
                              )}
                            </span>
                          </td>
                          <td className="px-1 py-1.5"><span className="block max-w-[160px] truncate">{obraLabel(r.obra_id)}</span></td>
                          <td className="tabular px-1 py-1.5 text-right text-muted-foreground">{num(r.horas_trabajadas)}</td>
                          <td className="tabular px-1 py-1.5 text-right font-medium text-foreground">{num(r.horas_facturables)}</td>
                          <td className="tabular px-1 py-1.5 text-right text-foreground">{ingreso == null ? '—' : cop(ingreso)}</td>
                        </tr>
                        {turnos.length > 0 && (
                          <tr className="text-secondary-foreground">
                            <td colSpan={5} className="px-1 pb-1.5 pt-0"><TurnosSublineas turnos={turnos} /></td>
                          </tr>
                        )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
    </Panel>
  )
}

// ── Mantenimientos (lista + badge vencido/próximo + form inline admin) ───────────────────────────────
function SeccionMantenimientos({ q, mants, horas, maquinaId, isAdmin, onCreado }) {
  const [abrirForm, setAbrirForm] = useState(false)
  const hoy = hoyCO()

  // Horas acumuladas del kárdex desde cada servicio (Σ horas_trabajadas con fecha posterior), calculado
  // una vez sobre los datos ya cargados. Aproximación acotada al kárdex traído (últimos ~30 partes).
  const horasAcumPorMant = useMemo(() => {
    const map = {}
    for (const m of mants) {
      map[m.id] = horas.reduce((acc, r) => acc + (r.fecha > m.fecha ? n(r.horas_trabajadas) : 0), 0)
    }
    return map
  }, [mants, horas])

  const accion = isAdmin
    ? (
      <button onClick={() => setAbrirForm((v) => !v)} className={`${BTN_OUTLINE} h-7 px-2 text-[12px]`} aria-expanded={abrirForm}>
        <Plus className="size-3.5" /> Registrar
      </button>
    )
    : null

  return (
    <Panel icono={Wrench} titulo="Mantenimientos" conteo={mants.length} accion={accion}>
      {isAdmin && abrirForm && (
        <FormMantenimiento
          maquinaId={maquinaId} hoy={hoy}
          onCancelar={() => setAbrirForm(false)}
          onCreado={() => { setAbrirForm(false); onCreado?.() }}
        />
      )}
      {q.loading ? <Cargando />
        : mants.length === 0 ? <Vacio>Sin mantenimientos registrados. Programa el preventivo por horómetro o fecha.</Vacio>
          : (
            <ul className="space-y-2">
              {mants.map((m) => (
                <MantItem key={m.id} m={m} estado={estadoMant(m, horasAcumPorMant[m.id] || 0, hoy)} />
              ))}
            </ul>
          )}
    </Panel>
  )
}

function MantItem({ m, estado }) {
  return (
    <li className="rounded-md border border-border-subtle bg-surface-2/50 px-2.5 py-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-[12px] font-semibold text-foreground">{TIPO_MANT[m.tipo] || m.tipo}</span>
        <span className="tabular text-[11px] text-muted-foreground">{m.fecha}</span>
        {estado === 'vencido' && <Semaforo tono="rojo">Vencido</Semaforo>}
        {estado === 'proximo' && <Semaforo tono="ambar">Próximo</Semaforo>}
        <span className="tabular ml-auto text-[12px] font-medium text-foreground">{cop(n(m.costo))}</span>
      </div>
      <p className="mt-1 text-[12px] leading-snug text-secondary-foreground">{m.descripcion}</p>
      {(m.proximo_en_fecha || m.proximo_en_horas != null || m.horas_maquina != null) && (
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
          {m.horas_maquina != null && <span className="inline-flex items-center gap-1"><Gauge className="size-3" aria-hidden="true" />{num(m.horas_maquina)} h horómetro</span>}
          {m.proximo_en_fecha && <span className="inline-flex items-center gap-1"><CalendarClock className="size-3" aria-hidden="true" />próximo {m.proximo_en_fecha}</span>}
          {m.proximo_en_horas != null && <span className="inline-flex items-center gap-1"><TriangleAlert className="size-3" aria-hidden="true" />cada {num(m.proximo_en_horas)} h</span>}
        </div>
      )}
    </li>
  )
}

const FORM_INICIAL = {
  tipo: 'PREVENTIVO', fecha: '', descripcion: '', costo: '', horas_maquina: '',
  proximo_en_fecha: '', proximo_en_horas: '',
}

function FormMantenimiento({ maquinaId, hoy, onCancelar, onCreado }) {
  const [f, setF] = useState({ ...FORM_INICIAL, fecha: hoy })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF((prev) => ({ ...prev, [k]: e.target.value }))

  async function guardar() {
    if (!f.descripcion.trim()) { toast.error('Describe el mantenimiento'); return }
    const payload = {
      tipo: f.tipo,
      descripcion: f.descripcion.trim(),
      costo: f.costo ? Number(f.costo) : 0,
      ...(f.fecha ? { fecha: f.fecha } : {}),
      ...(f.horas_maquina ? { horas_maquina: Number(f.horas_maquina) } : {}),
      ...(f.proximo_en_fecha ? { proximo_en_fecha: f.proximo_en_fecha } : {}),
      ...(f.proximo_en_horas ? { proximo_en_horas: Number(f.proximo_en_horas) } : {}),
    }
    setEnviando(true)
    try {
      const res = await api(`/maquinas/${maquinaId}/mantenimientos`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      if (!res.ok) { toast.error('No se pudo registrar el mantenimiento'); return }
      toast.success('Mantenimiento registrado')
      onCreado?.()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <div className="mb-3 rounded-md border border-border bg-surface-2/60 p-3">
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        <Campo label="Tipo">
          <select value={f.tipo} onChange={set('tipo')} className={SELECT_CLS}>
            {Object.entries(TIPO_MANT).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Campo>
        <Campo label="Fecha">
          <input type="date" value={f.fecha} onChange={set('fecha')} className={SELECT_CLS} />
        </Campo>
        <Campo label="Descripción" requerido className="sm:col-span-2">
          <input value={f.descripcion} onChange={set('descripcion')} placeholder="Cambio de aceite y filtros" className={SELECT_CLS} />
        </Campo>
        <Campo label="Costo" hint="0 si no tuvo costo.">
          <input type="number" inputMode="numeric" value={f.costo} onChange={set('costo')} placeholder="0" className={`${SELECT_CLS} tabular`} />
        </Campo>
        <Campo label="Horómetro" hint="Horas de la máquina al servicio.">
          <input type="number" inputMode="numeric" value={f.horas_maquina} onChange={set('horas_maquina')} placeholder="Opcional" className={`${SELECT_CLS} tabular`} />
        </Campo>
        <Campo label="Próximo por fecha">
          <input type="date" value={f.proximo_en_fecha} onChange={set('proximo_en_fecha')} className={SELECT_CLS} />
        </Campo>
        <Campo label="Próximo por horas" hint="Preventivo: cada X horas.">
          <input type="number" inputMode="numeric" value={f.proximo_en_horas} onChange={set('proximo_en_horas')} placeholder="Opcional" className={`${SELECT_CLS} tabular`} />
        </Campo>
      </div>
      <div className="mt-3 flex items-center justify-end gap-2">
        <button onClick={onCancelar} className={`${BTN_OUTLINE} h-8`}>Cancelar</button>
        <button onClick={guardar} disabled={enviando} className={`${BTN_PRIMARY} h-8`}>
          {enviando ? 'Guardando…' : 'Registrar mantenimiento'}
        </button>
      </div>
    </div>
  )
}
