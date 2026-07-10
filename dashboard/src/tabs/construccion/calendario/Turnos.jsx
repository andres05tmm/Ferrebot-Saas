/*
 * Turnos.jsx — desglose de rotación de operadores de un parte de horas de máquina.
 *
 * Una máquina puede rotar operadores el mismo día (Juan 8:00–13:00 · 5 h, luego Pedro 14:00–17:00 · 3 h).
 * El parte se conserva como AGREGADO del día (total = suma de turnos, mínimo facturable una vez al día);
 * este componente solo PRESENTA los turnos que trae el parte. Se comparte entre el detalle del día del
 * calendario (DetalleDia) y el kárdex de la ficha de máquina (FichaMaquina): misma sublínea compacta.
 *
 * `turnos`: [{ id?, operador, hora_inicio?, hora_fin?, horas }]. Lista vacía → no renderiza nada
 * (partes legacy sin rotación caen al display de cabecera del llamador). Sin dinero: solo quién y cuánto.
 */
import { franjaTurno, h } from './util.js'

const arr = (x) => (Array.isArray(x) ? x : [])

export default function TurnosSublineas({ turnos }) {
  const lista = arr(turnos)
  if (lista.length === 0) return null
  return (
    <ul className="mt-1 space-y-0.5 border-l border-border-subtle pl-2.5">
      {lista.map((t, i) => {
        const franja = franjaTurno(t.hora_inicio, t.hora_fin)
        return (
          <li key={t.id ?? i} className="flex flex-wrap items-baseline gap-x-1.5 text-[11px] text-muted-foreground">
            <span className="text-secondary-foreground">{t.operador || 'Sin operador'}</span>
            {franja && <span className="tabular">· {franja}</span>}
            <span className="tabular">· {h(t.horas)}</span>
          </li>
        )
      })}
    </ul>
  )
}
