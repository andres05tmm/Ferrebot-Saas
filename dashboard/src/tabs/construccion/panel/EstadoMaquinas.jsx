/*
 * EstadoMaquinas — el tablero de la flota: cuántas máquinas hay en cada estado (chips con `Semaforo`) y,
 * debajo, las que están OCUPADAS HOY con su obra, operador, horas y el ingreso que produjeron en el día.
 * Si una máquina ocupada no tiene parte de horas cargado hoy, se marca "— sin parte hoy": una señal útil
 * para el dueño (máquina en obra que quizá no está facturando). Solo presentación.
 */
import { Truck, Building2, User } from 'lucide-react'
import { cop, num } from '@/components/shared.jsx'
import { Semaforo } from '../comunes.jsx'
import { SeccionPanel, n } from './piezas.jsx'

// Estado de máquina → tono del punto + etiqueta. Ámbar aquí es 'warning' (mantenimiento), no la marca.
const ESTADO = {
  DISPONIBLE:    { tono: 'verde', label: 'Disponible' },
  OCUPADA:       { tono: 'azul',  label: 'En obra' },
  MANTENIMIENTO: { tono: 'ambar', label: 'Mantenimiento' },
  DAÑADA:        { tono: 'rojo',  label: 'Dañada' },
  BAJA:          { tono: 'gris',  label: 'De baja' },
}
const ORDEN = ['OCUPADA', 'DISPONIBLE', 'MANTENIMIENTO', 'DAÑADA', 'BAJA']

export default function EstadoMaquinas({ maquinas }) {
  if (!maquinas) return null
  const porEstado = maquinas.por_estado || {}
  const ocupadas = Array.isArray(maquinas.ocupadas_hoy) ? maquinas.ocupadas_hoy : []
  const total = n(maquinas.total)

  return (
    <SeccionPanel
      icon={Truck}
      titulo="Máquinas"
      accion={<span className="text-[11px] tabular-nums text-muted-foreground">{total} en flota</span>}
      aria-label="Estado de la maquinaria"
    >
      <div className="flex flex-wrap gap-1.5 px-4 py-3">
        {ORDEN.filter((e) => porEstado[e]).map((e) => (
          <Semaforo key={e} tono={ESTADO[e].tono}>
            {ESTADO[e].label} <span className="tabular-nums opacity-70">· {porEstado[e]}</span>
          </Semaforo>
        ))}
        {ORDEN.every((e) => !porEstado[e]) && (
          <span className="text-[12px] text-muted-foreground">Sin máquinas registradas.</span>
        )}
      </div>

      <div className="border-t border-border-subtle">
        <div className="px-4 pt-2.5 pb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
          Ocupadas hoy {ocupadas.length > 0 && <span className="tabular-nums">· {ocupadas.length}</span>}
        </div>
        {ocupadas.length === 0 ? (
          <p className="px-4 pb-3.5 text-[12px] text-muted-foreground">Ninguna máquina en obra hoy.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {ocupadas.map((m) => <FilaOcupada key={m.maquina_id} maquina={m} />)}
          </ul>
        )}
      </div>
    </SeccionPanel>
  )
}

function FilaOcupada({ maquina }) {
  const horas = n(maquina.horas_hoy)
  const conParte = horas > 0

  return (
    <li className="flex items-center gap-3 px-4 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium text-foreground">{maquina.maquina}</div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
          <span className="inline-flex items-center gap-1 truncate">
            <Building2 className="size-3 shrink-0" aria-hidden="true" />
            {maquina.obra_nombre || 'Sin obra'}
          </span>
          {maquina.operador_nombre && (
            <span className="inline-flex items-center gap-1 truncate">
              <User className="size-3 shrink-0" aria-hidden="true" />
              {maquina.operador_nombre}
            </span>
          )}
        </div>
      </div>
      <div className="shrink-0 text-right">
        {conParte ? (
          <>
            <div className="text-[13px] font-semibold tabular-nums text-foreground">{cop(n(maquina.ingreso_hoy))}</div>
            <div className="text-[11px] tabular-nums text-muted-foreground">{num(maquina.horas_hoy)} h hoy</div>
          </>
        ) : (
          <div className="text-[11px] font-medium text-warning">— sin parte hoy</div>
        )}
      </div>
    </li>
  )
}
