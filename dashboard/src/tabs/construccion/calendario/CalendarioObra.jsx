/*
 * CalendarioObra — página /calendario del vertical construcción (Commit 3, "Calendario de obra PIM").
 *
 * Un calendario mensual donde cada día resume la ACTIVIDAD de la obra: horas de máquina, reportes de
 * campo, asistencia del personal, mantenimientos, consumos de material e hitos; más lo PLANEADO
 * (asignaciones máquina→obra y trabajador→obra que aún no produjeron actividad). Tap en un día abre el
 * detalle. Un conmutador de VISTA (Todos | Obras | Máquinas | Trabajadores) enfoca el calendario en una
 * dimensión y, dentro de esa vista, un select filtra por una entidad concreta.
 *
 * Datos: GET /obras/calendario?anio&mes[+filtros de entidad] para el mes (días con actividad) y
 * GET /obras/calendario/dia?fecha[+filtros] para el detalle (lo pide DetalleDia). SIN cifras de dinero:
 * el contrato no las trae y la página no las inventa. Live: refetch ante los eventos que mueven la obra.
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { CalendarDays } from 'lucide-react'
import { useFetch } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import FiltrosCalendario from './FiltrosCalendario.jsx'
import GrillaMes from './GrillaMes.jsx'
import DetalleDia from './DetalleDia.jsx'
import EstadoActual from './EstadoActual.jsx'
import { hoyCO, hoyStrCO, qsEntidad, MESES, EVENTOS_CALENDARIO } from './util.js'

export default function CalendarioObra() {
  const { refreshKey } = useOutletContext() ?? {}
  const [{ anio, mes }, setPeriodo] = useState(hoyCO())
  const [diaSeleccionado, setDiaSeleccionado] = useState(null)
  const [filtros, setFiltros] = useState({ vista: 'todos', obraId: '', maquinaId: '', trabajadorId: '' })

  // El path lleva los filtros de entidad → cambiarlos refetchea el mes automáticamente (path en deps).
  const mesPath = `/obras/calendario?anio=${anio}&mes=${mes}${qsEntidad(filtros)}`
  const q = useFetch(mesPath, [refreshKey])
  useRealtimeEvent(EVENTOS_CALENDARIO, q.refetch)

  // Selects de entidad: se piden SOLO cuando su vista está activa (path null = en reposo, sin fetch).
  const obrasQ = useFetch(filtros.vista === 'obras' ? '/obras' : null)
  const maquinasQ = useFetch(filtros.vista === 'maquinas' ? '/maquinas' : null)
  const trabajadoresQ = useFetch(filtros.vista === 'trabajadores' ? '/trabajadores' : null)

  // Al cambiar de vista, limpiar los ids de las otras para no arrastrar un filtro oculto.
  function cambiarVista(vista) {
    setFiltros({ vista, obraId: '', maquinaId: '', trabajadorId: '' })
  }
  function cambiarEntidad(campo, valor) {
    setFiltros((prev) => ({ ...prev, [campo]: valor }))
  }
  function mover(delta) {
    setDiaSeleccionado(null) // el detalle abierto pertenece al mes que se abandona
    setPeriodo(({ anio, mes }) => {
      const m = mes + delta
      if (m < 1) return { anio: anio - 1, mes: 12 }
      if (m > 12) return { anio: anio + 1, mes: 1 }
      return { anio, mes: m }
    })
  }

  const dias = Array.isArray(q.data?.dias) ? q.data.dias : []
  const porFecha = Object.fromEntries(dias.map((d) => [d.fecha, d]))

  return (
    <div className="space-y-3">
      <div>
        <h1 className="inline-flex items-center gap-2 text-base font-semibold text-foreground">
          <CalendarDays className="size-5 text-primary" aria-hidden="true" /> Calendario de obra
        </h1>
        <p className="text-[12px] text-muted-foreground">
          {MESES[mes - 1]} {anio} · actividad de obras, máquinas y personal.
        </p>
      </div>

      <FiltrosCalendario
        vista={filtros.vista}
        onVista={cambiarVista}
        filtros={filtros}
        onEntidad={cambiarEntidad}
        obras={Array.isArray(obrasQ.data) ? obrasQ.data : []}
        maquinas={Array.isArray(maquinasQ.data) ? maquinasQ.data : []}
        trabajadores={Array.isArray(trabajadoresQ.data) ? trabajadoresQ.data : []}
      />

      <EstadoActual vista={filtros.vista} refreshKey={refreshKey} />

      <GrillaMes
        anio={anio}
        mes={mes}
        porFecha={porFecha}
        hoy={hoyStrCO()}
        seleccionada={diaSeleccionado}
        onSeleccionar={setDiaSeleccionado}
        onMover={mover}
        cargando={q.loading}
      />

      {diaSeleccionado && (
        <DetalleDia
          fecha={diaSeleccionado} filtros={filtros}
          onCerrar={() => setDiaSeleccionado(null)} onCambio={q.refetch}
        />
      )}
    </div>
  )
}
