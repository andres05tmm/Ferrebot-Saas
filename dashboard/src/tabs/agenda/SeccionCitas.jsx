/*
 * SeccionCitas — vista de CALENDARIO del día (alta fidelidad, fuente: DESIGN.md + screen.png).
 * Columna de horas + una columna por recurso (GET /agenda/recursos); bloques de cita posicionados por
 * inicio/fin (GET /agenda/citas?desde&hasta), coloreados por estado. Panel lateral "Acción Requerida"
 * con los pendientes (Aprobar=confirmar / Rechazar=cancelar). Tiempo real: las citas del agente entran
 * en vivo (useRealtimeEvent). Todo el manejo de horas es en zona Colombia. Vista DÍA (semana: luego).
 */
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { CalendarPlus, ChevronLeft, ChevronRight, MessageCircle, Monitor, Check, X, AlertTriangle, BellRing } from 'lucide-react'
import { api } from '@/lib/api.js'
import { useFetch } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import {
  ESTADOS, ESTADO_ACCENT, EstadoBadge, ConfirmacionBadge, requiereAtencion,
  aISOColombia, fmtFechaCO, fmtHora, fmtDiaLabel, hoyCO, masDiasCO, minutosCO, sumarDias,
} from './util.jsx'

// Rejilla del día: 07:00–21:00, 64px por hora.
const HORA_INICIO = 7
const HORA_FIN = 21
const HORA_PX = 64
const HORAS = Array.from({ length: HORA_FIN - HORA_INICIO }, (_, i) => HORA_INICIO + i)
const ALTO_GRILLA = (HORA_FIN - HORA_INICIO) * HORA_PX

