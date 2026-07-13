/*
 * FormAsignacionMaquina — formulario inline (colapsable) para asignar una máquina a una obra.
 * Clon estructural de FormMantenimiento (FichaMaquina.jsx): caja bordeada + grid de Campos + botones.
 *
 * Se usa en DOS sitios: en el Planeado del calendario (elige máquina de la lista) y en la ficha de la
 * máquina (máquina FIJA por prop `maquinaFija`, sin select). El backend aplica los defaults de la
 * máquina cuando precio_hora/minimo_horas viajan vacíos, así que aquí un campo vacío NO se envía.
 *
 * POST /maquinas/{id}/asignaciones {obra_id, fecha_inicio?, fecha_fin?, precio_hora?, minimo_horas?,
 * operador_id?} → 201. 409 (solape u obra liquidada) → se muestra el `detail` del backend. Solo admin
 * (el llamador ya gatea la visibilidad). SIN inventar cifras: el dinero es opcional y explícito.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { useFetch } from '@/components/shared.jsx'
import { Campo, SELECT_CLS, BTN_PRIMARY, BTN_OUTLINE } from '../comunes.jsx'
import { hoyStrCO } from './util.js'

const arr = (x) => (Array.isArray(x) ? x : [])
const labelMaq = (m) => (m.codigo ? `${m.codigo} · ${m.nombre}` : m.nombre)
const labelTrab = (t) => `${t.nombres || ''} ${t.apellidos || ''}`.trim() || `#${t.id}`

// Lee el `detail` de un error del backend (409/404) sin romper si el body no es JSON.
async function detalleError(res) {
  try { const b = await res.json(); return typeof b?.detail === 'string' ? b.detail : null } catch { return null }
}

export default function FormAsignacionMaquina({ maquinaFija, fechaInicioDefault, onExito, onCancelar }) {
  const maquinasQ = useFetch(maquinaFija ? null : '/maquinas')
  const obrasQ = useFetch('/obras')
  const trabajadoresQ = useFetch('/trabajadores')
  const [enviando, setEnviando] = useState(false)
  const [f, setF] = useState({
    maquina_id: '', obra_id: '', operador_id: '',
    fecha_inicio: fechaInicioDefault || hoyStrCO(), fecha_fin: '', precio_hora: '', minimo_horas: '',
  })
  const set = (k) => (e) => setF((prev) => ({ ...prev, [k]: e.target.value }))

  async function guardar() {
    const maquinaId = maquinaFija ? maquinaFija.id : f.maquina_id
    if (!maquinaId) { toast.error('Elige la máquina'); return }
    if (!f.obra_id) { toast.error('Elige la obra'); return }
    if (f.fecha_fin && f.fecha_fin < f.fecha_inicio) { toast.error('La fecha fin no puede ser anterior al inicio'); return }
    const payload = {
      obra_id: Number(f.obra_id),
      fecha_inicio: f.fecha_inicio,
      ...(f.fecha_fin ? { fecha_fin: f.fecha_fin } : {}),
      ...(f.operador_id ? { operador_id: Number(f.operador_id) } : {}),
      ...(f.precio_hora !== '' ? { precio_hora: Number(f.precio_hora) } : {}),
      ...(f.minimo_horas !== '' ? { minimo_horas: Number(f.minimo_horas) } : {}),
    }
    setEnviando(true)
    try {
      const res = await api(`/maquinas/${maquinaId}/asignaciones`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      if (!res.ok) { toast.error((await detalleError(res)) || 'No se pudo crear la asignación'); return }
      toast.success('Máquina asignada')
      onExito?.()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <div className="mb-1 rounded-md border border-border bg-surface-2/60 p-3">
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        {maquinaFija ? (
          <Campo label="Máquina" className="sm:col-span-2">
            <input value={labelMaq(maquinaFija)} readOnly className={`${SELECT_CLS} text-muted-foreground`} />
          </Campo>
        ) : (
          <Campo label="Máquina" requerido>
            <select value={f.maquina_id} onChange={set('maquina_id')} className={SELECT_CLS}>
              <option value="">Elige…</option>
              {arr(maquinasQ.data).map((m) => <option key={m.id} value={m.id}>{labelMaq(m)}</option>)}
            </select>
          </Campo>
        )}
        <Campo label="Obra" requerido>
          <select value={f.obra_id} onChange={set('obra_id')} className={SELECT_CLS}>
            <option value="">Elige…</option>
            {/* Una obra LIQUIDADA no admite asignaciones (409 del backend) — mejor no ofrecerla. */}
            {arr(obrasQ.data).filter((o) => o.estado !== 'LIQUIDADA').map((o) => <option key={o.id} value={o.id}>{o.nombre}</option>)}
          </select>
        </Campo>
        <Campo label="Operador" hint="Opcional.">
          <select value={f.operador_id} onChange={set('operador_id')} className={SELECT_CLS}>
            <option value="">Sin operador</option>
            {arr(trabajadoresQ.data).map((t) => <option key={t.id} value={t.id}>{labelTrab(t)}</option>)}
          </select>
        </Campo>
        <Campo label="Fecha inicio">
          <input type="date" value={f.fecha_inicio} onChange={set('fecha_inicio')} className={SELECT_CLS} />
        </Campo>
        <Campo label="Fecha fin" hint="Vacío = sin cierre previsto.">
          <input type="date" value={f.fecha_fin} onChange={set('fecha_fin')} className={SELECT_CLS} />
        </Campo>
        <Campo label="Precio / hora" hint="Vacío = default de la máquina.">
          <input type="number" inputMode="numeric" value={f.precio_hora} onChange={set('precio_hora')} placeholder="default de la máquina" className={`${SELECT_CLS} tabular`} />
        </Campo>
        <Campo label="Mínimo horas" hint="Vacío = default de la máquina.">
          <input type="number" inputMode="numeric" value={f.minimo_horas} onChange={set('minimo_horas')} placeholder="default de la máquina" className={`${SELECT_CLS} tabular`} />
        </Campo>
      </div>
      <div className="mt-3 flex items-center justify-end gap-2">
        <button type="button" onClick={onCancelar} className={`${BTN_OUTLINE} h-8 cursor-pointer`}>Cancelar</button>
        <button type="button" onClick={guardar} disabled={enviando} className={`${BTN_PRIMARY} h-8 cursor-pointer`}>
          {enviando ? 'Guardando…' : 'Asignar máquina'}
        </button>
      </div>
    </div>
  )
}
