/*
 * SeccionConfig — CRUD de configuración del pack Agenda (solo admin; el backend lo exige y aquí se
 * deshabilita con gracia para no-admin). Áreas: Servicios, Recursos (+asignación N:N), Disponibilidad
 * (horario semanal por recurso), Bloqueos, y Reglas (agenda_config). Todo contra /api/v1/agenda.
 */
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Plus, Trash2, Power } from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch } from '@/components/shared.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import { DIAS_SEMANA, TIPOS_RECURSO, aISOColombia, fmtFechaCO } from './util.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])
const AREAS = [
  ['servicios', 'Servicios'], ['recursos', 'Recursos'], ['disponibilidad', 'Disponibilidad'],
  ['bloqueos', 'Bloqueos'], ['reglas', 'Reglas'],
]

async function enviar(path, method, body, okMsg, after) {
  try {
    const res = await api(path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    if (res.ok) { toast.success(okMsg); after?.(); return true }
    if (res.status === 403) toast.error('Necesitas permisos de administrador')
    else toast.error('No se pudo guardar')
  } catch { toast.error('Error de conexión') }
  return false
}

export default function SeccionConfig({ admin }) {
  const [area, setArea] = useState('servicios')
  if (!admin) {
    return (
      <Card className="p-6 text-center text-sm text-muted-foreground">
        Solo un administrador puede editar la configuración de la agenda.
      </Card>
    )
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1">
        {AREAS.map(([id, label]) => (
          <button key={id} onClick={() => setArea(id)}
            className={`px-3 h-8 rounded-md text-sm font-medium ${area === id ? 'bg-surface-2 text-foreground' : 'text-muted-foreground hover:bg-surface-2/60'}`}>
            {label}
          </button>
        ))}
      </div>
      {area === 'servicios' && <AreaServicios />}
      {area === 'recursos' && <AreaRecursos />}
      {area === 'disponibilidad' && <AreaDisponibilidad />}
      {area === 'bloqueos' && <AreaBloqueos />}
      {area === 'reglas' && <AreaReglas />}
    </div>
  )
}

// ── Servicios ────────────────────────────────────────────────────────────────
function AreaServicios() {
  const q = useFetch('/agenda/servicios?incluir_inactivos=true')
  const servicios = arr(q.data)
  const vacio = { nombre: '', duracion_min: 30, precio: '', buffer_antes_min: 0, buffer_despues_min: 0, categoria: '' }
  const [f, setF] = useState(vacio)
  const [editId, setEditId] = useState(null)
  const set = (k) => (e) => setF(p => ({ ...p, [k]: e.target.value }))

  async function guardar() {
    if (!f.nombre.trim() || !f.duracion_min) { toast.error('Nombre y duración son obligatorios'); return }
    const body = {
      nombre: f.nombre.trim(), duracion_min: Number(f.duracion_min),
      precio: f.precio === '' ? null : Number(f.precio),
      buffer_antes_min: Number(f.buffer_antes_min) || 0, buffer_despues_min: Number(f.buffer_despues_min) || 0,
      categoria: f.categoria.trim() || null,
    }
    const ok = editId
      ? await enviar(`/agenda/servicios/${editId}`, 'PUT', body, 'Servicio actualizado', q.refetch)
      : await enviar('/agenda/servicios', 'POST', body, 'Servicio creado', q.refetch)
    if (ok) { setF(vacio); setEditId(null) }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <ListaCard titulo="Servicios" loading={q.loading} vacio={servicios.length === 0}>
        {servicios.map(s => (
          <Item key={s.id} inactivo={!s.activo}>
            <div className="min-w-0 flex-1">
              <div className="font-medium truncate">{s.nombre} {!s.activo && <span className="text-[11px] text-muted-foreground">(inactivo)</span>}</div>
              <div className="text-[11px] text-muted-foreground">{s.duracion_min} min · {s.precio ? `$${Number(s.precio).toLocaleString('es-CO')}` : 'sin precio'} · buffers {s.buffer_antes_min}/{s.buffer_despues_min}</div>
            </div>
            <Button size="sm" variant="ghost" onClick={() => { setF({ nombre: s.nombre, duracion_min: s.duracion_min, precio: s.precio ?? '', buffer_antes_min: s.buffer_antes_min, buffer_despues_min: s.buffer_despues_min, categoria: s.categoria ?? '' }); setEditId(s.id) }}>Editar</Button>
            {s.activo && (
              <Button size="sm" variant="ghost" aria-label={`Desactivar servicio ${s.id}`} className="text-destructive"
                onClick={() => enviar(`/agenda/servicios/${s.id}`, 'DELETE', null, 'Servicio desactivado', q.refetch)}>
                <Power className="size-3.5" />
              </Button>
            )}
          </Item>
        ))}
      </ListaCard>

      <Card className="p-3.5">
        <h3 className="text-sm font-semibold mb-3">{editId ? 'Editar servicio' : 'Nuevo servicio'}</h3>
        <div className="space-y-2">
          <Input value={f.nombre} onChange={set('nombre')} placeholder="Nombre *" aria-label="Nombre del servicio" className="h-9" />
          <div className="grid grid-cols-2 gap-2">
            <Num label="Duración (min)" value={f.duracion_min} onChange={set('duracion_min')} />
            <Num label="Precio" value={f.precio} onChange={set('precio')} placeholder="opcional" />
            <Num label="Buffer antes" value={f.buffer_antes_min} onChange={set('buffer_antes_min')} />
            <Num label="Buffer después" value={f.buffer_despues_min} onChange={set('buffer_despues_min')} />
          </div>
          <Input value={f.categoria} onChange={set('categoria')} placeholder="Categoría (opcional)" aria-label="Categoría" className="h-9" />
          <div className="flex justify-end gap-2">
            {editId && <Button variant="ghost" onClick={() => { setF(vacio); setEditId(null) }}>Cancelar</Button>}
            <Button onClick={guardar}>{editId ? 'Guardar' : 'Crear servicio'}</Button>
          </div>
        </div>
      </Card>
    </div>
  )
}

// ── Recursos (+ asignación N:N por servicio) ─────────────────────────────────
function AreaRecursos() {
  const recursosQ = useFetch('/agenda/recursos?incluir_inactivos=true')
  const serviciosQ = useFetch('/agenda/servicios')
  const recursos = arr(recursosQ.data)
  const servicios = arr(serviciosQ.data)
  const [f, setF] = useState({ nombre: '', tipo: 'profesional' })

  async function crear() {
    if (!f.nombre.trim()) { toast.error('El nombre es obligatorio'); return }
    const ok = await enviar('/agenda/recursos', 'POST', { nombre: f.nombre.trim(), tipo: f.tipo }, 'Recurso creado', recursosQ.refetch)
    if (ok) setF({ nombre: '', tipo: 'profesional' })
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <ListaCard titulo="Recursos" loading={recursosQ.loading} vacio={recursos.length === 0}>
          {recursos.map(r => (
            <Item key={r.id} inactivo={!r.activo}>
              <div className="min-w-0 flex-1">
                <div className="font-medium truncate">{r.nombre} {!r.activo && <span className="text-[11px] text-muted-foreground">(inactivo)</span>}</div>
                <div className="text-[11px] text-muted-foreground capitalize">{r.tipo}</div>
              </div>
              {r.activo && (
                <Button size="sm" variant="ghost" aria-label={`Desactivar recurso ${r.id}`} className="text-destructive"
                  onClick={() => enviar(`/agenda/recursos/${r.id}`, 'DELETE', null, 'Recurso desactivado', recursosQ.refetch)}>
                  <Power className="size-3.5" />
                </Button>
              )}
            </Item>
          ))}
        </ListaCard>

        <Card className="p-3.5">
          <h3 className="text-sm font-semibold mb-3">Nuevo recurso</h3>
          <div className="space-y-2">
            <Input value={f.nombre} onChange={e => setF(p => ({ ...p, nombre: e.target.value }))} placeholder="Nombre (p. ej. Dra. Pérez, Silla 2)" aria-label="Nombre del recurso" className="h-9" />
            <select value={f.tipo} onChange={e => setF(p => ({ ...p, tipo: e.target.value }))} aria-label="Tipo de recurso"
              className="h-9 px-2 rounded-md border border-border bg-surface text-sm w-full capitalize">
              {TIPOS_RECURSO.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            <div className="flex justify-end"><Button onClick={crear}>Crear recurso</Button></div>
          </div>
        </Card>
      </div>

      <Card className="p-3.5">
        <h3 className="text-sm font-semibold mb-1">Asignación de servicios</h3>
        <p className="text-[11px] text-muted-foreground mb-3">Marca qué recursos prestan cada servicio.</p>
        {servicios.length === 0 ? (
          <p className="text-sm text-muted-foreground">Crea primero un servicio.</p>
        ) : (
          <div className="space-y-3">
            {servicios.map(s => <AsignacionServicio key={s.id} servicio={s} recursos={recursos.filter(r => r.activo)} />)}
          </div>
        )}
      </Card>
    </div>
  )
}

function AsignacionServicio({ servicio, recursos }) {
  const q = useFetch(`/agenda/servicios/${servicio.id}/recursos`)
  const asignados = new Set(arr(q.data).map(r => r.id))

  async function toggle(recursoId, marcado) {
    if (marcado) {
      await enviar('/agenda/recurso-servicio', 'POST', { recurso_id: recursoId, servicio_id: servicio.id }, 'Asignado', q.refetch)
    } else {
      await enviar(`/agenda/recurso-servicio?recurso_id=${recursoId}&servicio_id=${servicio.id}`, 'DELETE', null, 'Quitado', q.refetch)
    }
  }

  return (
    <div className="border border-border-subtle rounded-md p-2">
      <div className="text-sm font-medium mb-1.5">{servicio.nombre}</div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {recursos.length === 0 ? <span className="text-xs text-muted-foreground">Sin recursos.</span> : recursos.map(r => (
          <label key={r.id} className="inline-flex items-center gap-1.5 text-[13px]">
            <input type="checkbox" checked={asignados.has(r.id)} aria-label={`${servicio.nombre} → ${r.nombre}`}
              onChange={e => toggle(r.id, e.target.checked)} />
            {r.nombre}
          </label>
        ))}
      </div>
    </div>
  )
}

// ── Disponibilidad (horario semanal por recurso) ─────────────────────────────
function AreaDisponibilidad() {
  const recursosQ = useFetch('/agenda/recursos')
  const recursos = arr(recursosQ.data)
  const [recursoId, setRecursoId] = useState('')
  const dispQ = useFetch(recursoId ? `/agenda/recursos/${recursoId}/disponibilidad` : '/agenda/recursos', [recursoId])
  const filas = recursoId ? arr(dispQ.data) : []
  const [nf, setNf] = useState({ dia_semana: 0, hora_inicio: '08:00', hora_fin: '12:00' })

  async function agregar() {
    if (!recursoId) { toast.error('Elige un recurso'); return }
    await enviar('/agenda/disponibilidad', 'POST', {
      recurso_id: Number(recursoId), dia_semana: Number(nf.dia_semana),
      hora_inicio: nf.hora_inicio, hora_fin: nf.hora_fin,
    }, 'Franja agregada', dispQ.refetch)
  }

  return (
    <Card className="p-3.5">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-sm font-semibold">Horario semanal de</span>
        <select value={recursoId} onChange={e => setRecursoId(e.target.value)} aria-label="Recurso del horario"
          className="h-9 px-2 rounded-md border border-border bg-surface text-sm">
          <option value="">elige un recurso…</option>
          {recursos.map(r => <option key={r.id} value={r.id}>{r.nombre}</option>)}
        </select>
      </div>
      {!recursoId ? (
        <p className="text-sm text-muted-foreground">Selecciona un recurso para ver y editar su horario.</p>
      ) : (
        <>
          <ul className="divide-y divide-border-subtle mb-3">
            {filas.length === 0 ? <li className="py-3 text-sm text-muted-foreground">Sin franjas. Agrega una abajo.</li> : filas.map(d => (
              <li key={d.id} className="py-2 flex items-center gap-2 text-sm">
                <span className="w-24 font-medium">{DIAS_SEMANA[d.dia_semana]}</span>
                <span className="text-muted-foreground">{d.hora_inicio?.slice(0, 5)} – {d.hora_fin?.slice(0, 5)}</span>
                <Button size="sm" variant="ghost" aria-label={`Eliminar franja ${d.id}`} className="ml-auto text-destructive"
                  onClick={() => enviar(`/agenda/disponibilidad/${d.id}`, 'DELETE', null, 'Franja eliminada', dispQ.refetch)}>
                  <Trash2 className="size-3.5" />
                </Button>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-end gap-2 border-t border-border-subtle pt-3">
            <select value={nf.dia_semana} onChange={e => setNf(p => ({ ...p, dia_semana: e.target.value }))} aria-label="Día de la semana"
              className="h-9 px-2 rounded-md border border-border bg-surface text-sm">
              {DIAS_SEMANA.map((d, i) => <option key={i} value={i}>{d}</option>)}
            </select>
            <Input type="time" value={nf.hora_inicio} onChange={e => setNf(p => ({ ...p, hora_inicio: e.target.value }))} aria-label="Hora inicio" className="h-9 w-32" />
            <Input type="time" value={nf.hora_fin} onChange={e => setNf(p => ({ ...p, hora_fin: e.target.value }))} aria-label="Hora fin" className="h-9 w-32" />
            <Button onClick={agregar}><Plus className="size-4" /> Agregar franja</Button>
          </div>
        </>
      )}
    </Card>
  )
}

// ── Bloqueos ─────────────────────────────────────────────────────────────────
function AreaBloqueos() {
  const q = useFetch('/agenda/bloqueos')
  const recursosQ = useFetch('/agenda/recursos')
  const bloqueos = arr(q.data)
  const recursos = arr(recursosQ.data)
  const [f, setF] = useState({ recurso_id: '', inicio: '', fin: '', motivo: '' })
  const set = (k) => (e) => setF(p => ({ ...p, [k]: e.target.value }))

  async function crear() {
    if (!f.inicio || !f.fin) { toast.error('Inicio y fin son obligatorios'); return }
    const ok = await enviar('/agenda/bloqueos', 'POST', {
      recurso_id: f.recurso_id ? Number(f.recurso_id) : null,
      inicio: aISOColombia(f.inicio), fin: aISOColombia(f.fin), motivo: f.motivo.trim() || null,
    }, 'Bloqueo creado', q.refetch)
    if (ok) setF({ recurso_id: '', inicio: '', fin: '', motivo: '' })
  }

  const nombreRecurso = Object.fromEntries(recursos.map(r => [r.id, r.nombre]))

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <ListaCard titulo="Bloqueos" loading={q.loading} vacio={bloqueos.length === 0}>
        {bloqueos.map(b => (
          <Item key={b.id}>
            <div className="min-w-0 flex-1">
              <div className="font-medium truncate">{b.motivo || 'Bloqueo'}</div>
              <div className="text-[11px] text-muted-foreground">
                {fmtFechaCO(b.inicio)} → {fmtFechaCO(b.fin)} · {b.recurso_id ? (nombreRecurso[b.recurso_id] || `#${b.recurso_id}`) : 'todo el negocio'}
              </div>
            </div>
            <Button size="sm" variant="ghost" aria-label={`Eliminar bloqueo ${b.id}`} className="text-destructive"
              onClick={() => enviar(`/agenda/bloqueos/${b.id}`, 'DELETE', null, 'Bloqueo eliminado', q.refetch)}>
              <Trash2 className="size-3.5" />
            </Button>
          </Item>
        ))}
      </ListaCard>

      <Card className="p-3.5">
        <h3 className="text-sm font-semibold mb-3">Nuevo bloqueo</h3>
        <div className="space-y-2">
          <label className="text-[11px] uppercase tracking-wider text-muted-foreground">Desde</label>
          <Input type="datetime-local" value={f.inicio} onChange={set('inicio')} aria-label="Inicio del bloqueo" className="h-9" />
          <label className="text-[11px] uppercase tracking-wider text-muted-foreground">Hasta</label>
          <Input type="datetime-local" value={f.fin} onChange={set('fin')} aria-label="Fin del bloqueo" className="h-9" />
          <select value={f.recurso_id} onChange={set('recurso_id')} aria-label="Recurso del bloqueo"
            className="h-9 px-2 rounded-md border border-border bg-surface text-sm w-full">
            <option value="">Todo el negocio (global)</option>
            {recursos.map(r => <option key={r.id} value={r.id}>{r.nombre}</option>)}
          </select>
          <Input value={f.motivo} onChange={set('motivo')} placeholder="Motivo (opcional)" aria-label="Motivo" className="h-9" />
          <div className="flex justify-end"><Button onClick={crear}>Crear bloqueo</Button></div>
        </div>
      </Card>
    </div>
  )
}

// ── Reglas (agenda_config) ───────────────────────────────────────────────────
const REGLAS_DEFAULT = {
  zona_horaria: 'America/Bogota', intervalo_slots_min: 15, anticipacion_minima_min: 120,
  ventana_maxima_dias: 30, politica_cancelacion_horas: 24, permite_reagendar: true,
  modo_confirmacion: 'auto', requiere_anticipo: false, anticipo_tipo: '', anticipo_valor: '',
  capacidad_por_slot: 1, recordatorios_horas: '24,2', persona: '', google_calendar_id: '',
}

function AreaReglas() {
  const q = useFetch('/agenda/config')   // 404 si aún no se ha configurado → defaults
  const [f, setF] = useState(null)
  // Inicializa el form al resolver la consulta (config existente o defaults). El 404 deja data=null.
  useEffect(() => {
    if (f !== null || q.loading) return
    const c = q.data
    setF(c ? {
      ...c, anticipo_tipo: c.anticipo_tipo ?? '', anticipo_valor: c.anticipo_valor ?? '',
      persona: c.persona ?? '', recordatorios_horas: (c.recordatorios_horas || []).join(','),
      google_calendar_id: c.google_calendar_id ?? '',
    } : { ...REGLAS_DEFAULT })
  }, [q.loading, q.data, f])
  if (!f) return <Card className="p-6 text-sm text-muted-foreground">Cargando reglas…</Card>
  const set = (k) => (e) => setF(p => ({ ...p, [k]: e.target.value }))
  const setBool = (k) => (e) => setF(p => ({ ...p, [k]: e.target.checked }))

  async function guardar() {
    const body = {
      zona_horaria: f.zona_horaria || 'America/Bogota',
      intervalo_slots_min: Number(f.intervalo_slots_min) || 15,
      anticipacion_minima_min: Number(f.anticipacion_minima_min) || 0,
      ventana_maxima_dias: Number(f.ventana_maxima_dias) || 30,
      politica_cancelacion_horas: Number(f.politica_cancelacion_horas) || 0,
      permite_reagendar: !!f.permite_reagendar,
      modo_confirmacion: f.modo_confirmacion,
      requiere_anticipo: !!f.requiere_anticipo,
      anticipo_tipo: f.anticipo_tipo || null,
      anticipo_valor: f.anticipo_valor === '' ? null : Number(f.anticipo_valor),
      capacidad_por_slot: Number(f.capacidad_por_slot) || 1,
      recordatorios_horas: String(f.recordatorios_horas).split(',').map(x => Number(x.trim())).filter(x => !isNaN(x)),
      persona: f.persona.trim() || null,
      google_calendar_id: f.google_calendar_id.trim() || null,
    }
    await enviar('/agenda/config', 'PUT', body, 'Reglas guardadas', q.refetch)
  }

  return (
    <Card className="p-3.5 max-w-2xl">
      <h3 className="text-sm font-semibold mb-3">Reglas de la agenda</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Num label="Intervalo de cupos (min)" value={f.intervalo_slots_min} onChange={set('intervalo_slots_min')} />
        <Num label="Anticipación mínima (min)" value={f.anticipacion_minima_min} onChange={set('anticipacion_minima_min')} />
        <Num label="Ventana máxima (días)" value={f.ventana_maxima_dias} onChange={set('ventana_maxima_dias')} />
        <Num label="Política de cancelación (horas)" value={f.politica_cancelacion_horas} onChange={set('politica_cancelacion_horas')} />
        <Num label="Capacidad por cupo" value={f.capacidad_por_slot} onChange={set('capacidad_por_slot')} />
        <Campo label="Modo de confirmación">
          <select value={f.modo_confirmacion} onChange={set('modo_confirmacion')} aria-label="Modo de confirmación"
            className="h-9 px-2 rounded-md border border-border bg-surface text-sm">
            <option value="auto">Automático</option>
            <option value="manual">Manual (el negocio aprueba)</option>
          </select>
        </Campo>
        <Campo label="Recordatorios (horas antes, coma)">
          <Input value={f.recordatorios_horas} onChange={set('recordatorios_horas')} aria-label="Recordatorios" placeholder="24,2" className="h-9" />
        </Campo>
        <Campo label="Zona horaria">
          <Input value={f.zona_horaria} onChange={set('zona_horaria')} aria-label="Zona horaria" className="h-9" />
        </Campo>
        <label className="inline-flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!f.permite_reagendar} onChange={setBool('permite_reagendar')} aria-label="Permite reagendar" />
          Permite reagendar
        </label>
        <label className="inline-flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!f.requiere_anticipo} onChange={setBool('requiere_anticipo')} aria-label="Requiere anticipo" />
          Requiere anticipo
        </label>
        {f.requiere_anticipo && (
          <>
            <Campo label="Tipo de anticipo">
              <select value={f.anticipo_tipo} onChange={set('anticipo_tipo')} aria-label="Tipo de anticipo"
                className="h-9 px-2 rounded-md border border-border bg-surface text-sm">
                <option value="">—</option>
                <option value="porcentaje">Porcentaje</option>
                <option value="fijo">Monto fijo</option>
              </select>
            </Campo>
            <Num label="Valor del anticipo" value={f.anticipo_valor} onChange={set('anticipo_valor')} placeholder="opcional" />
          </>
        )}
      </div>
      <Campo label="Persona / tono del agente" className="mt-3">
        <textarea value={f.persona} onChange={set('persona')} aria-label="Persona del agente" rows={3}
          className="w-full px-3 py-2 rounded-md border border-border bg-surface text-sm"
          placeholder="Ej: Hablas cordial y breve, tuteas al cliente." />
      </Campo>
      <Campo label="Google Calendar ID (opcional)" className="mt-3">
        <Input value={f.google_calendar_id} onChange={set('google_calendar_id')} aria-label="Google Calendar ID"
          placeholder="negocio@group.calendar.google.com" className="h-9" />
        <span className="text-[11px] text-muted-foreground">ID del Google Calendar del negocio; vacío = sin sync.</span>
      </Campo>
      <div className="flex justify-end mt-3"><Button onClick={guardar}>Guardar reglas</Button></div>
    </Card>
  )
}

// ── compartidos ──────────────────────────────────────────────────────────────
function ListaCard({ titulo, loading, vacio, children }) {
  return (
    <Card className="p-3">
      <h3 className="text-sm font-semibold mb-2">{titulo}</h3>
      {loading ? <p className="py-6 text-center text-sm text-muted-foreground">Cargando…</p>
        : vacio ? <p className="py-6 text-center text-sm text-muted-foreground">Sin elementos.</p>
        : <ul className="divide-y divide-border-subtle">{children}</ul>}
    </Card>
  )
}

function Item({ children, inactivo }) {
  return <li className={`py-2 flex items-center gap-2 text-[13px] ${inactivo ? 'opacity-60' : ''}`}>{children}</li>
}

function Num({ label, value, onChange, placeholder }) {
  return (
    <Campo label={label}>
      <Input type="number" value={value} onChange={onChange} placeholder={placeholder} aria-label={label} className="h-9" />
    </Campo>
  )
}

function Campo({ label, children, className = '' }) {
  return (
    <label className={`flex flex-col gap-1 ${className}`}>
      <span className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</span>
      {children}
    </label>
  )
}
