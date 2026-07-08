/*
 * TabMaquinas — parque de maquinaria del vertical construcción (Fase 1, flag `maquinaria`). Activos que
 * se alquilan/facturan por HORA: cada máquina tiene un `precio_hora_default` y un `minimo_horas_factura`
 * (piso facturable), más un `costo_operacion_hora` interno para la rentabilidad neta por obra (Fase 3).
 * Aquí: listar con su estado visible (DISPONIBLE / OCUPADA / MANTENIMIENTO / DAÑADA / BAJA) y crear.
 * Cada fila expande a una FICHA rica (FichaMaquina): asignaciones, kárdex de horas, mantenimientos y las
 * acciones de admin (cambio de estado, editar, dar de baja) — mismo patrón fila-expandible que ObraDetalle.
 *
 * Contrato de API (pinneado): /api/v1/maquinas — GET lista, POST crea, GET /{id}, PATCH /{id} (incl.
 * cambio de `estado`), DELETE /{id} = soft delete. Campos JSON = columnas del ORM en español (codigo,
 * nombre, tipo, estado, precio_hora_default, minimo_horas_factura, costo_operacion_hora…). El filtro por
 * estado se resuelve en cliente (parque chico). Live: re-fetch ante 'reconnected'.
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { Truck, Plus, Search, Gauge, ChevronDown, ChevronRight } from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch, cop, num } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Semaforo, Chips, Campo, EstadoVacio, Esqueleto, BTN_PRIMARY, BTN_OUTLINE } from './construccion/comunes.jsx'
import FichaMaquina from './construccion/FichaMaquina.jsx'

// Estado de máquina (enum del ORM) → tono + etiqueta. OCUPADA se rotula "En obra" (más claro para el
// operador); el VALOR sigue siendo OCUPADA, tal cual el ORM.
const MAQUINA = {
  DISPONIBLE:    { tono: 'verde', label: 'Disponible' },
  OCUPADA:       { tono: 'azul',  label: 'En obra' },
  MANTENIMIENTO: { tono: 'ambar', label: 'Mantenimiento' },
  DAÑADA:        { tono: 'rojo',  label: 'Dañada' },
  BAJA:          { tono: 'gris',  label: 'De baja' },
}
const ORDEN_ESTADOS = ['DISPONIBLE', 'OCUPADA', 'MANTENIMIENTO', 'DAÑADA', 'BAJA']

function metaEstado(estado) {
  return MAQUINA[estado] || { tono: 'gris', label: estado || '—' }
}

export default function TabMaquinas() {
  const { refreshKey } = useOutletContext() ?? {}
  const maquinasQ = useFetch('/maquinas', [refreshKey])
  // Obras UNA sola vez (no por ficha): mapa obra_id→nombre para resolver los nombres del kárdex y las
  // asignaciones sin N+1. Degrada en silencio (mapa vacío → "Obra #id") si el vendedor no puede listarlas.
  const obrasQ = useFetch('/obras', [refreshKey])
  useRealtimeEvent(['reconnected'], maquinasQ.refetch)

  const admin = useAuth().isAdmin()
  const obrasNombre = (Array.isArray(obrasQ.data) ? obrasQ.data : [])
    .reduce((acc, o) => { acc[o.id] = o.nombre; return acc }, {})

  const [q, setQ] = useState('')
  const [estado, setEstado] = useState(null)
  const [editando, setEditando] = useState(null)  // null | 'nueva' | maquina

  const maquinas = Array.isArray(maquinasQ.data) ? maquinasQ.data : []

  const conteos = maquinas.reduce((acc, m) => { acc[m.estado] = (acc[m.estado] || 0) + 1; return acc }, {})
  const chips = [
    { valor: null, label: 'Todas', conteo: maquinas.length },
    ...ORDEN_ESTADOS
      .filter((e) => conteos[e])
      .map((e) => ({ valor: e, label: metaEstado(e).label, tono: metaEstado(e).tono, conteo: conteos[e] })),
  ]

  const termino = q.trim().toLowerCase()
  const visibles = maquinas.filter((m) => {
    if (estado && m.estado !== estado) return false
    if (!termino) return true
    return [m.nombre, m.codigo, m.tipo, m.placa].filter(Boolean).some((s) => String(s).toLowerCase().includes(termino))
  })

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[200px] flex-1">
            <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar por nombre, código, tipo o placa…" aria-label="Buscar máquina" className="pl-9" />
          </div>
          <button onClick={() => setEditando(editando === 'nueva' ? null : 'nueva')} className={`${BTN_PRIMARY} h-9 shrink-0`}>
            <Plus className="size-4" /> Nueva máquina
          </button>
        </div>
        {chips.length > 1 && (
          <div className="mt-2.5">
            <Chips opciones={chips} valor={estado} onChange={setEstado} ariaLabel="Filtrar máquinas por estado" />
          </div>
        )}
      </Card>

      {editando && (
        <MaquinaForm
          maquina={editando === 'nueva' ? null : editando}
          onClose={() => setEditando(null)}
          onGuardada={() => { setEditando(null); maquinasQ.refetch() }}
        />
      )}

      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border-subtle flex items-center gap-2">
          <Truck className="size-4 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Parque de maquinaria {maquinas.length > 0 && <span className="tabular">· {visibles.length}</span>}
          </h2>
        </div>

        {maquinasQ.loading ? (
          <Esqueleto filas={4} />
        ) : maquinas.length === 0 ? (
          <EstadoVacio
            icono={Truck}
            titulo="El parque está vacío"
            descripcion="Registra tus máquinas (retro, vibro, volqueta…) con su precio por hora y su mínimo facturable. Desde aquí seguirás su estado y su costo por obra."
          >
            <button onClick={() => setEditando('nueva')} className={`${BTN_PRIMARY} h-9`}>
              <Plus className="size-4" /> Registrar la primera máquina
            </button>
          </EstadoVacio>
        ) : visibles.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">Ninguna máquina coincide con el filtro.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {visibles.map((m) => (
              <MaquinaFila key={m.id} maquina={m} admin={admin} obrasNombre={obrasNombre}
                onEditar={() => setEditando(m)} onCambio={maquinasQ.refetch} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

// Fila = cabecera clicable (expande) + FichaMaquina perezosa. El cambio de estado, editar y dar de baja
// viven ahora DENTRO de la ficha (no se pueden anidar controles en un <button>, y el detalle es su sitio
// natural). La cabecera colapsada conserva el vistazo: nombre, semáforo, código/tipo/placa y tarifa.
function MaquinaFila({ maquina, admin, obrasNombre, onEditar, onCambio }) {
  const [abierta, setAbierta] = useState(false)
  const est = metaEstado(maquina.estado)
  const panelId = `maquina-ficha-${maquina.id}`

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
          <Truck className="size-[18px]" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-[14px] font-medium text-foreground">{maquina.nombre}</span>
            <Semaforo tono={est.tono}>{est.label}</Semaforo>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11px] text-muted-foreground">
            <span className="tabular font-medium text-secondary-foreground">{maquina.codigo}</span>
            {maquina.tipo && <span className="truncate">· {maquina.tipo}</span>}
            {maquina.placa && <span>· {maquina.placa}</span>}
          </div>
        </div>

        {/* Tarifa por hora + mínimo facturable (vistazo rápido, oculto en móvil estrecho). */}
        <div className="hidden shrink-0 text-right sm:block">
          <div className="tabular text-[13px] font-semibold text-foreground">{cop(Number(maquina.precio_hora_default))}<span className="text-[10px] font-normal text-muted-foreground">/h</span></div>
          <div className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
            <Gauge className="size-3" aria-hidden="true" /> mín. {num(maquina.minimo_horas_factura)} h
          </div>
        </div>

        {abierta
          ? <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          : <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
      </button>

      {abierta && (
        <FichaMaquina
          id={panelId} maquina={maquina} isAdmin={admin} obrasNombre={obrasNombre}
          onEditar={onEditar} onCambio={onCambio}
        />
      )}
    </li>
  )
}

