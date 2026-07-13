/*
 * TabTrabajadores — personal de obra del vertical construcción (Fase 1, flag `nomina`). El trabajador
 * es la persona que ejecuta obra; distinto de la cuenta de dashboard (auth/RBAC). Dos naturalezas
 * conviven según `tipo_vinculacion`: DIRECTO (planta, con salario base y prestaciones) y PATACALIENTE
 * (por hora, con `tarifa_hora`, sin deducciones). Aquí, la capa de Fase 1: listar con su tipo visible,
 * crear/editar (el formulario muestra salario o tarifa según el tipo) y activar/desactivar.
 *
 * Contrato de API (pinneado): /api/v1/trabajadores — GET lista, POST crea, GET /{id}, PATCH /{id}
 * (incl. `activo`), DELETE /{id} = soft delete. Campos JSON = columnas del ORM en español
 * (tipo_vinculacion, documento, nombres, apellidos, cargo, salario_base, tarifa_hora, activo…). `activo`
 * es una baja laboral REVERSIBLE (toggle); DELETE es la ocultación del registro. Filtros en cliente.
 * Live: re-fetch ante 'reconnected'.
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { Users, UserPlus, Search, Pencil, Power } from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch, cop } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Semaforo, Chips, Campo, EstadoVacio, Esqueleto, BTN_PRIMARY, BTN_OUTLINE, SELECT_CLS } from './construccion/comunes.jsx'

const TIPOS_DOC = ['CC', 'CE', 'NIT', 'TI', 'PAS']
const VINCULACION = {
  DIRECTO:      { tono: 'azul',    label: 'Directo' },
  PATACALIENTE: { tono: 'violeta', label: 'Patacaliente' },
}

function metaTipo(tipo) {
  return VINCULACION[tipo] || { tono: 'gris', label: tipo || '—' }
}

export default function TabTrabajadores() {
  const { refreshKey } = useOutletContext() ?? {}
  const trabajadoresQ = useFetch('/trabajadores', [refreshKey])
  useRealtimeEvent(['reconnected'], trabajadoresQ.refetch)

  const [q, setQ] = useState('')
  const [tipo, setTipo] = useState(null)          // null | DIRECTO | PATACALIENTE
  const [soloActivos, setSoloActivos] = useState(true)
  const [editando, setEditando] = useState(null)  // null | 'nuevo' | trabajador

  const trabajadores = Array.isArray(trabajadoresQ.data) ? trabajadoresQ.data : []

  const activos = trabajadores.filter((t) => t.activo).length
  const chips = [
    { valor: null, label: 'Todos', conteo: trabajadores.length },
    { valor: 'DIRECTO', label: 'Directos', tono: 'azul', conteo: trabajadores.filter((t) => t.tipo_vinculacion === 'DIRECTO').length },
    { valor: 'PATACALIENTE', label: 'Patacalientes', tono: 'violeta', conteo: trabajadores.filter((t) => t.tipo_vinculacion === 'PATACALIENTE').length },
  ]

  const termino = q.trim().toLowerCase()
  const visibles = trabajadores.filter((t) => {
    if (tipo && t.tipo_vinculacion !== tipo) return false
    if (soloActivos && !t.activo) return false
    if (!termino) return true
    return [`${t.nombres} ${t.apellidos}`, t.documento, t.cargo].filter(Boolean).some((s) => String(s).toLowerCase().includes(termino))
  })

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[200px] flex-1">
            <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar por nombre, documento o cargo…" aria-label="Buscar trabajador" className="pl-9" />
          </div>
          <button onClick={() => setEditando(editando === 'nuevo' ? null : 'nuevo')} className={`${BTN_PRIMARY} h-9 shrink-0`}>
            <UserPlus className="size-4" /> Nuevo trabajador
          </button>
        </div>
        <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2">
          <Chips opciones={chips} valor={tipo} onChange={setTipo} ariaLabel="Filtrar por tipo de vinculación" />
          <label className="inline-flex cursor-pointer items-center gap-2 text-[12px] text-muted-foreground">
            <input type="checkbox" checked={soloActivos} onChange={(e) => setSoloActivos(e.target.checked)}
              className="size-3.5 rounded border-border text-primary focus-visible:ring-2 focus-visible:ring-ring" />
            Solo activos
          </label>
        </div>
      </Card>

      {editando && (
        <TrabajadorForm
          trabajador={editando === 'nuevo' ? null : editando}
          onClose={() => setEditando(null)}
          onGuardado={() => { setEditando(null); trabajadoresQ.refetch() }}
        />
      )}

      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border-subtle flex items-center gap-2">
          <Users className="size-4 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Personal {trabajadores.length > 0 && <span className="tabular">· {activos} activos</span>}
          </h2>
        </div>

        {trabajadoresQ.loading ? (
          <Esqueleto filas={5} />
        ) : trabajadores.length === 0 ? (
          <EstadoVacio
            icono={Users}
            titulo="Aún no hay personal registrado"
            descripcion="Registra a tu gente de obra (de planta, o por hora si es patacaliente) para asignarla a obras y liquidar su nómina. Empieza por el primero."
          >
            <button onClick={() => setEditando('nuevo')} className={`${BTN_PRIMARY} h-9`}>
              <UserPlus className="size-4" /> Registrar el primer trabajador
            </button>
          </EstadoVacio>
        ) : visibles.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">Ningún trabajador coincide con el filtro.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {visibles.map((t) => (
              <TrabajadorFila key={t.id} trabajador={t} onEditar={() => setEditando(t)} onCambio={trabajadoresQ.refetch} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

function TrabajadorFila({ trabajador, onEditar, onCambio }) {
  const t = metaTipo(trabajador.tipo_vinculacion)
  const [ocupado, setOcupado] = useState(false)
  const patacaliente = trabajador.tipo_vinculacion === 'PATACALIENTE'

  async function alternarActivo() {
    setOcupado(true)
    try {
      const res = await api(`/trabajadores/${trabajador.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ activo: !trabajador.activo }),
      })
      if (!res.ok) { toast.error('No se pudo cambiar el estado del trabajador'); return }
      toast.success(trabajador.activo ? 'Trabajador desactivado' : 'Trabajador activado')
      onCambio()
    } catch { toast.error('Error de conexión') } finally { setOcupado(false) }
  }

  return (
    <li className={`flex items-center gap-3 px-4 py-2.5 ${!trabajador.activo ? 'opacity-60' : ''}`}>
      <span className="grid size-9 shrink-0 place-items-center rounded-full bg-surface-2 text-[11px] font-semibold text-muted-foreground">
        {`${trabajador.nombres?.[0] || ''}${trabajador.apellidos?.[0] || ''}`.toUpperCase() || '?'}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[13px] font-medium text-foreground">{trabajador.nombres} {trabajador.apellidos}</span>
          <Semaforo tono={t.tono}>{t.label}</Semaforo>
          {!trabajador.activo && <Semaforo tono="gris">Inactivo</Semaforo>}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11px] text-muted-foreground">
          <span className="truncate text-secondary-foreground">{trabajador.cargo}</span>
          <span className="tabular">· {trabajador.tipo_documento || 'CC'} {trabajador.documento}</span>
        </div>
      </div>

      <div className="hidden shrink-0 text-right sm:block">
        {patacaliente
          ? trabajador.tarifa_hora != null && <div className="tabular text-[13px] font-semibold text-foreground">{cop(Number(trabajador.tarifa_hora))}<span className="text-[10px] font-normal text-muted-foreground">/h</span></div>
          : trabajador.salario_base != null && <div className="tabular text-[13px] font-semibold text-foreground">{cop(Number(trabajador.salario_base))}<span className="text-[10px] font-normal text-muted-foreground">/mes</span></div>}
      </div>

      <button onClick={onEditar} aria-label={`Editar ${trabajador.nombres} ${trabajador.apellidos}`}
        className="grid size-8 shrink-0 place-items-center rounded-md border border-border bg-surface text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground">
        <Pencil className="size-4" />
      </button>
      <button onClick={alternarActivo} disabled={ocupado}
        aria-label={trabajador.activo ? `Desactivar ${trabajador.nombres} ${trabajador.apellidos}` : `Activar ${trabajador.nombres} ${trabajador.apellidos}`}
        className={`grid size-8 shrink-0 place-items-center rounded-md border border-border bg-surface transition-colors ${trabajador.activo ? 'text-muted-foreground hover:bg-surface-2 hover:text-foreground' : 'text-success hover:bg-success/10'}`}>
        <Power className="size-4" />
      </button>
    </li>
  )
}

// ── Formulario de alta/edición ──────────────────────────────────────────────────────────────────
function TrabajadorForm({ trabajador, onClose, onGuardado }) {
  const edicion = !!trabajador
  const [f, setF] = useState({
    tipo_vinculacion: trabajador?.tipo_vinculacion || 'DIRECTO',
    tipo_documento: trabajador?.tipo_documento || 'CC',
    documento: trabajador?.documento || '',
    nombres: trabajador?.nombres || '',
    apellidos: trabajador?.apellidos || '',
    cargo: trabajador?.cargo || '',
    telefono: trabajador?.telefono || '',
    email: trabajador?.email || '',
    salario_base: trabajador?.salario_base != null ? String(trabajador.salario_base) : '',
    tarifa_hora: trabajador?.tarifa_hora != null ? String(trabajador.tarifa_hora) : '',
    fecha_ingreso: trabajador?.fecha_ingreso || '',
  })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF((prev) => ({ ...prev, [k]: e.target.value }))
  const directo = f.tipo_vinculacion === 'DIRECTO'

  async function guardar() {
    if (!f.nombres.trim() || !f.apellidos.trim()) { toast.error('Nombres y apellidos son obligatorios'); return }
    if (!f.documento.trim()) { toast.error('El documento es obligatorio'); return }
    if (!f.cargo.trim()) { toast.error('El cargo es obligatorio'); return }
    if (directo && f.salario_base && !(Number(f.salario_base) >= 0)) { toast.error('Salario base inválido'); return }
    if (!directo && f.tarifa_hora && !(Number(f.tarifa_hora) >= 0)) { toast.error('Tarifa por hora inválida'); return }

    const payload = {
      tipo_vinculacion: f.tipo_vinculacion,
      tipo_documento: f.tipo_documento,
      documento: f.documento.trim(),
      nombres: f.nombres.trim(),
      apellidos: f.apellidos.trim(),
      cargo: f.cargo.trim(),
      telefono: f.telefono.trim() || null,
      email: f.email.trim() || null,
    }
    // El campo económico de su naturaleza (F2.9): si quedó VACÍO en edición se OMITE — el null
    // explícito borraba el salario persistido y la siguiente liquidación reventaba con 422
    // TrabajadorNoLiquidable (hallazgo F1). El del OTRO tipo va null solo al crear o al cambiar de
    // tipo de vinculación (ahí sí "no aplica").
    const cambioTipo = edicion && trabajador?.tipo_vinculacion !== f.tipo_vinculacion
    if (directo) {
      if (f.salario_base) payload.salario_base = Number(f.salario_base)
      else if (!edicion) payload.salario_base = null
      if (!edicion || cambioTipo) payload.tarifa_hora = null
    } else {
      if (f.tarifa_hora) payload.tarifa_hora = Number(f.tarifa_hora)
      else if (!edicion) payload.tarifa_hora = null
      if (!edicion || cambioTipo) payload.salario_base = null
    }
    if (f.fecha_ingreso) payload.fecha_ingreso = f.fecha_ingreso

    setEnviando(true)
    try {
      const res = edicion
        ? await api(`/trabajadores/${trabajador.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
        : await api('/trabajadores', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (res.status === 409) { toast.error('Ya existe un trabajador con ese documento'); return }
      if (!res.ok) { toast.error(edicion ? 'No se pudo guardar el trabajador' : 'No se pudo crear el trabajador'); return }
      toast.success(edicion ? 'Trabajador actualizado' : 'Trabajador creado')
      onGuardado()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-4">
      <h2 className="mb-3 inline-flex items-center gap-1.5 text-sm font-semibold">
        <UserPlus className="size-4" aria-hidden="true" /> {edicion ? 'Editar trabajador' : 'Nuevo trabajador'}
      </h2>

      {/* Tipo de vinculación: segmentado (define qué campo económico se pide). */}
      <div className="mb-3">
        <span className="mb-1 block text-[11px] font-medium text-secondary-foreground">Tipo de vinculación</span>
        <div role="group" aria-label="Tipo de vinculación" className="inline-flex rounded-md border border-border p-0.5">
          {Object.entries(VINCULACION).map(([valor, meta]) => (
            <button key={valor} type="button" onClick={() => setF((prev) => ({ ...prev, tipo_vinculacion: valor }))}
              aria-pressed={f.tipo_vinculacion === valor}
              className={`h-8 rounded px-3 text-[12px] font-medium transition-colors duration-fast ${
                f.tipo_vinculacion === valor ? 'bg-primary-soft text-primary' : 'text-muted-foreground hover:text-foreground'
              }`}>
              {meta.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Campo label="Nombres" requerido>
          <Input value={f.nombres} onChange={set('nombres')} placeholder="Juan Camilo" className="h-9" />
        </Campo>
        <Campo label="Apellidos" requerido>
          <Input value={f.apellidos} onChange={set('apellidos')} placeholder="Ríos Vélez" className="h-9" />
        </Campo>
        <Campo label="Cargo" requerido>
          <Input value={f.cargo} onChange={set('cargo')} placeholder="Operador vibrocompactador" className="h-9" />
        </Campo>
        <Campo label="Tipo de documento">
          <select value={f.tipo_documento} onChange={set('tipo_documento')} className={SELECT_CLS}>
            {TIPOS_DOC.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </Campo>
        <Campo label="Documento" requerido>
          <Input value={f.documento} onChange={set('documento')} placeholder="1000123456" className="h-9 tabular" />
        </Campo>
        <Campo label="Fecha de ingreso">
          <Input type="date" value={f.fecha_ingreso} onChange={set('fecha_ingreso')} className="h-9" />
        </Campo>
        <Campo label="Teléfono">
          <Input value={f.telefono} onChange={set('telefono')} placeholder="Opcional" className="h-9" />
        </Campo>
        <Campo label="Correo">
          <Input value={f.email} onChange={set('email')} placeholder="Opcional" className="h-9" />
        </Campo>
        {/* Campo económico según la naturaleza (progressive disclosure). */}
        {directo ? (
          <Campo label="Salario base" hint="Mensual, sin auxilio de transporte.">
            <Input type="number" inputMode="numeric" value={f.salario_base} onChange={set('salario_base')} placeholder="0" className="h-9 tabular" />
          </Campo>
        ) : (
          <Campo label="Tarifa por hora" hint="Sin deducciones ni prestaciones.">
            <Input type="number" inputMode="numeric" value={f.tarifa_hora} onChange={set('tarifa_hora')} placeholder="0" className="h-9 tabular" />
          </Campo>
        )}
      </div>
      <div className="mt-4 flex items-center justify-end gap-2">
        <button onClick={onClose} className={`${BTN_OUTLINE} h-10`}>Cancelar</button>
        <button onClick={guardar} disabled={enviando} className={`${BTN_PRIMARY} h-10`}>
          {enviando ? 'Guardando…' : edicion ? 'Guardar cambios' : 'Crear trabajador'}
        </button>
      </div>
    </Card>
  )
}
