/*
 * SeccionCitas — lista funcional de citas del negocio (la vista calendario llega después).
 * GET /agenda/citas con filtros (rango, estado, recurso). Tiempo real: las citas que agenda el
 * agente de WhatsApp aparecen en vivo (useRealtimeEvent). Acciones: confirmar / cancelar / reagendar
 * y alta manual (origen=dashboard). Fechas en hora Colombia.
 */
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { CalendarPlus, Check, X, Clock } from 'lucide-react'
import { api } from '@/lib/api.js'
import { useFetch } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import { ESTADOS, EstadoBadge, aISOColombia, fmtFechaCO, hoyCO, masDiasCO } from './util.jsx'

const TERMINALES = new Set(['cumplida', 'cancelada', 'no_show'])

async function postAccion(path, body, okMsg, refetch) {
  try {
    const res = await api(path, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    if (res.ok) {
      toast.success(okMsg)
      refetch()
      return true
    }
    if (res.status === 409) {
      const d = await res.json().catch(() => ({}))
      const detalle = d?.detail
      toast.error(typeof detalle === 'object' ? 'Ese horario no está disponible' : (detalle || 'No se pudo'))
    } else {
      toast.error('No se pudo completar la acción')
    }
  } catch {
    toast.error('Error de conexión')
  }
  return false
}

export default function SeccionCitas() {
  const [desde, setDesde] = useState(hoyCO())
  const [hasta, setHasta] = useState(masDiasCO(7))
  const [estado, setEstado] = useState('')
  const [recursoId, setRecursoId] = useState('')
  const [creando, setCreando] = useState(false)

  const recursosQ = useFetch('/agenda/recursos')
  const serviciosQ = useFetch('/agenda/servicios')
  const recursos = Array.isArray(recursosQ.data) ? recursosQ.data : []
  const servicios = Array.isArray(serviciosQ.data) ? serviciosQ.data : []

  const query = `/agenda/citas?desde=${desde}&hasta=${hasta}`
    + (estado ? `&estado=${estado}` : '')
    + (recursoId ? `&recurso_id=${recursoId}` : '')
  const citasQ = useFetch(query, [desde, hasta, estado, recursoId])
  useRealtimeEvent(['cita_agendada', 'cita_estado', 'cita_reagendada', 'reconnected'], citasQ.refetch)

  const citas = Array.isArray(citasQ.data) ? citasQ.data : []
  const nombreServicio = useMemo(() => Object.fromEntries(servicios.map(s => [s.id, s.nombre])), [servicios])
  const nombreRecurso = useMemo(() => Object.fromEntries(recursos.map(r => [r.id, r.nombre])), [recursos])

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-end gap-2">
          <Campo label="Desde">
            <Input type="date" value={desde} onChange={e => setDesde(e.target.value)} aria-label="Desde" className="h-9 w-40" />
          </Campo>
          <Campo label="Hasta">
            <Input type="date" value={hasta} onChange={e => setHasta(e.target.value)} aria-label="Hasta" className="h-9 w-40" />
          </Campo>
          <Campo label="Estado">
            <select value={estado} onChange={e => setEstado(e.target.value)} aria-label="Estado"
              className="h-9 px-2 rounded-md border border-border bg-surface text-sm">
              <option value="">Todos</option>
              {ESTADOS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </Campo>
          <Campo label="Profesional / recurso">
            <select value={recursoId} onChange={e => setRecursoId(e.target.value)} aria-label="Recurso"
              className="h-9 px-2 rounded-md border border-border bg-surface text-sm">
              <option value="">Todos</option>
              {recursos.map(r => <option key={r.id} value={r.id}>{r.nombre}</option>)}
            </select>
          </Campo>
          <div className="ml-auto">
            <Button onClick={() => setCreando(v => !v)} className="h-9">
              <CalendarPlus className="size-4" /> Nueva cita
            </Button>
          </div>
        </div>
      </Card>

      {creando && (
        <NuevaCitaForm
          servicios={servicios} recursos={recursos}
          onClose={() => setCreando(false)} onCreada={() => { setCreando(false); citasQ.refetch() }}
        />
      )}

      <Card className="p-0 overflow-hidden">
        {citasQ.loading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : citas.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">No hay citas en este rango.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-muted-foreground text-xs">
              <tr>
                <Th>Fecha y hora</Th><Th>Cliente</Th><Th>Servicio</Th><Th>Recurso</Th>
                <Th>Estado</Th><Th className="text-right pr-3">Acciones</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {citas.map(c => (
                <FilaCita
                  key={c.id} cita={c}
                  servicio={nombreServicio[c.servicio_id]} recurso={nombreRecurso[c.recurso_id]}
                  refetch={citasQ.refetch}
                />
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}

function FilaCita({ cita, servicio, recurso, refetch }) {
  const [reagendando, setReagendando] = useState(false)
  const [nuevo, setNuevo] = useState('')
  const terminal = TERMINALES.has(cita.estado)

  async function reagendar() {
    const iso = aISOColombia(nuevo)
    if (!iso) { toast.error('Elige la nueva fecha y hora'); return }
    const ok = await postAccion(`/agenda/citas/${cita.id}/reagendar`, { nuevo_inicio: iso }, 'Cita reagendada', refetch)
    if (ok) { setReagendando(false); setNuevo('') }
  }

  return (
    <>
      <tr className="hover:bg-surface-2/50">
        <td className="px-3 py-2.5 whitespace-nowrap">{fmtFechaCO(cita.inicio)}</td>
        <td className="px-3 py-2.5">
          <div className="font-medium">{cita.cliente_nombre}</div>
          <div className="text-[11px] text-muted-foreground">{cita.cliente_telefono}</div>
        </td>
        <td className="px-3 py-2.5 text-muted-foreground">{servicio || `#${cita.servicio_id}`}</td>
        <td className="px-3 py-2.5 text-muted-foreground">{recurso || `#${cita.recurso_id}`}</td>
        <td className="px-3 py-2.5"><EstadoBadge estado={cita.estado} /></td>
        <td className="px-3 py-2.5">
          <div className="flex items-center justify-end gap-1">
            {cita.estado === 'pendiente' && (
              <Button size="sm" variant="outline" aria-label={`Confirmar cita ${cita.id}`}
                onClick={() => postAccion(`/agenda/citas/${cita.id}/confirmar`, null, 'Cita confirmada', refetch)}>
                <Check className="size-3.5" /> Confirmar
              </Button>
            )}
            {!terminal && (
              <Button size="sm" variant="ghost" aria-label={`Reagendar cita ${cita.id}`} onClick={() => setReagendando(v => !v)}>
                <Clock className="size-3.5" /> Reagendar
              </Button>
            )}
            {!terminal && (
              <Button size="sm" variant="ghost" aria-label={`Cancelar cita ${cita.id}`}
                className="text-destructive hover:bg-destructive/10"
                onClick={() => postAccion(`/agenda/citas/${cita.id}/cancelar`, null, 'Cita cancelada', refetch)}>
                <X className="size-3.5" /> Cancelar
              </Button>
            )}
          </div>
        </td>
      </tr>
      {reagendando && (
        <tr className="bg-surface-2/40">
          <td colSpan={6} className="px-3 py-2">
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">Nueva fecha y hora:</span>
              <Input type="datetime-local" value={nuevo} onChange={e => setNuevo(e.target.value)}
                aria-label={`Nuevo horario cita ${cita.id}`} className="h-9 w-56" />
              <Button size="sm" onClick={reagendar}>Mover</Button>
              <Button size="sm" variant="ghost" onClick={() => setReagendando(false)}>Cancelar</Button>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

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

function Campo({ label, children }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</span>
      {children}
    </label>
  )
}

function Th({ children, className = '' }) {
  return <th className={`px-3 py-2 text-left font-medium ${className}`}>{children}</th>
}
