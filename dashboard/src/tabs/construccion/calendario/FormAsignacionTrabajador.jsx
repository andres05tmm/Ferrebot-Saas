/*
 * FormAsignacionTrabajador — espejo SIN dinero de FormAsignacionMaquina: asigna un trabajador a una obra.
 * Mismo molde inline (caja + grid de Campos + botones). Se usa desde el Planeado del calendario; admite
 * trabajador FIJO por prop `trabajadorFija` (sin select) para reuso futuro desde una ficha de personal.
 *
 * POST /trabajadores/{id}/asignaciones {obra_id, fecha_inicio?, fecha_fin?} → 201. 409 (solape u obra
 * liquidada) → se muestra el `detail`. Solo admin (el llamador gatea la visibilidad).
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { useFetch } from '@/components/shared.jsx'
import { Campo, SELECT_CLS, BTN_PRIMARY, BTN_OUTLINE } from '../comunes.jsx'
import { hoyStrCO } from './util.js'

const arr = (x) => (Array.isArray(x) ? x : [])
const labelTrab = (t) => `${t.nombres || ''} ${t.apellidos || ''}`.trim() || `#${t.id}`

async function detalleError(res) {
  try { const b = await res.json(); return typeof b?.detail === 'string' ? b.detail : null } catch { return null }
}

export default function FormAsignacionTrabajador({ trabajadorFija, fechaInicioDefault, onExito, onCancelar }) {
  const trabajadoresQ = useFetch(trabajadorFija ? null : '/trabajadores')
  const obrasQ = useFetch('/obras')
  const [enviando, setEnviando] = useState(false)
  const [f, setF] = useState({
    trabajador_id: '', obra_id: '', fecha_inicio: fechaInicioDefault || hoyStrCO(), fecha_fin: '',
  })
  const set = (k) => (e) => setF((prev) => ({ ...prev, [k]: e.target.value }))

  async function guardar() {
    const trabajadorId = trabajadorFija ? trabajadorFija.id : f.trabajador_id
    if (!trabajadorId) { toast.error('Elige el trabajador'); return }
    if (!f.obra_id) { toast.error('Elige la obra'); return }
    if (f.fecha_fin && f.fecha_fin < f.fecha_inicio) { toast.error('La fecha fin no puede ser anterior al inicio'); return }
    const payload = {
      obra_id: Number(f.obra_id),
      fecha_inicio: f.fecha_inicio,
      ...(f.fecha_fin ? { fecha_fin: f.fecha_fin } : {}),
    }
    setEnviando(true)
    try {
      const res = await api(`/trabajadores/${trabajadorId}/asignaciones`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      if (!res.ok) { toast.error((await detalleError(res)) || 'No se pudo crear la asignación'); return }
      toast.success('Trabajador asignado')
      onExito?.()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <div className="mb-1 rounded-md border border-border bg-surface-2/60 p-3">
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        {trabajadorFija ? (
          <Campo label="Trabajador" className="sm:col-span-2">
            <input value={labelTrab(trabajadorFija)} readOnly className={`${SELECT_CLS} text-muted-foreground`} />
          </Campo>
        ) : (
          <Campo label="Trabajador" requerido>
            <select value={f.trabajador_id} onChange={set('trabajador_id')} className={SELECT_CLS}>
              <option value="">Elige…</option>
              {arr(trabajadoresQ.data).map((t) => <option key={t.id} value={t.id}>{labelTrab(t)}</option>)}
            </select>
          </Campo>
        )}
        <Campo label="Obra" requerido>
          <select value={f.obra_id} onChange={set('obra_id')} className={SELECT_CLS}>
            <option value="">Elige…</option>
            {arr(obrasQ.data).map((o) => <option key={o.id} value={o.id}>{o.nombre}</option>)}
          </select>
        </Campo>
        <Campo label="Fecha inicio">
          <input type="date" value={f.fecha_inicio} onChange={set('fecha_inicio')} className={SELECT_CLS} />
        </Campo>
        <Campo label="Fecha fin" hint="Vacío = sin cierre previsto.">
          <input type="date" value={f.fecha_fin} onChange={set('fecha_fin')} className={SELECT_CLS} />
        </Campo>
      </div>
      <div className="mt-3 flex items-center justify-end gap-2">
        <button type="button" onClick={onCancelar} className={`${BTN_OUTLINE} h-8 cursor-pointer`}>Cancelar</button>
        <button type="button" onClick={guardar} disabled={enviando} className={`${BTN_PRIMARY} h-8 cursor-pointer`}>
          {enviando ? 'Guardando…' : 'Asignar trabajador'}
        </button>
      </div>
    </div>
  )
}
