/*
 * EstadoActual — franja "Estado actual" sobre la grilla del calendario de obra.
 *
 * Responde de un vistazo las preguntas del cliente PIM: cuántas horas lleva en uso cada máquina este mes,
 * DÓNDE está cada máquina (en qué obra) y con qué operador, y DÓNDE está cada trabajador y con qué máquina.
 * Es la foto de AHORA (asignaciones vigentes hoy), separada del detalle de un día concreto.
 *
 * Datos: GET /obras/calendario/estado (contrato pactado; SIN dinero). Colapsable (<details open>). Respeta
 * la VISTA activa: 'maquinas' → solo máquinas; 'trabajadores' → solo trabajadores; 'todos'/'obras' → ambos.
 * Live: refetch ante los mismos eventos SSE que mueven la obra (EVENTOS_CALENDARIO).
 */
import { Radar, Truck, Users, MapPin } from 'lucide-react'
import { Card } from '@/components/ui/card.jsx'
import { useFetch } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Semaforo, EstadoVacio, Esqueleto } from '../comunes.jsx'
import { EVENTOS_CALENDARIO, h, fechaDiaMes } from './util.js'

const arr = (x) => (Array.isArray(x) ? x : [])

export default function EstadoActual({ vista = 'todos', refreshKey }) {
  const q = useFetch('/obras/calendario/estado', [refreshKey])
  useRealtimeEvent(EVENTOS_CALENDARIO, q.refetch)

  const maquinas = arr(q.data?.maquinas)
  const trabajadores = arr(q.data?.trabajadores)
  const verMaquinas = vista === 'todos' || vista === 'obras' || vista === 'maquinas'
  const verTrabajadores = vista === 'todos' || vista === 'obras' || vista === 'trabajadores'
  const vacio = (verMaquinas ? maquinas.length : 0) + (verTrabajadores ? trabajadores.length : 0) === 0

  return (
    <Card className="p-0 overflow-hidden">
      <details open>
        <summary className="flex cursor-pointer list-none items-center gap-2 border-b border-border-subtle px-3.5 py-2.5 text-[13px] font-semibold text-foreground">
          <Radar className="size-4 text-primary" aria-hidden="true" />
          <span>Estado actual</span>
          <span className="ml-auto text-[11px] font-normal text-muted-foreground">quién está dónde, ahora</span>
        </summary>

        {q.loading ? (
          <Esqueleto filas={3} />
        ) : vacio ? (
          <EstadoVacio icono={Radar} titulo="Nada en obra por ahora"
            descripcion="No hay máquinas ni trabajadores con asignación vigente hoy. Asigna desde el detalle de un día." />
        ) : (
          <div className="space-y-3 p-3">
            {verMaquinas && <BloqueMaquinas maquinas={maquinas} />}
            {verTrabajadores && <BloqueTrabajadores trabajadores={trabajadores} />}
          </div>
        )}
      </details>
    </Card>
  )
}

// ── Bloque genérico: título con icono + conteo, o un vacío discreto si la lista viene sin nada ──────
function Bloque({ icono: Icono, titulo, conteo, children }) {
  return (
    <section className="space-y-1.5">
      <h3 className="flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <Icono className="size-3.5" aria-hidden="true" />
        <span>{titulo}</span>
        <span className="ml-auto tabular font-normal normal-case">{conteo}</span>
      </h3>
      {children}
    </section>
  )
}

function Fila({ children }) {
  return <div className="rounded-md bg-surface-2/50 px-2.5 py-1.5 text-[12px] text-secondary-foreground">{children}</div>
}

function BloqueMaquinas({ maquinas }) {
  return (
    <Bloque icono={Truck} titulo="Máquinas" conteo={maquinas.length}>
      {maquinas.length === 0
        ? <Fila><span className="text-muted-foreground">Sin máquinas registradas.</span></Fila>
        : maquinas.map((m) => {
          const ocupada = m.estado === 'OCUPADA'
          return (
            <Fila key={m.maquina_id}>
              <div className="flex items-center gap-2">
                <span className="font-medium text-foreground">{m.maquina || `Máquina #${m.maquina_id}`}</span>
                {ocupada
                  ? <Semaforo tono="azul" className="ml-auto">En obra</Semaforo>
                  : <Semaforo tono="gris" className="ml-auto">Disponible</Semaforo>}
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
                {ocupada ? (
                  <>
                    <span className="inline-flex items-center gap-1 text-foreground">
                      <MapPin className="size-3 text-muted-foreground" aria-hidden="true" />{m.obra || 'obra sin nombre'}
                    </span>
                    {m.operador && <span>· operador {m.operador}</span>}
                    {m.desde && <span>· desde el {fechaDiaMes(m.desde)}</span>}
                  </>
                ) : (
                  <span>disponible (sin obra)</span>
                )}
                <span>· {h(m.horas_mes)} este mes</span>
              </div>
            </Fila>
          )
        })}
    </Bloque>
  )
}

function BloqueTrabajadores({ trabajadores }) {
  return (
    <Bloque icono={Users} titulo="Trabajadores" conteo={trabajadores.length}>
      {trabajadores.length === 0
        ? <Fila><span className="text-muted-foreground">Nadie en obra por ahora.</span></Fila>
        : trabajadores.map((t) => (
          <Fila key={t.trabajador_id}>
            <div className="flex flex-wrap items-baseline gap-x-1.5">
              <span className="font-medium text-foreground">{t.trabajador || `Trabajador #${t.trabajador_id}`}</span>
              <span className="inline-flex items-center gap-1 text-muted-foreground">
                <MapPin className="size-3" aria-hidden="true" />{t.obra || 'sin obra asignada'}
              </span>
            </div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">
              {t.maquina ? `con ${t.maquina}` : 'sin máquina'}
              {t.desde && ` · desde el ${fechaDiaMes(t.desde)}`}
            </div>
          </Fila>
        ))}
    </Bloque>
  )
}
