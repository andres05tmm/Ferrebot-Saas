/*
 * TarjetaOperacion — tarjeta de una máquina EN OPERACIÓN (sesión abierta). Muestra el cronómetro en vivo
 * del tiempo de máquina activa (desde `iniciada_en`), la obra, el operador actual y el tiempo de su tramo,
 * con las acciones Rotar operador / Finalizar. Presentación + el hook de reloj; las mutaciones van en los
 * modales del padre.
 */
import { Truck, Building2, User, RotateCw, Square } from 'lucide-react'
import { Card } from '@/components/ui/card.jsx'
import { Semaforo, BTN_PRIMARY, BTN_OUTLINE } from '../comunes.jsx'
import { useCronometro } from './useCronometro.js'

export default function TarjetaOperacion({ sesion, onRotar, onFinalizar }) {
  const total = useCronometro(sesion.iniciada_en)
  const tramo = useCronometro(sesion.tramo_desde || sesion.iniciada_en)

  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[14px] font-semibold text-foreground">
            <Truck className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <span className="truncate">{sesion.maquina}</span>
          </div>
          <div className="mt-1 flex items-center gap-1 text-[12px] text-muted-foreground">
            <Building2 className="size-3 shrink-0" aria-hidden="true" />
            <span className="truncate">{sesion.obra}</span>
          </div>
        </div>
        <Semaforo tono="azul">En operación</Semaforo>
      </div>

      <div className="rounded-md bg-surface-2 px-3 py-2 text-center">
        <div className="font-mono text-2xl font-semibold tabular-nums text-foreground" aria-live="off">
          {total}
        </div>
        <div className="mt-0.5 text-[11px] text-muted-foreground">tiempo de máquina activa</div>
      </div>

      <div className="flex items-center gap-1.5 text-[12px]">
        <User className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="truncate text-foreground">{sesion.operador || 'Sin operador'}</span>
        <span className="ml-auto font-mono tabular-nums text-muted-foreground">{tramo}</span>
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => onRotar(sesion)}
          className={`${BTN_OUTLINE} h-9 flex-1 cursor-pointer`}
        >
          <RotateCw className="size-4" aria-hidden="true" /> Rotar
        </button>
        <button
          type="button"
          onClick={() => onFinalizar(sesion)}
          className={`${BTN_PRIMARY} h-9 flex-1 cursor-pointer`}
        >
          <Square className="size-4" aria-hidden="true" /> Finalizar
        </button>
      </div>
    </Card>
  )
}
