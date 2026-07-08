/*
 * TablaObrasRiesgo — el corazón del cockpit: las obras del portafolio ORDENADAS POR RIESGO (el backend
 * ya las manda con las rojas primero). Cada fila responde la pregunta del dueño cada mañana: ¿esta obra
 * está ganando o perdiendo plata? Columnas: obra · cliente · % de presupuesto consumido · presupuesto ·
 * gastado · utilidad · semáforo. Las obras en pérdida llevan un tinte de fondo (no un stripe: patrón de
 * la fila excedida de CarteraAlquiler) + su `Semaforo` "En pérdida" — riesgo con texto, nunca color-solo.
 *
 * Máx ~8 filas (el resto se ve en /obras, enlazado en el header). Números tabulares; tabla con scroll-x
 * propio en móvil. Solo presentación: recibe el rollup ya calculado del endpoint /obras/dashboard.
 */
import { Link } from 'react-router-dom'
import { HardHat, ArrowRight } from 'lucide-react'
import { cop } from '@/components/shared.jsx'
import { Semaforo, EstadoVacio } from '../comunes.jsx'
import { SeccionPanel, n, pctConsumido } from './piezas.jsx'

const MAX_FILAS = 8

// Semáforo del backend → tono + etiqueta de rentabilidad (color-not-only: la etiqueta lleva el sentido).
const SEMAFORO = {
  rojo:     { tono: 'rojo',  label: 'En pérdida' },
  amarillo: { tono: 'ambar', label: 'Ajustada' },
  verde:    { tono: 'verde', label: 'Rentable' },
}

function verLink(children) {
  return (
    <Link to="/obras" className="inline-flex items-center gap-1 text-[12px] font-medium text-primary transition-colors hover:text-primary-hover">
      {children} <ArrowRight className="size-3.5" aria-hidden="true" />
    </Link>
  )
}

export default function TablaObrasRiesgo({ obras = [] }) {
  const total = obras.length
  const visibles = obras.slice(0, MAX_FILAS)

  return (
    <SeccionPanel
      icon={HardHat}
      titulo="Obras por riesgo"
      accion={total > 0 ? verLink('Ver todas') : null}
      aria-label="Obras del portafolio ordenadas por riesgo"
    >
      {total === 0 ? (
        <EstadoVacio
          icono={HardHat}
          titulo="Sin obras activas"
          descripcion="Cuando registres una obra, aquí verás su presupuesto contra el gasto real y el semáforo de rentabilidad."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse text-[13px]">
            <thead>
              <tr className="border-b border-border-subtle text-[10px] uppercase tracking-wider text-muted-foreground">
                <th className="px-4 py-2 text-left font-medium">Obra</th>
                <th className="px-3 py-2 text-left font-medium">Cliente</th>
                <th className="px-3 py-2 text-right font-medium">% ppto.</th>
                <th className="px-3 py-2 text-right font-medium">Presupuesto</th>
                <th className="px-3 py-2 text-right font-medium">Gastado</th>
                <th className="px-3 py-2 text-right font-medium">Utilidad</th>
                <th className="px-4 py-2 text-right font-medium">Estado</th>
              </tr>
            </thead>
            <tbody>
              {visibles.map((o) => <FilaObra key={o.obra_id} obra={o} />)}
            </tbody>
          </table>
        </div>
      )}

      {total > MAX_FILAS && (
        <div className="border-t border-border-subtle px-4 py-2 text-right">
          {verLink(`Ver las ${total} obras`)}
        </div>
      )}
    </SeccionPanel>
  )
}

function FilaObra({ obra }) {
  const sem = SEMAFORO[obra.semaforo] || SEMAFORO.verde
  const enPerdida = obra.semaforo === 'rojo'
  const pct = pctConsumido(obra.gasto_total, obra.ingreso_presupuestado)
  const utilidad = n(obra.utilidad_real)
  const cliente = obra.cliente_nombre ?? `Cliente #${obra.cliente_id}`

  return (
    <tr className={`border-b border-border-subtle last:border-0 ${enPerdida ? 'bg-destructive/[0.06]' : ''}`}>
      <td className="max-w-[220px] px-4 py-2.5">
        <span className="block truncate font-medium text-foreground">{obra.nombre}</span>
      </td>
      <td className="max-w-[160px] px-3 py-2.5">
        <span className="block truncate text-secondary-foreground">{cliente}</span>
      </td>
      <td className="px-3 py-2.5 text-right tabular-nums text-secondary-foreground">
        {pct == null ? <span className="text-muted-foreground">—</span> : (
          <span className={pct > 100 ? 'font-semibold text-destructive' : ''}>{pct}%</span>
        )}
      </td>
      <td className="px-3 py-2.5 text-right tabular-nums text-secondary-foreground">
        {obra.tiene_presupuesto ? cop(n(obra.ingreso_presupuestado)) : <span className="text-muted-foreground">—</span>}
      </td>
      <td className="px-3 py-2.5 text-right tabular-nums text-secondary-foreground">{cop(n(obra.gasto_total))}</td>
      <td className={`px-3 py-2.5 text-right font-semibold tabular-nums ${utilidad < 0 ? 'text-destructive' : 'text-foreground'}`}>
        {cop(utilidad)}
      </td>
      <td className="px-4 py-2.5 text-right">
        <Semaforo tono={sem.tono}>{sem.label}</Semaforo>
      </td>
    </tr>
  )
}
