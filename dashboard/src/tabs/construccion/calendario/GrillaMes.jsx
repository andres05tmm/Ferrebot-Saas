/*
 * GrillaMes — grilla mensual del calendario de obra (clon estructural de historial/VistaMes.jsx).
 *
 * Semana L-M-X-J-V-S-D, navegación de meses y una celda por día. Cada celda pinta una fila de DOTS por
 * tipo de actividad (máx 4 + "+n"), el total de horas de máquina del día (solo desktop) y distingue tres
 * estados: HOY (anillo), día SELECCIONADO (fondo suave) y día SOLO-PLANEADO (borde punteado: hay
 * asignaciones pero aún sin actividad real). Presentación pura: recibe `porFecha` ya agregado del backend.
 *
 * MAPEO DE DOTS (documentado; refleja la leyenda visible bajo la grilla). Token semántico, nunca color
 * hardcodeado — el tema 'obra' aplica solo:
 *   horas_maquina            → bg-primary                 producción de máquina = la marca del tenant
 *   reportes                 → bg-info                    avance de obra (informativo)
 *   asistencias              → bg-success                 personal presente
 *   mantenimientos           → bg-warning                 mantenimiento HECHO (atención)
 *   consumos                 → bg-[hsl(var(--chart-5))]   material (violeta; espeja Semaforo violeta)
 *   hitos                    → bg-[hsl(var(--chart-4))]   hito de obra — DESVIACIÓN de la sugerencia
 *                                                          `bg-destructive`: un hito (inicio/fin) NO es un
 *                                                          error; el rojo se reserva a riesgo/pérdida.
 *   proximos_mantenimientos  → bg-warning/50              mantenimiento futuro (atenuado vs. el hecho)
 * `maquinas_asignadas`/`trabajadores_asignados` NO son dots: alimentan el borde punteado "solo planeado".
 */
import { useMemo } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Card } from '@/components/ui/card.jsx'
import { DIAS_SEMANA, MESES, abreviarHoras } from './util.js'

const TIPOS_DOT = [
  { clave: 'horas_maquina', label: 'Horas máquina', punto: 'bg-primary' },
  { clave: 'reportes', label: 'Reportes', punto: 'bg-info' },
  { clave: 'asistencias', label: 'Asistencia', punto: 'bg-success' },
  { clave: 'mantenimientos', label: 'Mantenimiento', punto: 'bg-warning' },
  { clave: 'consumos', label: 'Consumos', punto: 'bg-[hsl(var(--chart-5))]' },
  { clave: 'hitos', label: 'Hitos', punto: 'bg-[hsl(var(--chart-4))]' },
  { clave: 'proximos_mantenimientos', label: 'Próx. mant.', punto: 'bg-warning/50' },
]

function tieneActividad(c = {}) {
  return TIPOS_DOT.some((t) => Number(c[t.clave]) > 0)
}
// Día "solo planeado": sin actividad real pero con máquina/trabajador asignado (planeación a futuro).
function soloPlaneado(c = {}) {
  return !tieneActividad(c) && (Number(c.maquinas_asignadas) > 0 || Number(c.trabajadores_asignados) > 0)
}

function tituloCelda(fecha, c, planeado) {
  const partes = TIPOS_DOT.filter((t) => Number(c[t.clave]) > 0).map((t) => `${c[t.clave]} ${t.label.toLowerCase()}`)
  if (partes.length) return `${fecha}: ${partes.join(' · ')}`
  if (planeado) return `${fecha}: solo planeado`
  return `${fecha}: sin actividad`
}