async function postAccion(path, body, okMsg, refetch) {
  try {
    const res = await api(path, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    if (res.ok) { toast.success(okMsg); refetch(); return true }
    if (res.status === 409) {
      const d = await res.json().catch(() => ({}))
      toast.error(typeof d?.detail === 'object' ? 'Ese horario no está disponible' : (d?.detail || 'No se pudo'))
    } else {
      toast.error('No se pudo completar la acción')
    }
  } catch { toast.error('Error de conexión') }
  return false
}

export default function SeccionCitas() {
  const [dia, setDia] = useState(hoyCO())
  const [estado, setEstado] = useState('')
  const [recursoId, setRecursoId] = useState('')
  const [creando, setCreando] = useState(false)

  const recursosQ = useFetch('/agenda/recursos')
  const serviciosQ = useFetch('/agenda/servicios')
  const recursos = Array.isArray(recursosQ.data) ? recursosQ.data : []
  const servicios = Array.isArray(serviciosQ.data) ? serviciosQ.data : []

  const citasQ = useFetch(
    `/agenda/citas?desde=${dia}&hasta=${dia}${recursoId ? `&recurso_id=${recursoId}` : ''}`,
    [dia, recursoId],
  )
  const pendQ = useFetch(`/agenda/citas?estado=pendiente&desde=${hoyCO()}&hasta=${masDiasCO(30)}`)

  const refrescar = () => { citasQ.refetch(); pendQ.refetch() }
  useRealtimeEvent(['cita_agendada', 'cita_estado', 'cita_reagendada', 'cita_confirmacion', 'reconnected'], refrescar)

  const nombreServicio = useMemo(() => Object.fromEntries(servicios.map(s => [s.id, s.nombre])), [servicios])
  const citasDia = (Array.isArray(citasQ.data) ? citasQ.data : []).filter(c => !estado || c.estado === estado)
  const pendientes = Array.isArray(pendQ.data) ? pendQ.data : []
  const recursosVisibles = recursoId ? recursos.filter(r => String(r.id) === String(recursoId)) : recursos

  return (
    <div className="flex flex-col gap-3">
      <BarraSuperior
        dia={dia} setDia={setDia}
        estado={estado} setEstado={setEstado}
        recursoId={recursoId} setRecursoId={setRecursoId}
        recursos={recursos} onNueva={() => setCreando(v => !v)}
      />

      {creando && (
        <NuevaCitaForm
          servicios={servicios} recursos={recursos}
          onClose={() => setCreando(false)} onCreada={() => { setCreando(false); refrescar() }}
        />
      )}

      <div className="flex flex-col xl:flex-row gap-3 items-start">
        <Card className="flex-1 w-full p-0 overflow-hidden shadow-sm">
          <Calendario
            loading={citasQ.loading || recursosQ.loading}
            recursos={recursosVisibles} citas={citasDia} nombreServicio={nombreServicio} dia={dia}
          />
        </Card>
        <AccionRequerida pendientes={pendientes} nombreServicio={nombreServicio} refrescar={refrescar} />
      </div>
    </div>
  )
}

// ── Barra superior: navegación de fecha + filtros ────────────────────────────
function BarraSuperior({ dia, setDia, estado, setEstado, recursoId, setRecursoId, recursos, onNueva }) {
  return (
    <Card className="p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold mr-1">Agenda</h2>
        <div className="inline-flex items-center rounded-md border border-border overflow-hidden">
          <button onClick={() => setDia(sumarDias(dia, -1))} aria-label="Día anterior" className="h-9 px-2 hover:bg-surface-2 text-muted-foreground">
            <ChevronLeft className="size-4" />
          </button>
          <button onClick={() => setDia(hoyCO())} aria-label="Hoy" className="h-9 px-3 text-sm font-medium border-x border-border min-w-[140px] text-center capitalize">
            {fmtDiaLabel(dia)}
          </button>
          <button onClick={() => setDia(sumarDias(dia, 1))} aria-label="Día siguiente" className="h-9 px-2 hover:bg-surface-2 text-muted-foreground">
            <ChevronRight className="size-4" />
          </button>
        </div>

        <select value={estado} onChange={e => setEstado(e.target.value)} aria-label="Estado"
          className="h-9 px-2 rounded-md border border-border bg-surface text-sm capitalize">
          <option value="">Estado: todos</option>
          {ESTADOS.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={recursoId} onChange={e => setRecursoId(e.target.value)} aria-label="Recurso"
          className="h-9 px-2 rounded-md border border-border bg-surface text-sm">
          <option value="">Profesional: todos</option>
          {recursos.map(r => <option key={r.id} value={r.id}>{r.nombre}</option>)}
        </select>

        <Button onClick={onNueva} className="ml-auto h-9">
          <CalendarPlus className="size-4" /> Nueva cita
        </Button>
      </div>
    </Card>
  )
}

// ── Calendario: columna de horas + columnas por recurso ──────────────────────
function Calendario({ loading, recursos, citas, nombreServicio, dia }) {
  const porRecurso = useMemo(() => {
    const m = {}
    for (const c of citas) (m[c.recurso_id] ||= []).push(c)
    return m
  }, [citas])

  if (loading) return <p className="py-16 text-center text-sm text-muted-foreground">Cargando agenda…</p>
  if (recursos.length === 0) {
    return <p className="py-16 text-center text-sm text-muted-foreground">No hay recursos. Créalos en <span className="font-medium">Configuración</span>.</p>
  }

  const ahoraMin = minutosCO(new Date().toISOString())
  const topAhora = ((ahoraMin - HORA_INICIO * 60) / 60) * HORA_PX
  const mostrarAhora = dia === hoyCO() && topAhora >= 0 && topAhora <= ALTO_GRILLA

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[560px]">
        {/* Encabezado de recursos */}
        <div className="flex border-b border-border sticky top-0 bg-surface-2 z-10">
          <div className="w-14 shrink-0" />
          {recursos.map(r => (
            <div key={r.id} className="flex-1 min-w-[170px] px-3 py-2 border-l border-border-subtle text-center">
              <div className="font-display text-sm font-bold truncate">{r.nombre}</div>
              <div className="text-[11px] text-muted-foreground capitalize">{r.tipo}</div>
            </div>
          ))}
        </div>

        {/* Cuerpo */}
        <div className="flex relative" style={{ height: ALTO_GRILLA }}>
          {/* Columna de horas */}
          <div className="w-14 shrink-0 relative">
            {HORAS.map((h, i) => (
              <div key={h} className="absolute right-2 -translate-y-1/2 text-[11px] text-muted-foreground tabular-nums"
                style={{ top: i * HORA_PX }}>
                {String(h).padStart(2, '0')}:00
              </div>
            ))}
          </div>

          {recursos.map(r => (
            <ColumnaRecurso
              key={r.id} recurso={r} citas={porRecurso[r.id] || []} nombreServicio={nombreServicio}
            />
          ))}

          {/* Indicador de "ahora" */}
          {mostrarAhora && (
            <div className="absolute left-14 right-0 z-20 pointer-events-none" style={{ top: topAhora }} aria-hidden="true">
              <div className="h-px bg-primary relative">
                <span className="absolute -left-1 -top-[3px] size-1.5 rounded-full bg-primary" />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ColumnaRecurso({ recurso, citas, nombreServicio }) {
  return (
    <div className="flex-1 min-w-[170px] relative border-l border-border-subtle" aria-label={`Columna ${recurso.nombre}`}>
      {/* Líneas de hora */}
      {HORAS.map((h, i) => (
        <div key={h} className="absolute left-0 right-0 border-t border-border-subtle/60" style={{ top: i * HORA_PX }} />
      ))}
      {citas.map(c => <BloqueCita key={c.id} cita={c} servicio={nombreServicio[c.servicio_id]} />)}
    </div>
  )
}

function BloqueCita({ cita, servicio }) {
  const ini = minutosCO(cita.inicio)
  const fin = Math.max(minutosCO(cita.fin), ini + 15)
  const top = Math.max(((ini - HORA_INICIO * 60) / 60) * HORA_PX, 0)
  const alto = Math.max(((fin - ini) / 60) * HORA_PX, 42)
  const atencion = requiereAtencion(cita)
  const enRiesgo = cita.estado === 'confirmada' && cita.confirmacion === 'en_riesgo'

  return (
    <div
      className={`absolute left-1 right-1 rounded-md border-l-4 px-2 py-1 overflow-hidden shadow-xs ${ESTADO_ACCENT[cita.estado] || 'border-border bg-surface-2'} ${atencion ? 'ring-1 ring-warning/40' : ''} ${enRiesgo ? 'ring-1 ring-destructive/50' : ''}`}
      style={{ top, height: alto }}
      title={`${fmtHora(cita.inicio)}–${fmtHora(cita.fin)} · ${cita.cliente_nombre}`}
    >
      <div className="flex items-center justify-between gap-1">
        <span className="text-[11px] font-medium tabular-nums text-muted-foreground">
          {fmtHora(cita.inicio)}–{fmtHora(cita.fin)}
        </span>
        {atencion
          ? <span className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-warning"><AlertTriangle className="size-3" /> Revisar</span>
          : cita.estado === 'confirmada'
            ? <ConfirmacionBadge confirmacion={cita.confirmacion} />
            : <EstadoBadge estado={cita.estado} />}
      </div>
      <div className="text-[13px] font-semibold leading-tight truncate flex items-center gap-1">
        {cita.origen === 'whatsapp'
          ? <MessageCircle className="size-3 shrink-0 text-success" aria-label="Por WhatsApp" />
          : <Monitor className="size-3 shrink-0 text-muted-foreground" aria-label="Por dashboard" />}
        <span className="truncate">{cita.cliente_nombre}</span>
      </div>
      {alto > 52 && <div className="text-[11px] text-muted-foreground truncate">{servicio || `#${cita.servicio_id}`}</div>}
    </div>
  )
}

// ── Panel "Acción Requerida" ─────────────────────────────────────────────────
function AccionRequerida({ pendientes, nombreServicio, refrescar }) {
  return (
    <Card className="w-full xl:w-80 shrink-0 p-3 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold inline-flex items-center gap-1.5">
          <BellRing className="size-4 text-primary" /> Acción requerida
        </h3>
        {pendientes.length > 0 && (
          <span className="inline-flex items-center rounded-full bg-primary/10 text-primary px-2 py-0.5 text-xs font-semibold">
            {pendientes.length} {pendientes.length === 1 ? 'nueva' : 'nuevas'}
          </span>
        )}
      </div>
      {pendientes.length === 0 ? (
        <div className="py-8 text-center text-sm text-muted-foreground">
          <Check className="size-5 mx-auto mb-2 text-success" /> Sin pendientes por revisar.
        </div>
      ) : (
        <ul className="space-y-2">
          {pendientes.map(c => (
            <li key={c.id} className="rounded-md border border-border-subtle p-2.5">
              <div className="font-medium text-sm truncate">{c.cliente_nombre}</div>
              <div className="text-[11px] text-muted-foreground mb-1">{fmtFechaCO(c.inicio)}</div>
              <div className="text-[12px] text-muted-foreground truncate mb-2">{nombreServicio[c.servicio_id] || `Servicio #${c.servicio_id}`}</div>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" className="flex-1" aria-label={`Rechazar cita ${c.id}`}
                  onClick={() => postAccion(`/agenda/citas/${c.id}/cancelar`, null, 'Cita rechazada', refrescar)}>
                  <X className="size-3.5" /> Rechazar
                </Button>
                <Button size="sm" className="flex-1" aria-label={`Aprobar cita ${c.id}`}
                  onClick={() => postAccion(`/agenda/citas/${c.id}/confirmar`, null, 'Cita aprobada', refrescar)}>
                  <Check className="size-3.5" /> Aprobar
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

// ── Alta manual (origen=dashboard) ───────────────────────────────────────────
function NuevaCitaForm({ servicios, recursos, onClose, onCreada }) {
  const [f, setF] = useState({ servicio_id: '', recurso_id: '', inicio: '', cliente_nombre: '', cliente_telefono: '' })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF(prev => ({ ...prev, [k]: e.target.value }))

  async function crear() {
    if (!f.servicio_id || !f.recurso_id || !f.inicio || !f.cliente_nombre.trim() || !f.cliente_telefono.trim()) {
      toast.error('Completa servicio, recurso, fecha, nombre y teléfono'); return
    }
    setEnviando(true)
    try {
      const res = await api('/agenda/citas', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          servicio_id: Number(f.servicio_id), recurso_id: Number(f.recurso_id),
          inicio: aISOColombia(f.inicio),
          cliente_nombre: f.cliente_nombre.trim(), cliente_telefono: f.cliente_telefono.trim(),
        }),
      })
      if (res.status === 201) { toast.success('Cita agendada'); onCreada() }
      else if (res.status === 409) { toast.error('Ese horario no está disponible') }
      else { toast.error('No se pudo agendar') }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <h3 className="text-sm font-semibold mb-3">Nueva cita</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <select value={f.servicio_id} onChange={set('servicio_id')} aria-label="Servicio"
          className="h-9 px-2 rounded-md border border-border bg-surface text-sm">
          <option value="">Servicio…</option>
          {servicios.map(s => <option key={s.id} value={s.id}>{s.nombre}</option>)}
        </select>
        <select value={f.recurso_id} onChange={set('recurso_id')} aria-label="Recurso de la cita"
          className="h-9 px-2 rounded-md border border-border bg-surface text-sm">
          <option value="">Recurso…</option>
          {recursos.map(r => <option key={r.id} value={r.id}>{r.nombre}</option>)}
        </select>
        <Input type="datetime-local" value={f.inicio} onChange={set('inicio')} aria-label="Fecha y hora" className="h-9" />
        <Input value={f.cliente_nombre} onChange={set('cliente_nombre')} placeholder="Nombre del cliente" aria-label="Nombre del cliente" className="h-9" />
        <Input value={f.cliente_telefono} onChange={set('cliente_telefono')} placeholder="Teléfono (WhatsApp)" aria-label="Teléfono" className="h-9" />
      </div>
      <div className="flex justify-end gap-2 mt-3">
        <Button variant="ghost" onClick={onClose}>Cancelar</Button>
        <Button onClick={crear} disabled={enviando}>{enviando ? 'Agendando…' : 'Agendar'}</Button>
      </div>
    </Card>
  )
}