// ── Formulario de alta/edición ──────────────────────────────────────────────────────────────────
function MaquinaForm({ maquina, onClose, onGuardada }) {
  const edicion = !!maquina
  const [f, setF] = useState({
    codigo: maquina?.codigo || '',
    nombre: maquina?.nombre || '',
    tipo: maquina?.tipo || '',
    placa: maquina?.placa || '',
    serial: maquina?.serial || '',
    anio_fabricacion: maquina?.anio_fabricacion ? String(maquina.anio_fabricacion) : '',
    precio_hora_default: maquina?.precio_hora_default != null ? String(maquina.precio_hora_default) : '',
    minimo_horas_factura: maquina?.minimo_horas_factura != null ? String(maquina.minimo_horas_factura) : '1',
    costo_operacion_hora: maquina?.costo_operacion_hora != null ? String(maquina.costo_operacion_hora) : '',
    notas: maquina?.notas || '',
  })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF((prev) => ({ ...prev, [k]: e.target.value }))

  async function guardar() {
    if (!f.codigo.trim() || !f.nombre.trim() || !f.tipo.trim()) { toast.error('Código, nombre y tipo son obligatorios'); return }
    if (!(Number(f.precio_hora_default) > 0)) { toast.error('Indica un precio por hora válido'); return }
    const payload = {
      codigo: f.codigo.trim(),
      nombre: f.nombre.trim(),
      tipo: f.tipo.trim(),
      placa: f.placa.trim() || null,
      serial: f.serial.trim() || null,
      anio_fabricacion: f.anio_fabricacion ? Number(f.anio_fabricacion) : null,
      precio_hora_default: Number(f.precio_hora_default),
      minimo_horas_factura: f.minimo_horas_factura ? Number(f.minimo_horas_factura) : 1,
      costo_operacion_hora: f.costo_operacion_hora ? Number(f.costo_operacion_hora) : null,
      notas: f.notas.trim() || null,
    }
    setEnviando(true)
    try {
      const res = edicion
        ? await api(`/maquinas/${maquina.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
        : await api('/maquinas', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (res.status === 409) { toast.error('Ya existe una máquina con ese código'); return }
      if (!res.ok) { toast.error(edicion ? 'No se pudo guardar la máquina' : 'No se pudo crear la máquina'); return }
      toast.success(edicion ? 'Máquina actualizada' : 'Máquina creada')
      onGuardada()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-4">
      <h2 className="mb-3 inline-flex items-center gap-1.5 text-sm font-semibold">
        <Truck className="size-4" aria-hidden="true" /> {edicion ? 'Editar máquina' : 'Nueva máquina'}
      </h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Campo label="Código" requerido>
          <Input value={f.codigo} onChange={set('codigo')} placeholder="M-001" className="h-9" />
        </Campo>
        <Campo label="Nombre" requerido className="lg:col-span-2">
          <Input value={f.nombre} onChange={set('nombre')} placeholder="Vibrocompactador CAT CS533E" className="h-9" />
        </Campo>
        <Campo label="Tipo" requerido>
          <Input value={f.tipo} onChange={set('tipo')} placeholder="Vibrocompactador" className="h-9" />
        </Campo>
        <Campo label="Placa">
          <Input value={f.placa} onChange={set('placa')} placeholder="Opcional" className="h-9" />
        </Campo>
        <Campo label="Serial">
          <Input value={f.serial} onChange={set('serial')} placeholder="Opcional" className="h-9" />
        </Campo>
        <Campo label="Precio por hora" requerido hint="Tarifa sugerida de alquiler al cliente.">
          <Input type="number" inputMode="numeric" value={f.precio_hora_default} onChange={set('precio_hora_default')} placeholder="0" className="h-9 tabular" />
        </Campo>
        <Campo label="Mínimo de horas" hint="Piso facturable por servicio.">
          <Input type="number" inputMode="numeric" value={f.minimo_horas_factura} onChange={set('minimo_horas_factura')} placeholder="1" className="h-9 tabular" />
        </Campo>
        <Campo label="Costo interno / hora" hint="Combustible y desgaste; para la rentabilidad neta.">
          <Input type="number" inputMode="numeric" value={f.costo_operacion_hora} onChange={set('costo_operacion_hora')} placeholder="Opcional" className="h-9 tabular" />
        </Campo>
        <Campo label="Año de fabricación">
          <Input type="number" inputMode="numeric" value={f.anio_fabricacion} onChange={set('anio_fabricacion')} placeholder="Opcional" className="h-9 tabular" />
        </Campo>
        <Campo label="Notas" className="sm:col-span-2 lg:col-span-3">
          <Input value={f.notas} onChange={set('notas')} placeholder="Observaciones del equipo" className="h-9" />
        </Campo>
      </div>
      <div className="mt-4 flex items-center justify-end gap-2">
        <button onClick={onClose} className={`${BTN_OUTLINE} h-10`}>Cancelar</button>
        <button onClick={guardar} disabled={enviando} className={`${BTN_PRIMARY} h-10`}>
          {enviando ? 'Guardando…' : edicion ? 'Guardar cambios' : 'Crear máquina'}
        </button>
      </div>
    </Card>
  )
}
