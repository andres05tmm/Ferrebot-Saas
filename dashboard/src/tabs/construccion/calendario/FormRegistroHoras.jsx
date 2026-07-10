/*
 * FormRegistroHoras — formulario inline (colapsable) para registrar un parte/turno de horas de máquina.
 * Clon estructural de FormAsignacionMaquina: caja bordeada + grid de Campos + botones. Lo ve CUALQUIER
 * rol (el vendedor registra horas desde el campo); no gatea por admin.
 *
 * Se usa en DOS sitios: la sección Máquinas del detalle del día (elige máquina de la lista) y el kárdex de
 * la ficha (máquina FIJA por prop `maquinaFija`, sin select). Soporta ROTACIÓN de operadores: si el día ya
 * tiene un parte de (máquina, obra), enviar un operador o franja distintos agrega un TURNO nuevo y el
 * backend recalcula el total del día — la respuesta trae `horas_trabajadas` del DÍA para el toast.
 *
 * POST /maquinas/{id}/horas {obra_id, horas_trabajadas, fecha?, operador_id?, hora_inicio?, hora_fin?,
 * observaciones?} → 200/201 RegistroHorasRespuesta {turnos, horas_trabajadas, horas_facturables, ingreso,
 * replay?}. Un campo vacío NO viaja. replay:true → aviso "Ya estaba registrado" (no duplica). 409 (sin
 * asignación activa que cubra la fecha) → se muestra el `detail` del backend.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { useFetch } from '@/components/shared.jsx'
import { Campo, SELECT_CLS, BTN_PRIMARY, BTN_OUTLINE } from '../comunes.jsx'
import { hoyStrCO, h } from './util.js'

const arr = (x) => (Array.isArray(x) ? x : [])
const labelMaq = (m) => (m.codigo ? `${m.codigo} · ${m.nombre}` : m.nombre)
const labelTrab = (t) => `${t.nombres || ''} ${t.apellidos || ''}`.trim() || `#${t.id}`

// Lee el `detail` de un error del backend (409/404) sin romper si el body no es JSON.
async function detalleError(res) {
  try { const b = await res.json(); return typeof b?.detail === 'string' ? b.detail : null } catch { return null }
}

export default function FormRegistroHoras({ maquinaFija, fechaDefault, onExito, onCancelar }) {
  const maquinasQ = useFetch(maquinaFija ? null : '/maquinas')
  const obrasQ = useFetch('/obras')
  const trabajadoresQ = useFetch('/trabajadores')
  const [enviando, setEnviando] = useState(false)
  const [f, setF] = useState({
    maquina_id: '', obra_id: '', operador_id: '',
    fecha: fechaDefault || hoyStrCO(), horas_trabajadas: '', hora_inicio: '', hora_fin: '', observaciones: '',
  })
  const set = (k) => (e) => setF((prev) => ({ ...prev, [k]: e.target.value }))

  function validar(maquinaId) {
    if (!maquinaId) return 'Elige la máquina'
    if (!f.obra_id) return 'Elige la obra'
    if (f.horas_trabajadas === '' || Number(f.horas_trabajadas) <= 0) return 'Indica las horas trabajadas'
    if (f.hora_inicio && f.hora_fin && f.hora_fin <= f.hora_inicio) return 'La hora fin debe ser posterior al inicio'
    return null
  }

  async function guardar() {
    const maquinaId = maquinaFija ? maquinaFija.id : f.maquina_id
    const error = validar(maquinaId)
    if (error) { toast.error(error); return }
    const payload = {
      obra_id: Number(f.obra_id),
      horas_trabajadas: Number(f.horas_trabajadas),
      fecha: f.fecha,
      ...(f.operador_id ? { operador_id: Number(f.operador_id) } : {}),
      ...(f.hora_inicio ? { hora_inicio: f.hora_inicio } : {}),
      ...(f.hora_fin ? { hora_fin: f.hora_fin } : {}),
      ...(f.observaciones.trim() ? { observaciones: f.observaciones.trim() } : {}),
    }
    setEnviando(true)
    try {
      const res = await api(`/maquinas/${maquinaId}/horas`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      if (!res.ok) { toast.error((await detalleError(res)) || 'No se pudieron registrar las horas'); return }
      const data = await res.json().catch(() => ({}))
      if (data?.replay) toast.message('Ya estaba registrado')
      else toast.success(`${h(f.horas_trabajadas)} registradas · total del día ${h(data?.horas_trabajadas)}`)
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
            {arr(obrasQ.data).map((o) => <option key={o.id} value={o.id}>{o.nombre}</option>)}
          </select>
        </Campo>
        <Campo label="Fecha">
          <input type="date" value={f.fecha} onChange={set('fecha')} className={SELECT_CLS} />
        </Campo>
        <Campo label="Horas trabajadas" requerido>
          <input type="number" inputMode="decimal" step="0.5" min="0" value={f.horas_trabajadas}
            onChange={set('horas_trabajadas')} placeholder="Ej. 5" className={`${SELECT_CLS} tabular`} />
        </Campo>
        <Campo label="Operador" hint="Opcional. Registrar el turno de este operador.">
          <select value={f.operador_id} onChange={set('operador_id')} className={SELECT_CLS}>
            <option value="">Sin operador</option>
            {arr(trabajadoresQ.data).map((t) => <option key={t.id} value={t.id}>{labelTrab(t)}</option>)}
          </select>
        </Campo>
        <Campo label="Hora inicio" hint="Opcional.">
          <input type="time" value={f.hora_inicio} onChange={set('hora_inicio')} className={SELECT_CLS} />
        </Campo>
        <Campo label="Hora fin" hint="Opcional.">
          <input type="time" value={f.hora_fin} onChange={set('hora_fin')} className={SELECT_CLS} />
        </Campo>
        <Campo label="Observaciones" hint="Opcional." className="sm:col-span-2">
          <input value={f.observaciones} onChange={set('observaciones')} placeholder="Notas del parte" className={SELECT_CLS} />
        </Campo>
      </div>
      <div className="mt-3 flex items-center justify-end gap-2">
        <button type="button" onClick={onCancelar} className={`${BTN_OUTLINE} h-8 cursor-pointer`}>Cancelar</button>
        <button type="button" onClick={guardar} disabled={enviando} className={`${BTN_PRIMARY} h-8 cursor-pointer`}>
          {enviando ? 'Guardando…' : 'Registrar horas'}
        </button>
      </div>
    </div>
  )
}
