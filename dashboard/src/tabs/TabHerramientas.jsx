/*
 * TabHerramientas — herramienta menor del vertical construcción (Fase 1, flag `herramientas`). CRUD
 * compacto: a diferencia de las máquinas, la herramienta no se factura por hora; se lleva por `cantidad`
 * y una `ubicacion_actual` de texto libre (obra o bodega), con su estado visible.
 *
 * Contrato de API (pinneado): /api/v1/herramientas — GET lista, POST crea, GET /{id}, PATCH /{id},
 * DELETE /{id} = soft delete. Campos JSON = columnas del ORM en español (codigo, nombre, categoria,
 * cantidad, ubicacion_actual, estado, valor_reposicion…). Filtro por estado en cliente. Live: re-fetch
 * ante 'reconnected'.
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { Wrench, Plus, Search, Pencil, Trash2, MapPin } from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch, cop, num } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Semaforo, Chips, Campo, EstadoVacio, Esqueleto, BTN_PRIMARY, BTN_OUTLINE, SELECT_CLS } from './construccion/comunes.jsx'

// Estado de herramienta (enum del ORM) → tono + etiqueta.
const HERRAMIENTA = {
  DISPONIBLE:    { tono: 'verde', label: 'Disponible' },
  EN_OBRA:       { tono: 'azul',  label: 'En obra' },
  MANTENIMIENTO: { tono: 'ambar', label: 'Mantenimiento' },
  PERDIDA:       { tono: 'rojo',  label: 'Perdida' },
  BAJA:          { tono: 'gris',  label: 'De baja' },
}
const ORDEN_ESTADOS = ['DISPONIBLE', 'EN_OBRA', 'MANTENIMIENTO', 'PERDIDA', 'BAJA']

function metaEstado(estado) {
  return HERRAMIENTA[estado] || { tono: 'gris', label: estado || '—' }
}

export default function TabHerramientas() {
  const { refreshKey } = useOutletContext() ?? {}
  const herramientasQ = useFetch('/herramientas', [refreshKey])
  useRealtimeEvent(['reconnected'], herramientasQ.refetch)

  const [q, setQ] = useState('')
  const [estado, setEstado] = useState(null)
  const [editando, setEditando] = useState(null)  // null | 'nueva' | herramienta

  const herramientas = Array.isArray(herramientasQ.data) ? herramientasQ.data : []

  const conteos = herramientas.reduce((acc, h) => { acc[h.estado] = (acc[h.estado] || 0) + 1; return acc }, {})
  const chips = [
    { valor: null, label: 'Todas', conteo: herramientas.length },
    ...ORDEN_ESTADOS
      .filter((e) => conteos[e])
      .map((e) => ({ valor: e, label: metaEstado(e).label, tono: metaEstado(e).tono, conteo: conteos[e] })),
  ]

  const termino = q.trim().toLowerCase()
  const visibles = herramientas.filter((h) => {
    if (estado && h.estado !== estado) return false
    if (!termino) return true
    return [h.nombre, h.codigo, h.categoria, h.ubicacion_actual].filter(Boolean).some((s) => String(s).toLowerCase().includes(termino))
  })

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[200px] flex-1">
            <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar por nombre, código, categoría o ubicación…" aria-label="Buscar herramienta" className="pl-9" />
          </div>
          <button onClick={() => setEditando(editando === 'nueva' ? null : 'nueva')} className={`${BTN_PRIMARY} h-9 shrink-0`}>
            <Plus className="size-4" /> Nueva herramienta
          </button>
        </div>
        {chips.length > 1 && (
          <div className="mt-2.5">
            <Chips opciones={chips} valor={estado} onChange={setEstado} ariaLabel="Filtrar herramientas por estado" />
          </div>
        )}
      </Card>

      {editando && (
        <HerramientaForm
          herramienta={editando === 'nueva' ? null : editando}
          onClose={() => setEditando(null)}
          onGuardada={() => { setEditando(null); herramientasQ.refetch() }}
        />
      )}

      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border-subtle flex items-center gap-2">
          <Wrench className="size-4 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Herramienta {herramientas.length > 0 && <span className="tabular">· {visibles.length}</span>}
          </h2>
        </div>

        {herramientasQ.loading ? (
          <Esqueleto filas={5} />
        ) : herramientas.length === 0 ? (
          <EstadoVacio
            icono={Wrench}
            titulo="Sin herramienta registrada"
            descripcion="Lleva el control de la herramienta menor (pulidoras, taladros, formaletas…): cantidad, dónde está y en qué estado. Empieza registrando la primera."
          >
            <button onClick={() => setEditando('nueva')} className={`${BTN_PRIMARY} h-9`}>
              <Plus className="size-4" /> Registrar la primera herramienta
            </button>
          </EstadoVacio>
        ) : visibles.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">Ninguna herramienta coincide con el filtro.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {visibles.map((h) => (
              <HerramientaFila key={h.id} herramienta={h} onEditar={() => setEditando(h)} onCambio={herramientasQ.refetch} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

function HerramientaFila({ herramienta, onEditar, onCambio }) {
  const est = metaEstado(herramienta.estado)

  async function eliminar() {
    if (!window.confirm(`¿Dar de baja "${herramienta.nombre}"? Dejará de aparecer en el listado.`)) return
    try {
      const res = await api(`/herramientas/${herramienta.id}`, { method: 'DELETE' })
      if (res.ok) { toast.success('Herramienta dada de baja'); onCambio() }
      else toast.error('No se pudo dar de baja la herramienta')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <li className="flex items-center gap-3 px-4 py-2.5">
      <span className="grid size-8 shrink-0 place-items-center rounded-md bg-surface-2 text-muted-foreground">
        <Wrench className="size-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[13px] font-medium text-foreground">{herramienta.nombre}</span>
          <Semaforo tono={est.tono}>{est.label}</Semaforo>
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11px] text-muted-foreground">
          <span className="tabular font-medium text-secondary-foreground">{herramienta.codigo}</span>
          {herramienta.categoria && <span className="truncate">· {herramienta.categoria}</span>}
          {herramienta.ubicacion_actual && <span className="inline-flex items-center gap-1 truncate"><MapPin className="size-3" aria-hidden="true" />{herramienta.ubicacion_actual}</span>}
        </div>
      </div>

      <div className="shrink-0 text-right">
        <div className="tabular text-[13px] font-semibold text-foreground">{num(herramienta.cantidad)}<span className="text-[10px] font-normal text-muted-foreground"> und</span></div>
        {herramienta.valor_reposicion != null && (
          <div className="tabular text-[10px] text-muted-foreground">rep. {cop(Number(herramienta.valor_reposicion))}</div>
        )}
      </div>

      <button onClick={onEditar} aria-label={`Editar ${herramienta.nombre}`}
        className="grid size-8 shrink-0 place-items-center rounded-md border border-border bg-surface text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground">
        <Pencil className="size-4" />
      </button>
      <button onClick={eliminar} aria-label={`Dar de baja ${herramienta.nombre}`}
        className="grid size-8 shrink-0 place-items-center rounded-md border border-border bg-surface text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive">
        <Trash2 className="size-4" />
      </button>
    </li>
  )
}

// ── Formulario de alta/edición ──────────────────────────────────────────────────────────────────
function HerramientaForm({ herramienta, onClose, onGuardada }) {
  const edicion = !!herramienta
  const [f, setF] = useState({
    codigo: herramienta?.codigo || '',
    nombre: herramienta?.nombre || '',
    categoria: herramienta?.categoria || '',
    cantidad: herramienta?.cantidad != null ? String(herramienta.cantidad) : '1',
    ubicacion_actual: herramienta?.ubicacion_actual || '',
    estado: herramienta?.estado || 'DISPONIBLE',
    valor_reposicion: herramienta?.valor_reposicion != null ? String(herramienta.valor_reposicion) : '',
    notas: herramienta?.notas || '',
  })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF((prev) => ({ ...prev, [k]: e.target.value }))

  async function guardar() {
    if (!f.codigo.trim() || !f.nombre.trim()) { toast.error('Código y nombre son obligatorios'); return }
    const payload = {
      codigo: f.codigo.trim(),
      nombre: f.nombre.trim(),
      categoria: f.categoria.trim() || null,
      cantidad: f.cantidad ? Number(f.cantidad) : 1,
      ubicacion_actual: f.ubicacion_actual.trim() || null,
      estado: f.estado,
      valor_reposicion: f.valor_reposicion ? Number(f.valor_reposicion) : null,
      notas: f.notas.trim() || null,
    }
    setEnviando(true)
    try {
      const res = edicion
        ? await api(`/herramientas/${herramienta.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
        : await api('/herramientas', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (res.status === 409) { toast.error('Ya existe una herramienta con ese código'); return }
      if (!res.ok) { toast.error(edicion ? 'No se pudo guardar la herramienta' : 'No se pudo crear la herramienta'); return }
      toast.success(edicion ? 'Herramienta actualizada' : 'Herramienta creada')
      onGuardada()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-4">
      <h2 className="mb-3 inline-flex items-center gap-1.5 text-sm font-semibold">
        <Wrench className="size-4" aria-hidden="true" /> {edicion ? 'Editar herramienta' : 'Nueva herramienta'}
      </h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Campo label="Código" requerido>
          <Input value={f.codigo} onChange={set('codigo')} placeholder="H-001" className="h-9" />
        </Campo>
        <Campo label="Nombre" requerido className="lg:col-span-2">
          <Input value={f.nombre} onChange={set('nombre')} placeholder="Pulidora Bosch 4½”" className="h-9" />
        </Campo>
        <Campo label="Categoría">
          <Input value={f.categoria} onChange={set('categoria')} placeholder="Eléctrica, manual…" className="h-9" />
        </Campo>
        <Campo label="Cantidad" requerido>
          <Input type="number" inputMode="numeric" value={f.cantidad} onChange={set('cantidad')} placeholder="1" className="h-9 tabular" />
        </Campo>
        <Campo label="Estado">
          <select value={f.estado} onChange={set('estado')} className={SELECT_CLS}>
            {ORDEN_ESTADOS.map((e) => <option key={e} value={e}>{metaEstado(e).label}</option>)}
          </select>
        </Campo>
        <Campo label="Ubicación actual" className="sm:col-span-2">
          <Input value={f.ubicacion_actual} onChange={set('ubicacion_actual')} placeholder="Bodega principal u obra" className="h-9" />
        </Campo>
        <Campo label="Valor de reposición" hint="Costo de reemplazo si se pierde o daña.">
          <Input type="number" inputMode="numeric" value={f.valor_reposicion} onChange={set('valor_reposicion')} placeholder="Opcional" className="h-9 tabular" />
        </Campo>
        <Campo label="Notas" className="sm:col-span-2 lg:col-span-3">
          <Input value={f.notas} onChange={set('notas')} placeholder="Observaciones" className="h-9" />
        </Campo>
      </div>
      <div className="mt-4 flex items-center justify-end gap-2">
        <button onClick={onClose} className={`${BTN_OUTLINE} h-10`}>Cancelar</button>
        <button onClick={guardar} disabled={enviando} className={`${BTN_PRIMARY} h-10`}>
          {enviando ? 'Guardando…' : edicion ? 'Guardar cambios' : 'Crear herramienta'}
        </button>
      </div>
    </Card>
  )
}
