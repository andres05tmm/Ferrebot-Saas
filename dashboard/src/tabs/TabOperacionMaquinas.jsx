/*
 * TabOperacionMaquinas (/operacion) — tablero de operación de máquina EN VIVO del vertical construcción.
 * Arriba, las máquinas EN OPERACIÓN como tarjetas con cronómetro (rotar/finalizar); abajo, las máquinas
 * asignadas listas para ACTIVAR. Un GET /operacion/tablero + GET /maquinas alimentan la vista; se refresca
 * en vivo por SSE (activar/rotar/finalizar de cualquier dispositivo). Gate por la feature `maquinaria`.
 *
 * La captura en vivo es solo eso: al finalizar se materializa en el parte de horas diario de siempre
 * (mínimo facturable + cartera), con revisión humana de las horas (ModalFinalizar).
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { Timer, Truck, PlayCircle } from 'lucide-react'
import { useFetch, ErrorMsg } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth'
import { Card } from '@/components/ui/card.jsx'
import { Semaforo, EstadoVacio, Esqueleto, BTN_PRIMARY } from './construccion/comunes.jsx'
import { estadoMaquina } from './construccion/estadoMaquina.js'
import { postOperacion } from './construccion/operacion/net.js'
import TarjetaOperacion from './construccion/operacion/TarjetaOperacion.jsx'
import ModalActivar from './construccion/operacion/ModalActivar.jsx'
import ModalRotar from './construccion/operacion/ModalRotar.jsx'
import ModalFinalizar from './construccion/operacion/ModalFinalizar.jsx'

// Eventos que mueven el tablero (activar/rotar/finalizar + cambios de estado de la máquina).
const EVENTOS = [
  'reconnected', 'sesion_maquina_iniciada', 'tramo_operador_rotado', 'sesion_maquina_finalizada',
  'maquina_actualizada',
]
// Estados desde los que tiene sentido activar (una máquina de baja/dañada/en mantenimiento no).
const ACTIVABLE = new Set(['DISPONIBLE', 'OCUPADA'])
const labelMaq = (m) => (m.codigo ? `${m.codigo} · ${m.nombre}` : m.nombre)

export default function TabOperacionMaquinas() {
  const { refreshKey } = useOutletContext() ?? {}
  const admin = useAuth().isAdmin()
  const tableroQ = useFetch('/operacion/tablero', [refreshKey])
  const maquinasQ = useFetch('/maquinas', [refreshKey])
  const [modal, setModal] = useState(null)   // {tipo:'activar', maquina} | {tipo:'rotar'|'finalizar', sesion}

  const refetch = () => { tableroQ.refetch(); maquinasQ.refetch() }
  useRealtimeEvent(EVENTOS, refetch)

  // Anular (solo admin): descarta una activación por error SIN materializar (Finalizar factura el
  // mínimo pactado aunque las horas se ajusten a 0 — la salida correcta para el error es esta).
  async function anular(sesion) {
    if (!window.confirm(`¿Anular la operación de ${sesion.maquina}? No se registrará ningún parte ni cobro.`)) return
    const r = await postOperacion(`/operacion/${sesion.sesion_id}/anular`)
    if (r.ok) { toast.success('Operación anulada (sin parte ni cobro)'); refetch() }
    else toast.error(r.error)
  }

  const sesiones = Array.isArray(tableroQ.data) ? tableroQ.data : []
  const maquinas = Array.isArray(maquinasQ.data) ? maquinasQ.data : []
  const corriendo = new Set(sesiones.map((s) => s.maquina_id))
  const disponibles = maquinas.filter((m) => !corriendo.has(m.id) && ACTIVABLE.has(m.estado))

  const cerrar = () => setModal(null)
  const alExito = () => { cerrar(); refetch() }
  const cargando = tableroQ.loading && !tableroQ.data

  return (
    <div className="mx-auto w-full max-w-5xl px-3 py-4 sm:px-4">
      <header className="mb-4">
        <h1 className="flex items-center gap-2 text-lg font-semibold text-foreground">
          <Timer className="size-5 text-primary" aria-hidden="true" /> Operación de máquinas
        </h1>
        <p className="mt-0.5 text-[13px] text-muted-foreground">
          Activa una máquina, corre el cronómetro y rota operadores. Al finalizar se registra el parte del día.
        </p>
      </header>

      {tableroQ.error && <ErrorMsg msg={tableroQ.error} />}

      {/* En operación */}
      <section aria-label="Máquinas en operación">
        <h2 className="mb-2 text-[11px] uppercase tracking-wider text-muted-foreground">
          En operación {sesiones.length > 0 && <span className="tabular-nums">· {sesiones.length}</span>}
        </h2>
        {cargando ? (
          <Esqueleto filas={2} />
        ) : sesiones.length === 0 ? (
          <EstadoVacio
            icono={Timer}
            titulo="Ninguna máquina en operación"
            descripcion="Activa una máquina asignada a una obra para arrancar su cronómetro."
          />
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {sesiones.map((s) => (
              <TarjetaOperacion
                key={s.sesion_id}
                sesion={s}
                onRotar={(sesion) => setModal({ tipo: 'rotar', sesion })}
                onFinalizar={(sesion) => setModal({ tipo: 'finalizar', sesion })}
                onAnular={admin ? anular : null}
              />
            ))}
          </div>
        )}
      </section>

      {/* Disponibles para activar */}
      <section aria-label="Máquinas disponibles para activar" className="mt-6">
        <h2 className="mb-2 text-[11px] uppercase tracking-wider text-muted-foreground">
          Disponibles para activar {disponibles.length > 0 && <span className="tabular-nums">· {disponibles.length}</span>}
        </h2>
        {disponibles.length === 0 ? (
          <p className="text-[13px] text-muted-foreground">No hay máquinas libres para activar.</p>
        ) : (
          <Card className="divide-y divide-border-subtle">
            {disponibles.map((m) => {
              const est = estadoMaquina(m.estado)
              return (
                <div key={m.id} className="flex items-center gap-3 px-4 py-2.5">
                  <Truck className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-medium text-foreground">{labelMaq(m)}</div>
                    <div className="mt-0.5"><Semaforo tono={est.tono}>{est.label}</Semaforo></div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setModal({ tipo: 'activar', maquina: m })}
                    className={`${BTN_PRIMARY} h-9 cursor-pointer`}
                  >
                    <PlayCircle className="size-4" aria-hidden="true" /> Activar
                  </button>
                </div>
              )
            })}
          </Card>
        )}
      </section>

      {modal?.tipo === 'activar' && <ModalActivar maquina={modal.maquina} onCerrar={cerrar} onExito={alExito} />}
      {modal?.tipo === 'rotar' && <ModalRotar sesion={modal.sesion} onCerrar={cerrar} onExito={alExito} />}
      {modal?.tipo === 'finalizar' && <ModalFinalizar sesion={modal.sesion} onCerrar={cerrar} onExito={alExito} />}
    </div>
  )
}
