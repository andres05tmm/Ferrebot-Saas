/*
 * FiltrosCalendario — conmutador de VISTA del calendario + select de entidad de esa vista.
 *
 * La vista (Todos | Obras | Máquinas | Trabajadores) enfoca el calendario en una dimensión; dentro de
 * ella, un <select> filtra por una entidad concreta (una obra, una máquina, un trabajador). Mobile-first:
 * los chips scrollean en horizontal en pantallas angostas en vez de envolver en varias filas.
 */
import { Card } from '@/components/ui/card.jsx'
import { Chips, Campo, SELECT_CLS } from '../comunes.jsx'

const VISTAS = [
  { valor: 'todos', label: 'Todos' },
  { valor: 'obras', label: 'Obras' },
  { valor: 'maquinas', label: 'Máquinas' },
  { valor: 'trabajadores', label: 'Trabajadores' },
]

export default function FiltrosCalendario({ vista, onVista, filtros, onEntidad, obras, maquinas, trabajadores }) {
  return (
    <Card className="p-3 space-y-2.5">
      {/* Wrapper scrolleable en móvil: los chips no envuelven, se deslizan (touch). */}
      <div className="-mx-1 overflow-x-auto px-1">
        <Chips opciones={VISTAS} valor={vista} onChange={onVista} ariaLabel="Ver calendario por" />
      </div>

      {vista === 'obras' && (
        <Campo label="Obra">
          <select value={filtros.obraId} onChange={(e) => onEntidad('obraId', e.target.value)} className={SELECT_CLS}>
            <option value="">Todas las obras</option>
            {obras.map((o) => <option key={o.id} value={o.id}>{o.nombre}</option>)}
          </select>
        </Campo>
      )}

      {vista === 'maquinas' && (
        <Campo label="Máquina">
          <select value={filtros.maquinaId} onChange={(e) => onEntidad('maquinaId', e.target.value)} className={SELECT_CLS}>
            <option value="">Todas las máquinas</option>
            {maquinas.map((m) => (
              <option key={m.id} value={m.id}>{m.codigo ? `${m.codigo} · ${m.nombre}` : m.nombre}</option>
            ))}
          </select>
        </Campo>
      )}

      {vista === 'trabajadores' && (
        <Campo label="Trabajador">
          <select value={filtros.trabajadorId} onChange={(e) => onEntidad('trabajadorId', e.target.value)} className={SELECT_CLS}>
            <option value="">Todos los trabajadores</option>
            {trabajadores.map((t) => (
              <option key={t.id} value={t.id}>{`${t.nombres || ''} ${t.apellidos || ''}`.trim() || `#${t.id}`}</option>
            ))}
          </select>
        </Campo>
      )}
    </Card>
  )
}
