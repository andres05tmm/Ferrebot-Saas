/*
 * TarjetaOperacion — tarjeta de una máquina EN OPERACIÓN (sesión abierta). Muestra el cronómetro en vivo
 * del tiempo de máquina activa (desde `iniciada_en`), la obra, el operador actual y el tiempo de su tramo,
 * con las acciones Rotar operador / Finalizar. Presentación + el hook de reloj; las mutaciones van en los
 * modales del padre.
 */
import { Truck, Building2, User, RotateCw, Square } from 'lucide-react'
import { Card } from '@/components/ui/card.jsx'
import { hoyStrCO } from '@/lib/fechas'
import { Semaforo, BTN_PRIMARY, BTN_OUTLINE } from '../comunes.jsx'
import { useCronometro } from './useCronometro.js'

// Día Colombia (YYYY-MM-DD) de un instante ISO — para detectar la sesión que viene de OTRO día.
const diaCO = (iso) => new Date(iso).toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
const diaCorto = (iso) => new Date(iso).toLocaleDateString('es-CO', { day: '2-digit', month: 'short', timeZone: 'America/Bogota' })

export default function TarjetaOperacion({ sesion, onRotar, onFinalizar, onAnular }) {
  const total = useCronometro(sesion.iniciada_en)
  const tramo = useCronometro(sesion.tramo_desde || sesion.iniciada_en)
  // Una sesión abierta desde OTRO día se delata con fecha (F2.6): solo el cronómetro no alcanzaba y
  // se colaban partes fantasma de sesiones olvidadas.
  const deOtroDia = sesion.iniciada_en && diaCO(sesion.iniciada_en) !== hoyStrCO()

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
        {deOtroDia
          ? <Semaforo tono="ambar">Desde el {diaCorto(sesion.iniciada_en)}</Semaforo>
          : <Semaforo tono="azul">En operación</Semaforo>}
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
      {/* Salida para la activación por error (solo admin): Finalizar SIEMPRE factura el mínimo. */}
      {onAnular && (
        <button
          type="button"
          onClick={() => onAnular(sesion)}
          className="self-end text-[11px] font-medium text-muted-foreground cursor-pointer hover:text-destructive"
        >
          Anular sin cobrar
        </button>
      )}
    </Card>
  )
}