export default function GrillaMes({ anio, mes, porFecha, hoy, seleccionada, onSeleccionar, onMover, cargando }) {
  const celdas = useMemo(() => {
    const primero = new Date(Date.UTC(anio, mes - 1, 1))
    const offset = (primero.getUTCDay() + 6) % 7
    const nDias = new Date(Date.UTC(anio, mes, 0)).getUTCDate()
    const out = Array.from({ length: offset }, () => null)
    for (let d = 1; d <= nDias; d++) {
      const fecha = `${anio}-${String(mes).padStart(2, '0')}-${String(d).padStart(2, '0')}`
      out.push({ dia: d, fecha, datos: porFecha[fecha] })
    }
    return out
  }, [anio, mes, porFecha])

  const totalHoras = useMemo(
    () => Object.values(porFecha).reduce((a, d) => a + Number(d.horas_maquina_total || 0), 0),
    [porFecha],
  )

  return (
    <Card className="p-3.5">
      <div className="mb-3 flex items-center justify-between">
        <button onClick={() => onMover(-1)} aria-label="Mes anterior"
          className="grid size-8 place-items-center rounded-md border border-border hover:bg-surface-2">
          <ChevronLeft className="size-4" />
        </button>
        <div className="text-center">
          <h2 className="text-sm font-semibold">{MESES[mes - 1]} {anio}</h2>
          {totalHoras > 0 && (
            <p className="hidden text-caption text-muted-foreground sm:block">{abreviarHoras(totalHoras)} de máquina este mes</p>
          )}
        </div>
        <button onClick={() => onMover(1)} aria-label="Mes siguiente"
          className="grid size-8 place-items-center rounded-md border border-border hover:bg-surface-2">
          <ChevronRight className="size-4" />
        </button>
      </div>

      <div className={`grid grid-cols-7 gap-1 text-center ${cargando ? 'opacity-60' : ''}`} aria-busy={cargando || undefined}>
        {DIAS_SEMANA.map((d) => <div key={d} className="py-1 text-caption text-muted-foreground">{d}</div>)}
        {celdas.map((c, i) => c === null
          ? <div key={`v-${i}`} />
          : <Celda key={c.fecha} celda={c} esHoy={c.fecha === hoy}
              seleccionada={c.fecha === seleccionada} onSeleccionar={onSeleccionar} />)}
      </div>

      <Leyenda />
    </Card>
  )
}

function Celda({ celda, esHoy, seleccionada, onSeleccionar }) {
  const conteos = celda.datos?.conteos || {}
  const activos = TIPOS_DOT.filter((t) => Number(conteos[t.clave]) > 0)
  const planeado = soloPlaneado(conteos)
  const horas = abreviarHoras(celda.datos?.horas_maquina_total)
  const cls = [
    'relative flex min-h-11 flex-col items-center gap-1 rounded-md px-1 py-1 transition-colors duration-fast',
    seleccionada ? 'bg-primary/10' : 'hover:bg-surface-2',
    esHoy ? 'ring-2 ring-primary' : '',
    planeado ? 'border border-dashed border-border' : '',
  ].join(' ')
  const titulo = tituloCelda(celda.fecha, conteos, planeado)

  return (
    <button type="button" onClick={() => onSeleccionar(celda.fecha)} title={titulo}
      aria-label={titulo} aria-pressed={seleccionada} className={cls}>
      <span className="text-[11px] font-medium tabular">{celda.dia}</span>
      <span className="flex min-h-[6px] items-center gap-0.5">
        {activos.slice(0, 4).map((t) => <span key={t.clave} className={`size-1.5 rounded-full ${t.punto}`} aria-hidden="true" />)}
        {activos.length > 4 && <span className="text-[9px] leading-none text-muted-foreground">+{activos.length - 4}</span>}
      </span>
      {horas && <span className="hidden text-[10px] leading-none text-muted-foreground sm:block">{horas}</span>}
    </button>
  )
}

function Leyenda() {
  return (
    <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 border-t border-border-subtle pt-2.5 text-[10px] text-muted-foreground">
      {TIPOS_DOT.map((t) => (
        <span key={t.clave} className="inline-flex items-center gap-1">
          <span className={`size-1.5 rounded-full ${t.punto}`} aria-hidden="true" /> {t.label}
        </span>
      ))}
      <span className="inline-flex items-center gap-1">
        <span className="size-2.5 rounded-sm border border-dashed border-border" aria-hidden="true" /> Solo planeado
      </span>
    </div>
  )
}
